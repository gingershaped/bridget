from asyncio import Task, get_event_loop
from datetime import datetime
import html
import json
from logging import getLogger
from typing import Coroutine
from aiohttp import ClientSession
from bs4 import BeautifulSoup, Tag

from discord import Client, Embed, Intents, Interaction, Message, TextChannel, Webhook
from discord.app_commands import CommandTree, Group
from discord.utils import setup_logging, find, MISSING
from motor.motor_asyncio import AsyncIOMotorClient
from odmantic import AIOEngine
from sechat import Bot

from bridget.discord2se import DiscordToSEForwarder
from bridget.discordifier import Discordifier
from bridget.models import BridgedMessage, Configuration, DualBridge
from bridget.se2discord import SEToDiscordForwarder
from bridget.util import ChatPFPFetcher, resolveChatPFP, prettyDelta

class BridgetClient(Client):
    def __init__(self):
        intents = Intents.none()
        intents.guilds = True
        intents.guild_messages = True
        intents.guild_typing = True
        intents.message_content = True
        intents.messages = True
        super().__init__(intents=intents)
        self.logger = getLogger("DiscordClient")

        self.tree = CommandTree(self)
        self.forwarders: dict[int, DiscordToSEForwarder] = {}

        self.tree.command(name="queued", description="Check number of queued messages")(self.queueSizeCommand)
        self.tree.context_menu(name="Message permalink")(self.messageLinkCommand)
        self.tree.context_menu(name="User info")(self.userInfoCommand)
        roomGroup = Group(name="room", description="Commands relating to the bridged room")
        roomGroup.command(name="info", description="Information about the room")(self.roomInfoCommand)
        roomGroup.command(name="users", description="List of users chatting in the room")(self.userListCommand)
        self.tree.add_command(roomGroup)

    async def setup_hook(self):
        await self.tree.sync()

    async def queueSizeCommand(self, interaction: Interaction):
        assert interaction.guild_id is not None
        forwarder = self.forwarders[interaction.guild_id]
        await interaction.response.send_message(
            f"There are {forwarder.queue.qsize()} messages in the queue.",
            ephemeral=True
        )

    async def messageLinkCommand(self, interaction: Interaction, message: Message):
        assert interaction.guild_id is not None
        forwarder = self.forwarders[interaction.guild_id]
        messageInfo = await forwarder.getSEByDiscord(message.id)
        if messageInfo is None:
            await interaction.response.send_message("Invalid message.", ephemeral=True)
        else:
            await interaction.response.send_message(
                f"Message permalink: <https://chat.stackexchange.com/transcript/message/{messageInfo.chatIdent}#{messageInfo.chatIdent}>",
                ephemeral=True
            )

    async def roomInfoCommand(self, interaction: Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        assert interaction.guild_id is not None
        forwarder = self.forwarders[interaction.guild_id]
        async with ClientSession() as session:
            async with session.get(f"https://chat.stackexchange.com/rooms/thumbs/{forwarder.room.roomID}") as response:
                roomInfo = await response.json()
            async with session.get(f"https://chat.stackexchange.com/rooms/{forwarder.room.roomID}") as response:
                soup = BeautifulSoup(await response.content.read(), features="lxml")
                assert isinstance(userDiv := soup.find(class_="js-present"), Tag)
                users = json.loads(html.unescape(userDiv.attrs["data-users"]))

        await interaction.followup.send(embed=Embed(
                title=roomInfo["name"],
                description=roomInfo["description"],
                url=f"https://chat.stackexchange.com/rooms/{forwarder.room.roomID}",
            ).add_field(
                name="In room", value=", ".join(
                    f"[{user['name']}](https://chat.stackexchange.com/user/{user['id']})" for user in users
                )
            ),
        )

    def makeUserEmbed(self, user: dict):
        return Embed(
            title=user["name"],
            description=user.get("user_message", MISSING),
            url=f"https://chat.stackexchange.com/user/{user['id']}",
        ).add_field(
            name="Reputation", value=user["reputation"]
        ).add_field(
            name="Last seen", value=prettyDelta((datetime.now() - datetime.fromtimestamp(user["last_seen"]))), inline=True
        ).add_field(
            name="Last message", value=prettyDelta((datetime.now() - datetime.fromtimestamp(user["last_post"]))), inline=True
        ).set_thumbnail(
            url=resolveChatPFP(user["email_hash"])
        ).set_footer(
            text=user["site"]["caption"],
            icon_url=Discordifier.fixUrl(user["site"]["icon"])
        )

    async def userListCommand(self, interaction: Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        assert interaction.guild_id is not None
        forwarder = self.forwarders[interaction.guild_id]
        async with ClientSession() as session:
            async with session.get(f"https://chat.stackexchange.com/rooms/{forwarder.room.roomID}") as response:
                soup = BeautifulSoup(await response.content.read(), features="lxml")
                assert isinstance(userDiv := soup.find(class_="js-present"), Tag)
                users = json.loads(html.unescape(userDiv.attrs["data-users"]))

            embeds = []
            for partial in users:
                async with session.get(f"https://chat.stackexchange.com/users/thumbs/{partial['id']}") as response:
                    user = await response.json()
                embeds.append(
                    self.makeUserEmbed(user)
                )

        await interaction.followup.send(
            embeds=embeds
        )

    async def userInfoCommand(self, interaction: Interaction, message: Message):
        await interaction.response.defer(ephemeral=True, thinking=True)
        assert interaction.guild_id is not None
        forwarder = self.forwarders[interaction.guild_id]
        if (messageInfo := await forwarder.engine.find_one(BridgedMessage, BridgedMessage.discordIdent == message.id)) is None:
            await interaction.followup.send("Invalid message.")
        else:
            async with ClientSession() as session:
                async with session.get(f"https://chat.stackexchange.com/users/thumbs/{messageInfo.chatUser}") as response:
                    user = await response.json()
            await interaction.followup.send(embed=self.makeUserEmbed(user))

class Bridget:
    def __init__(self, config: Configuration):
        setup_logging()
        self.logger = getLogger("Bridget")
        self.config = config
        self.tasks: set[Task] = set()

    def startTask(self, coro: Coroutine):
        task = get_event_loop().create_task(coro)
        self.tasks.add(task)
        task.add_done_callback(self.tasks.remove)
        return task

    async def run(self):
        self.logger.info("Starting")

        engine = AIOEngine(AsyncIOMotorClient(self.config["database"]["uri"]), self.config["database"]["name"])
        pfpFetcher = ChatPFPFetcher()

        bot = Bot()
        await bot.authenticate(self.config["chat"]["email"], self.config["chat"]["password"], self.config["chat"]["host"])

        client = BridgetClient()
        await client.login(self.config["token"])
        clientTask = self.startTask(client.connect())
        await client.wait_until_ready()

        self.logger.info("Clients ready")
        assert bot.userID is not None
        for single in self.config["single"]:
            hook = Webhook.from_url(single["hook"], client=client)
            forwarder = SEToDiscordForwarder(bot.userID, single["room"], hook, engine, pfpFetcher)
            self.startTask(forwarder.run())

        for dual in self.config["dual"]:
            guild = client.get_guild(dual["guild"])
            assert guild is not None
            channel = guild.get_channel(dual["channel"])
            assert isinstance(channel, TextChannel)
            hook = find(lambda hook: hook.user == client.user, await channel.webhooks())
            if not isinstance(hook, Webhook):
                hook = await channel.create_webhook(name="Bridget", reason="Creating bridge webhook")
            room = await bot.joinRoom(dual["room"])
            se2dc = SEToDiscordForwarder(bot.userID, dual["room"], hook, engine, pfpFetcher)
            dc2se = DiscordToSEForwarder(room, client, engine, guild, channel, dual["roleIcons"], dual["ignore"])
            client.forwarders[guild.id] = dc2se
            self.startTask(se2dc.run())
            self.startTask(dc2se.run())

        try:
            await clientTask
        finally:
            self.logger.info("Shutting down")
            for task in self.tasks:
                task.cancel()
                await task
            await bot.shutdown()
            await client.close()