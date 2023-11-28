from typing import TYPE_CHECKING
from datetime import datetime
from logging import getLogger
from asyncio import Queue, Lock, TaskGroup, get_event_loop, sleep
from dataclasses import dataclass
from aiohttp import ClientSession

import json

from discord import Guild, Member, Message, TextChannel
from discord.utils import find
from odmantic import AIOEngine
from sechat import Room
if TYPE_CHECKING:
    from bridget import BridgetClient
from bridget.chatifier import Chatifier
from bridget.models import BridgedMessage

@dataclass
class Action:
    pass

# I do not like these dataclasses
@dataclass
class SendMessageAction(Action):
    discordMessage: Message
    message: str

@dataclass
class EditMessageAction(Action):
    messageInfo: BridgedMessage
    message: str

@dataclass
class DeleteMessageAction(Action):
    messageInfo: BridgedMessage

class WhosTyping:
    def __init__(self, client: "BridgetClient", roomId: int, channelId: int):
        self.logger = getLogger("WhosTyping").getChild(str(roomId))
        self.client = client
        self.roomId = roomId
        self.channelId = channelId
        self.lock = Lock()
        self.typing: dict[str, datetime] = {}

        self.client.signals.signal("typing").connect(self.on_typing)
        self.client.signals.signal("message").connect(self.on_message)

    async def on_typing(self, sender, channel: TextChannel, user: Member, started: datetime):
        if not channel.id == self.channelId:
            return
        self.logger.info(f"Got typing notif for {user}")
        async with self.lock:
            self.typing[user.display_name] = datetime.now()

    async def on_message(self, sender, message: Message):
        if (
            message.channel.id != self.channelId
            or message.author.discriminator == "0000" # big brain time
            or not isinstance(message.author, Member)
            or message.author.display_name not in self.typing
        ):
            return
        async with self.lock:
            self.typing.pop(message.author.display_name)

    async def run(self):
        async with ClientSession() as session:
            async with session.ws_connect("wss://rydwolf.xyz/whos_typing") as socket:
                await socket.send_str(f"bridge\n{self.roomId}")
                self.logger.info("Connected")
                while True:
                    async with self.lock:
                        now = datetime.now()
                        msg = "\n".join(
                            json.dumps(user) for user, time in self.typing.items() if (now - time).seconds <= 10
                        )
                        if len(msg):
                            await socket.send_str("\n" + msg)
                    await sleep(0.5)

