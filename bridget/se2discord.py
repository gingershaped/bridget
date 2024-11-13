from asyncio import sleep
from datetime import datetime
import re

from bs4 import BeautifulSoup, Tag
from discord import (Embed, Forbidden, NotFound, TextChannel, Webhook,
                     WebhookMessage)
from discord.utils import MISSING
from odmantic import AIOEngine
from sechat import Room
from sechat.events import DeleteEvent, EditEvent, MessageEvent

from bridget.discordifier import Discordifier
from bridget.models import BridgedMessage
from bridget.util import ChatPFPFetcher


class SEToDiscordForwarder:
    pfp_fetcher = ChatPFPFetcher()
    converter = Discordifier()
    
    def __init__(self, room_id: int, ignored: list[int], suppress_embeds_for: list[int], webhook: Webhook, engine: AIOEngine):
        self.ignored = ignored
        self.room_id = room_id
        self.suppress_embeds_for = suppress_embeds_for
        self.webhook = webhook
        self.engine = engine

    async def fetch_corresponding_message(self, se_message_id: int):
        if (message := await self.engine.find_one(BridgedMessage, BridgedMessage.se_message_id == se_message_id)) is not None:
            try:
                assert self.webhook.user is not None
                if message.discord_user_id == self.webhook.user.id:
                    return await self.webhook.fetch_message(message.discord_message_id)
                else:
                    assert isinstance(self.webhook.channel, TextChannel)
                    return await self.webhook.channel.fetch_message(message.discord_message_id)
            except (NotFound, Forbidden) as e:
                return None

    async def create_reply_embed(self, messageId: int):
        async with self.webhook.session.get(f"https://chat.stackexchange.com/message/{messageId}?raw=true") as response:
            return Embed(
                title=f"Reply to #{messageId}",
                url=f"https://chat.stackexchange.com/transcript/message/{messageId}#{messageId}",
                description=(await response.text()).splitlines()[0]
            )

    async def handle_message(self, event: MessageEvent):
        assert self.webhook.user is not None
        if event.user_id in self.ignored:
            return
        if event.content.startswith("\u200d"):
            return
        if event.user_name in ("everyone", "here"):
            return
        converted = self.converter.convert(BeautifulSoup(event.content, features="lxml").body) # type: ignore
        if converted is None:
            return
        pfp = await self.pfp_fetcher.fetch_pfp_url(event.user_id)
        # this isn't great
        if isinstance(converted, Embed):
            embeds = [converted]
            content = ""
        else:
            embeds = []
            content = converted
        view = MISSING
        if event.parent_id is not None and event.show_parent:
            content = re.sub(r"^@\S+", "", content)
            if (replied_message := await self.fetch_corresponding_message(event.parent_id)) is not None:
                prefix = f"[⤷]({replied_message.jump_url}) "
                if replied_message.author.id == self.webhook.id:
                    prefix += f"{replied_message.author.mention} "
                content = prefix + content
            else:
                embeds.append(await self.create_reply_embed(event.parent_id))
        if isinstance(event, EditEvent):
            message = await self.fetch_corresponding_message(event.message_id)
            if isinstance(message, WebhookMessage):
                await message.edit(
                    content=content,
                    embeds=embeds,
                    view=view
                )
        else:
            message = await self.webhook.send(
                content=content,
                username=event.user_name,
                avatar_url=pfp,
                embeds=embeds,
                suppress_embeds=event.user_id in self.suppress_embeds_for and not len(embeds),
                view=view,
                wait=True,
            )
            await self.engine.save(BridgedMessage( # type: ignore
                se_message_id=event.message_id,
                discord_message_id=message.id,
                se_user_id=event.user_id,
                discord_user_id=self.webhook.user.id,
                received_at=datetime.now()
            ))

    async def handle_delete(self, event: DeleteEvent):
        if event.user_id in self.ignored:
            return
        if isinstance(message := await self.fetch_corresponding_message(event.message_id), WebhookMessage):
            await message.delete()

    async def run(self):
        async for event in Room.anonymous(self.room_id):
            if isinstance(event, MessageEvent):
                await self.handle_message(event)
            elif isinstance(event, DeleteEvent):
                await self.handle_delete(event)