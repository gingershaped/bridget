from datetime import datetime
from typing import Optional
from logging import getLogger
from asyncio import Queue
from dataclasses import dataclass
from aiohttp import ClientSession

from discord import Client, Guild, Intents, Member, Message, RawMessageDeleteEvent, RawMessageUpdateEvent, TextChannel, User, Webhook
from odmantic import AIOEngine
from sechat import Room
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

class DiscordToSEForwarder:
    def __init__(self, room: Room, client: Client, engine: AIOEngine, guild: Guild, channel: TextChannel, roleSymbols: dict[int, str]):
        self.queue: Queue[Action] = Queue()
        self.room = room
        self.engine = engine
        self.client = client
        self.guild = guild
        self.channel = channel
        self.roleSymbols = roleSymbols
        self.converter = Chatifier(guild)

        self.client.event(self.on_message)
        self.client.event(self.on_message_edit)
        self.client.event(self.on_message_delete)

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
            symbols += self.roleSymbols.get(role.id, "")
        if len(symbols):
            symbols = " " + symbols
        return f"[{user.display_name}{symbols}]"

    async def prepareMessage(self, message: Message):
        assert isinstance(message.author, Member)
        content = self.converter.convert(message.content)
        if content.count("\n") == 0:
            for attachment in message.attachments:
                content += f" [{attachment.filename}]({attachment.url})"
        prefix = self.prefix(message.author)
        reply = ""
        if message.reference is not None and message.reference.message_id is not None:
            seMessage = await self.getSEByDiscord(message.reference.message_id)
            if seMessage is not None:
                reply = f":{seMessage.chatIdent}"
            elif content.count("\n") == 0:
                reply = f"[[⤷]({message.reference.jump_url})]"
        if content.startswith("    "):
            content = f"    {prefix}\n{content}"
        elif content.startswith("> "):
            content = f"{reply} > {self.prefix(message.author)}\n{content}"
        else:
            content = f"{reply} {self.prefix(message.author)} {content}"
        return content

    async def on_message(self, message: Message):
        if (
            message.guild != self.guild
            or message.channel != self.channel
            or message.author.discriminator == "0000" # big brain time
            or not isinstance(message.author, Member)
        ):
            return
        await self.queue.put(SendMessageAction(
            message, await self.prepareMessage(message)
        ))

    async def on_message_edit(self, before: Message, after: Message):
        if before.content != after.content:
            if (messageInfo := await self.getSEByDiscord(before.id)) is not None:
                await self.queue.put(EditMessageAction(messageInfo, await self.prepareMessage(after)))

    async def on_message_delete(self, message: Message):
        if (messageInfo := await self.getSEByDiscord(message.id)) is not None:
            await self.queue.put(DeleteMessageAction(messageInfo))
        
    async def run(self):
        # magic queue fuckery to ensure messages are sent in order
        while True:
            item = await self.queue.get()
            if isinstance(item, SendMessageAction):
                messageId = await self.room.send(item.message)
                if messageId is not None:
                    await self.engine.save(BridgedMessage(
                        chatIdent=messageId,
                        discordIdent=item.discordMessage.id,
                        chatUser=self.room.userID,
                        discordUser=item.discordMessage.author.id,
                        recievedAt=datetime.now()
                    ))
                else:
                    await item.discordMessage.add_reaction("❌")
            elif isinstance(item, EditMessageAction):
                if not self.canModify(item.messageInfo.recievedAt):
                    await self.room.reply(item.messageInfo.chatIdent, "Message was edited:")
                    item.messageInfo.chatIdent = await self.room.send(item.message)
                    await self.engine.save(item.messageInfo)
                else:
                    await self.room.edit(item.messageInfo.chatIdent, item.message)
            elif isinstance(item, DeleteMessageAction):
                if not self.canModify(item.messageInfo.recievedAt):
                    await self.room.reply(item.messageInfo.chatIdent, "Message was deleted.")
                else:
                    await self.room.delete(item.messageInfo.chatIdent)
                await self.engine.delete(item.messageInfo)