class DiscordToSEForwarder:
    def __init__(self, room: Room, client: "BridgetClient", engine: AIOEngine, guild: Guild, channel: TextChannel, roleSymbols: dict[str, str], ignore: list[int]):
        self.sendQueue: Queue[SendMessageAction] = Queue()
        self.editQueue: Queue[EditMessageAction] = Queue()
        self.deleteQueue: Queue[DeleteMessageAction] = Queue()
        self.room = room
        self.engine = engine
        self.client = client
        self.guild = guild
        self.channel = channel
        self.roleSymbols = roleSymbols
        self.ignore = ignore
        self.converter = Chatifier(guild)
        self.typing = WhosTyping(client, self.room.roomID, self.channel.id)

        self.client.signals.signal("message").connect(self.on_message)
        self.client.signals.signal("message_edit").connect(self.on_message_edit)
        self.client.signals.signal("message_delete").connect(self.on_message_delete)

    def canModify(self, dt: datetime):
        # we use 2 minutes instead of 2.5 because any number of things may intervene
        # and cause us to go over if we use 2.5
        # which means an error, and I don't want to risk the whole thing imploding
        return (datetime.now() - dt).seconds < (60 * 2)

    async def getSEByDiscord(self, ident: int):
        if (message := await self.engine.find_one(BridgedMessage, BridgedMessage.discordIdent == ident)) is not None:
            return message

    def prefix(self, user: Member):
        # TODO: this is awful
        symbols = ""
        if user.bot:
            symbols += "⚙"
        for role in user.roles:
            symbols += self.roleSymbols.get(str(role.id), "")
        if len(symbols):
            symbols = " " + symbols
        return f"[{user.display_name}{symbols}]"

    async def prepareMessage(self, message: Message, note: str = ""):
        assert isinstance(message.author, Member)
        content = self.converter.convert(message.content)
        if content.count("\n") == 0:
            if len(message.embeds) == 1:
                content += "<embed>"
            elif len(message.embeds) > 1:
                content += f"<{len(message.embeds)} embeds>"
            for attachment in message.attachments:
                content += f" [{attachment.filename}]({attachment.url})"
        prefix = note + self.prefix(message.author)
        reply = ""
        if message.reference is not None and message.reference.message_id is not None:
            seMessage = await self.getSEByDiscord(message.reference.message_id)
            if seMessage is not None:
                reply = f":{seMessage.chatIdent}"
            elif content.count("\n") == 0:
                reply = f"[⤷]({message.reference.jump_url})"
        if content.startswith("    "):
            content = f"    {prefix}\n{content}"
        elif content.startswith("> "):
            content = f"{reply} > {self.prefix(message.author)}\n{content}"
        else:
            content = f"{reply} {self.prefix(message.author)} {content}"
        return content

    async def on_message(self, sender, message: Message):
        if (
            message.guild != self.guild
            or message.channel != self.channel
            or message.author.discriminator == "0000" # big brain time
            or not isinstance(message.author, Member)
            or message.author.id in self.ignore
        ):
            return
        await self.sendQueue.put(SendMessageAction(
            message, await self.prepareMessage(message)
        ))

    async def on_message_edit(self, sender, before: Message, after: Message):
        if before.content != after.content:
            if (messageInfo := await self.getSEByDiscord(before.id)) is not None:
                await self.editQueue.put(EditMessageAction(messageInfo, await self.prepareMessage(after)))

    async def on_message_delete(self, sender, message: Message):
        if (messageInfo := await self.getSEByDiscord(message.id)) is not None:
            await self.deleteQueue.put(DeleteMessageAction(messageInfo))

    async def sendTask(self):
        while True:
            await self.editQueue.join()
            item = await self.sendQueue.get()
            if len(item.message) > 200 and item.message.count("\n") == 0:
                # not today
                await item.discordMessage.add_reaction("📏")
            else:
                sendStarted = datetime.now()
                messageId = await self.room.send(item.message)
                if messageId is not None:
                    await self.engine.save(BridgedMessage(
                        chatIdent=messageId,
                        discordIdent=item.discordMessage.id,
                        chatUser=self.room.userID,
                        discordUser=item.discordMessage.author.id,
                        recievedAt=datetime.now()
                    ))
                    if (datetime.now() - sendStarted).seconds >= 10:
                        notif = await self.channel.send(
                            "Ratelimit is too high! Forwarding from Discord to SE has been paused for two minutes."
                        )
                        await sleep(120)
                        await notif.edit(content="Forwarding resumed.", delete_after=30)
                else:
                    await item.discordMessage.add_reaction("❌")
            self.sendQueue.task_done()
     
    async def editTask(self):
        while True:
            item = await self.editQueue.get()
            message = await self.channel.get_partial_message(item.messageInfo.discordIdent).fetch()
            if len(item.message) > 200:
                await message.add_reaction("📏")
            elif find(lambda r: r.emoji == "📏", message.reactions) is not None:
                assert self.client.user is not None
                await message.remove_reaction("📏", self.client.user)
            if not self.canModify(item.messageInfo.recievedAt):
                await message.reply("Edit ignored due to edit window expiring, sorry!")
            else:
                await self.room.edit(item.messageInfo.chatIdent, item.message)
            self.editQueue.task_done()
    
    async def sendNotification(self, target: int, message: str):
        await self.sendQueue.join()
        await self.room.reply(target, message)

    async def deleteTask(self):
        while True:
            item = await self.deleteQueue.get()
            if not self.canModify(item.messageInfo.recievedAt):
                get_event_loop().create_task(self.sendNotification(item.messageInfo.chatIdent, "Message was deleted."))
            else:
                await self.room.delete(item.messageInfo.chatIdent)
            await self.engine.delete(item.messageInfo)
            self.deleteQueue.task_done()

    async def run(self):
        async with TaskGroup() as group:
            group.create_task(self.sendTask())
            group.create_task(self.editTask())
            group.create_task(self.deleteTask())
            group.create_task(self.typing.run())