from datetime import datetime
from asyncio import sleep

from bs4 import BeautifulSoup, Tag
from discord import Embed, Forbidden, NotFound, TextChannel, Webhook
from discord.utils import MISSING
from sechat import EditEvent, MessageEvent, DeleteEvent
from odmantic import AIOEngine

from bridget.discordifier import Discordifier
from bridget.models import BridgedMessage
from bridget.util import ChatPFPFetcher

class SEToDiscordForwarder:
    def __init__(self, userId: int, room: int, noembed: list[int], hook: Webhook, engine: AIOEngine, pfpFetcher: ChatPFPFetcher):
        self.lastT = 0
        self.userId = userId
        self.room = room
        self.noembed = noembed
        self.hook = hook
        self.engine = engine
        self.converter = Discordifier()
        self.pfpFetcher = pfpFetcher

    async def getDiscordBySE(self, ident: int):
        if (message := await self.engine.find_one(BridgedMessage, BridgedMessage.chatIdent == ident)) is not None:
            try:
                assert self.hook.user != None
                if message.discordUser == self.hook.user.id:
                    return await self.hook.fetch_message(message.discordIdent)
                else:
                    assert isinstance(self.hook.channel, TextChannel)
                    return await self.hook.channel.fetch_message(message.discordIdent)
            except (NotFound, Forbidden):
                return None

    async def getReplyEmbed(self, messageId: int):
        # not a great way to do this, but views only work in hook messages if a bot made the hook
        # they silently fail otherwise
        # this is documented nowhere in the discord.py or Discord docs :)
        # at least it's only a fallback...
        async with self.hook.session.get(f"https://chat.stackexchange.com/message/{messageId}?raw=true") as response:
            return Embed(
                title=f"Reply to #{messageId}",
                url=f"https://chat.stackexchange.com/transcript/message/{messageId}#{messageId}",
                description=(await response.text()).splitlines()[0]
            )

    async def onMessage(self, event: MessageEvent):
        if event.user_id == self.userId:
            return
        if event.content.startswith("\u200d"):
            return
        converted = self.converter.convert(BeautifulSoup(event.content, features="lxml"))
        if converted is None:
            return
        pfp = await self.pfpFetcher.getPFP(event.user_id)
        # this isn't great
        if isinstance(converted, Embed):
            embeds = [converted]
            content = MISSING
        else:
            embeds = []
            content = converted
        view = MISSING
        if event.parent_id is not None and event.show_parent:
            repliedMessageInfo = await self.getDiscordBySE(event.parent_id)
            if repliedMessageInfo is None:
                embeds.append(await self.getReplyEmbed(event.parent_id))
            else:
                if content == MISSING:
                    content = f"[⤷]({repliedMessageInfo.jump_url})"
                else:
                    content = f"[⤷]({repliedMessageInfo.jump_url}) " + content
        # polymorphism abuse
        if isinstance(event, EditEvent):
            messageInfo = await self.getDiscordBySE(event.message_id)
            if messageInfo is not None:
                await messageInfo.edit(
                    content=content,
                    embeds=embeds,
                    view=view
                )
        else:
            if event.user_id in self.noembed and not len(embeds):
                noembeds = True
            else:
                noembeds = False
            message = await self.hook.send(
                content=content,
                username=event.user_name,
                avatar_url=pfp,
                embeds=embeds,
                suppress_embeds=noembeds,
                view=view,
                wait=True,
            )
            await self.engine.save(BridgedMessage(
                chatIdent=event.message_id,
                discordIdent=message.author.id,
                chatUser=event.user_id,
                discordUser=message.author.id,
                recievedAt=datetime.now()
            ))

    async def onDelete(self, event: DeleteEvent):
        if event.user_id == self.userId:
            return
        messageInfo = await self.getDiscordBySE(event.message_id)
        if messageInfo is not None:
            await messageInfo.delete()

    async def getFkey(self):
        # that yoinky sploinky
        # fuck fkeys, this took me two days to figure out
        async with self.hook.session.get(f"https://chat.stackexchange.com/rooms/{self.room}") as page:
            soup = BeautifulSoup(await page.read(), features="lxml")
            assert isinstance(frick := soup.find("input", id="fkey"), Tag)
            return frick.attrs["value"]

    async def getT(self):
        async with self.hook.session.post(f"https://chat.stackexchange.com/chats/{self.room}/events", data={
                "since": 0,
                "mode": "Messages",
                "msgCount": 100,
                "fkey": self.fkey
            }) as response:
                return (await response.json())["time"]

    async def events(self):
        response = await self.hook.session.post(f"https://chat.stackexchange.com/events", data={
            f"r{self.room}": self.lastT,
            "fkey": self.fkey
        })
        data = (await response.json()).get(f"r{self.room}")
        if data is None:
            return
        if "t" in data:
            self.lastT = data["t"]
        if "e" in data:
            for event in data["e"]:
                match event["event_type"]:
                    case 1: # message
                        yield MessageEvent(**event)
                    case 2: # edit
                        yield EditEvent(**event)
                    case 10: # delete
                        yield DeleteEvent(**event)

    async def run(self):
        self.fkey = await self.getFkey()
        self.lastT = await self.getT()
        while True:
            async for event in self.events():
                if isinstance(event, MessageEvent):
                    await self.onMessage(event)
            # this is probably fine...
            await sleep(2)