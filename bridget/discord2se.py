import json
from typing import TYPE_CHECKING
from datetime import datetime
from asyncio import Lock, Queue, TaskGroup, sleep


from aiohttp import ClientSession
from discord import Member, Message, Object, TextChannel
from discord.utils import find
from odmantic import AIOEngine
from sechat import Room

from bridget.chatifier import Chatifier
from bridget.models import BridgedMessage

class DiscordToSEForwarder:
    max_message_length = 200

    def __init__(self, room: Room, engine: AIOEngine, channel: TextChannel, client_id: int, role_symbols: dict[str, str], ignore: list[int]):
        self.room = room
        self.engine = engine
        self.client_id = client_id
        self.channel = channel
        self.role_symbols = role_symbols
        self.ignore = ignore
        self.converter = Chatifier(channel.guild)

        self._send_queue: Queue[Message] = Queue()
        self._edit_queue: Queue[Message] = Queue()
        self._delete_queue: Queue[Message] = Queue()
        self.notification_queue: Queue[tuple[str, int]] = Queue()

        self._typing_lock = Lock()
        self._last_typed_at: dict[int, datetime] = {}
        

    def can_modify(self, dt: datetime):
        # we use 2 minutes instead of 2.5 because any number of things may intervene
        # and cause us to go over if we use 2.5
        # which means an error, and I don't want to risk the whole thing imploding
        return (datetime.now() - dt).seconds < (60 * 2)

    async def get_bridge_record(self, discord_id: int):
        if (message := await self.engine.find_one(BridgedMessage, BridgedMessage.discord_message_id == discord_id)) is not None:
            return message

    def format_display_name(self, user: Member):
        # TODO: this is awful
        symbols = ""
        if user.bot:
            symbols += "⚙"
        for role in user.roles:
            symbols += self.role_symbols.get(str(role.id), "")
        if len(symbols):
            symbols = " " + symbols
        return f"[{user.display_name}{symbols}]"

    async def convert_message(self, message: Message, note: str = ""):
        assert isinstance(message.author, Member)
        content = await self.converter.convert(message.content)
        if content.count("\n") == 0:
            if len(message.embeds) == 1:
                content += " <embed>"
            elif len(message.embeds) > 1:
                content += f" <{len(message.embeds)} embeds>"
            for attachment in message.attachments:
                content += f" [{attachment.filename}]({attachment.url})"
        prefix = note + self.format_display_name(message.author)
        reply = ""
        if message.reference is not None and message.reference.message_id is not None:
            seMessage = await self.get_bridge_record(message.reference.message_id)
            if seMessage is not None:
                reply = f":{seMessage.se_message_id}"
            elif content.count("\n") == 0:
                reply = f"[⤷]({message.reference.jump_url})"
        if content.startswith("    "):
            content = f"    {prefix}\n{content}"
        elif content.startswith("> "):
            content = f"{reply} > {self.format_display_name(message.author)}\n{content}"
        else:
            content = f"{reply} {self.format_display_name(message.author)} {content}"
        return content

    async def queue_message(self, message: Message):
        await self._send_queue.put(message)
        async with self._typing_lock:
            self._last_typed_at.pop(message.author.id, None)

    async def queue_edit(self, message: Message):
        await self._edit_queue.put(message)

    async def queue_delete(self, message: Message):
        await self._delete_queue.put(message)

    async def queue_typing(self, member: Member):
        async with self._typing_lock:
            self._last_typed_at[member.id] = datetime.now()

    async def _delete_task(self):
        while True:
            message = await self._delete_queue.get()
            if (bridge_record := await self.get_bridge_record(message.id)) is not None:
                if self.can_modify(bridge_record.received_at):
                    await self.room.delete(bridge_record.se_message_id)
                else:
                    await self.notification_queue.put(("Message was deleted", bridge_record.se_message_id))
                await self.engine.delete(bridge_record)
            self._delete_queue.task_done()

    async def _edit_task(self):
        while True:
            message = await self._edit_queue.get()
            bridge_record = await self.get_bridge_record(message.id)
            new_content = await self.convert_message(message)
            if len(new_content) > self.max_message_length and new_content.count("\n") == 0:
                await message.add_reaction("📏")
            else:
                if find(lambda r: r.emoji == "📏", message.reactions) is not None:
                    await message.remove_reaction("📏", Object(self.client_id))
                if bridge_record is None:
                    # we've never sent this message, possibly because it was too long
                    await self._send_queue.put(message)
                elif not self.can_modify(bridge_record.received_at):
                    await message.reply("Your edit was ignored because the edit window expired, sorry!")
                else:
                    await self.room.edit(bridge_record.se_message_id, new_content)
            self._edit_queue.task_done()
            
    async def _send_task(self):
        while True:
            # new messages and edits share a ratelimit, so give edits priority
            # since they're time-sensitive
            await self._edit_queue.join()
            message = await self._send_queue.get()
            content = await self.convert_message(message)
            if len(content) > 200 and content.count("\n") == 0:
                await message.add_reaction("📏")
            else:
                se_message_id = await self.room.send(content)
                await self.engine.save(BridgedMessage( # type: ignore
                    se_message_id=se_message_id,
                    discord_message_id=message.id,
                    se_user_id=self.room.user_id,
                    discord_user_id=message.author.id,
                    received_at=datetime.now()
                ))
            self._send_queue.task_done()

    async def _notification_task(self):
        while True:
            # give priority to user messages
            await self._send_queue.join()
            await self.room.send(*(await self.notification_queue.get()))
    
    async def _typing_task(self):
        async with ClientSession() as session, session.ws_connect("wss://rydwolf.xyz/whos_typing") as connection:
            await connection.send_str(f"bridge\n{self.room.room_id}")
            while True:
                async with self._typing_lock:
                    now = datetime.now()
                    names = []
                    for id, last_typed in self._last_typed_at.items():
                        if (now - last_typed).seconds <= 10:
                            if (member := self.channel.guild.get_member(id)) is None:
                                if (member := await self.channel.guild.fetch_member(id)) is None:
                                    raise Exception(id)
                            names.append(json.dumps(member.display_name))
                    if len(names):
                        await connection.send_str("\n" + "\n".join(names))
                    else:
                        await connection.send_str("")
                await sleep(0.5)

    async def _room_ws_task(self):
        # open a webhook connection so we stay in the room list
        async for event in self.room.events():
            pass

    async def run(self):
        try:
            async with TaskGroup() as group:
                group.create_task(self._delete_task(), name=f"delete/{self.room.room_id}")
                group.create_task(self._edit_task(), name=f"edit/{self.room.room_id}")
                group.create_task(self._send_task(), name=f"send/{self.room.room_id}")
                group.create_task(self._notification_task(), name=f"notification/{self.room.room_id}")
                group.create_task(self._typing_task(), name=f"typing/{self.room.room_id}")
                group.create_task(self._room_ws_task(), name=f"room-ws-hack/{self.room.room_id}")
        finally:
            await self.room.close()
