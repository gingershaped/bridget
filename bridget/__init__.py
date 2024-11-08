from asyncio import TaskGroup
from datetime import datetime
import html
import json
from logging import getLogger

from aiohttp import ClientSession
from bs4 import BeautifulSoup, Tag
from discord import AllowedMentions, Client, Color, Embed, Intents, Interaction, Member, Message, TextChannel, User, Webhook
from discord.abc import Messageable
from discord.app_commands import CommandTree, Command, ContextMenu, Group
from discord.utils import find, MISSING
from odmantic import AIOEngine
from sechat import Credentials, Room
from motor.motor_asyncio import AsyncIOMotorClient

from bridget.discord2se import DiscordToSEForwarder
from bridget.discordifier import Discordifier
from bridget.models import BridgedMessage, Configuration
from bridget.se2discord import SEToDiscordForwarder
from bridget.util import pretty_delta, resolve_chat_pfp

class BridgetClient(Client):
    def __init__(self, config: Configuration):
        intents = Intents.none()
        intents.guilds = True
        intents.guild_messages = True
        intents.guild_typing = True
        intents.message_content = True
        intents.messages = True
        super().__init__(intents=intents)

        self.config = config
        self.logger = getLogger("DiscordClient")
        self.tree = CommandTree(self)
        self.allowed_mentions = AllowedMentions(everyone=False, users=True, roles=False, replied_user=True)
        # self.tree.command(name="queued", description="Check number of queued messages")(self.que)
        # self.tree.context_menu(name="Message permalink")(self.messageLinkCommand)
        # self.tree.context_menu(name="User info")(self.userInfoCommand)
        # roomGroup = Group(name="room", description="Commands relating to the bridged room")
        # roomGroup.command(name="info", description="Information about the room")(self.roomInfoCommand)
        # roomGroup.command(name="users", description="List of users chatting in the room")(self.userListCommand)
        self.tree.add_command(Command(
            name="queued",
            description="Check how many messages/edits/deletions are queued",
            callback=self.check_queued
        ))
        self.tree.add_command(ContextMenu(
            name="Message permalink",
            callback=self.message_permalink
        ))
        self.tree.add_command(ContextMenu(
            name="User information",
            callback=self.user_info
        ))
        room_group = Group(name="room", description="Commands relating to the room bridged to this channel")
        room_group.add_command(Command(
            name="info",
            description="Information about the room",
            callback=self.room_info
        ))
        room_group.add_command(Command(
            name="users",
            description="A list of users currently in the room",
            callback=self.user_list
        ))
        self.tree.add_command(room_group)

        self.se_forwarders: dict[Messageable, DiscordToSEForwarder] = {}
        self.ignore = {forwarder["channel"]: set(forwarder["ignore"]) for forwarder in self.config["dual"]}

    async def setup_hook(self):
        await self.tree.sync()

    def should_forward(self, message: Message):
        return message.channel in self.se_forwarders and message.author.discriminator != "0000" and message.author.id not in self.ignore[message.channel.id]

    async def on_message(self, message: Message):
        if self.should_forward(message):
            await self.se_forwarders[message.channel].queue_message(message)

    async def on_message_edit(self, old: Message, new: Message):
        if old.content != new.content and isinstance(new.author, Member) and self.should_forward(new):
            await self.se_forwarders[new.channel].queue_edit(new)
        
    async def on_message_delete(self, message: Message):
        if self.should_forward(message):
            await self.se_forwarders[message.channel].queue_delete(message)

    async def on_typing(self, channel: Messageable, user: User | Member, when: datetime):
        if channel in self.se_forwarders and isinstance(user, Member):
            await self.se_forwarders[channel].queue_typing(user)

    async def check_queued(self, interaction: Interaction):
        if interaction.channel is not None and interaction.channel in self.se_forwarders:
            forwarder = self.se_forwarders[interaction.channel]
            await interaction.response.send_message(embed=Embed(
                color=Color.brand_green(),
                title="Queue status",
                description="\n".join((
                    f"{forwarder._send_queue.qsize()} messages to send",
                    f"{forwarder._edit_queue.qsize()} messages to edit",
                    f"{forwarder._delete_queue.qsize()} messages to delete",
                )),
            ), ephemeral=True)
        else:
            await interaction.response.send_message(content="This channel is not bridged.")

    async def message_permalink(self, interaction: Interaction, message: Message):
        if message.channel in self.se_forwarders:
            forwarder = self.se_forwarders[message.channel]
            if (bridge_record := await forwarder.get_bridge_record(message.id)) is not None:
                await interaction.response.send_message(f"Message permalink: <https://chat.stackexchange.com/transcript/message/{bridge_record.se_message_id}#{bridge_record.se_message_id}>", ephemeral=True)
                return
        await interaction.response.send_message("This message was not bridged.", ephemeral=True)

    async def room_info(self, interaction: Interaction):
        if interaction.channel is not None and interaction.channel in self.se_forwarders:
            forwarder = self.se_forwarders[interaction.channel]
            async with ClientSession() as session:
                async with session.get(f"https://chat.stackexchange.com/rooms/thumbs/{forwarder.room.room_id}") as response:
                    room_info = await response.json()
                async with session.get(f"https://chat.stackexchange.com/rooms/{forwarder.room.room_id}") as response:
                    soup = BeautifulSoup(await response.content.read(), features="lxml")
                    assert isinstance(userDiv := soup.find(class_="js-present"), Tag)
                    users = json.loads(html.unescape(userDiv.attrs["data-users"]))
            await interaction.response.send_message(embed=Embed(
                title=room_info["name"],
                description=SEToDiscordForwarder.converter.convert(BeautifulSoup(room_info["description"], features="lxml")),
                url=f"https://chat.stackexchange.com/rooms/{forwarder.room.room_id}",
            ).add_field(
                name="In room", value=", ".join(
                    f"[{user['name']}](https://chat.stackexchange.com/user/{user['id']})" for user in users
                )
            ), ephemeral=True)
        else:
            await interaction.response.send_message(content="This channel is not bridged.", ephemeral=True)
    
    def make_user_embed(self, user: dict):
        return Embed(
            title=user["name"],
            description=user.get("user_message", MISSING),
            url=f"https://chat.stackexchange.com/user/{user['id']}",
        ).add_field(
            name="Reputation", value=user["reputation"]
        ).add_field(
            name="Last seen", value=pretty_delta((datetime.now() - datetime.fromtimestamp(user["last_seen"]))) if user["last_seen"] != None else "Unknown", inline=True
        ).add_field(
            name="Last message", value=pretty_delta((datetime.now() - datetime.fromtimestamp(user["last_post"]))) if user["last_post"] != None else "Unknown", inline=True
        ).set_thumbnail(
            url=resolve_chat_pfp(user["email_hash"])
        ).set_footer(
            text=user["site"]["caption"],
            icon_url=Discordifier.fix_url(user["site"]["icon"])
        )

    async def user_list(self, interaction: Interaction):
        if interaction.channel is not None and interaction.channel in self.se_forwarders:
            await interaction.response.defer(ephemeral=True, thinking=True)
            forwarder = self.se_forwarders[interaction.channel]
            async with ClientSession() as session:
                async with session.get(f"https://chat.stackexchange.com/rooms/{forwarder.room.room_id}") as response:
                    soup = BeautifulSoup(await response.content.read(), features="lxml")
                    assert isinstance(user_list := soup.find(class_="js-present"), Tag)
                    users = json.loads(html.unescape(user_list.attrs["data-users"]))

                embeds = []
                for partial in users:
                    async with session.get(f"https://chat.stackexchange.com/users/thumbs/{partial['id']}") as response:
                        user = await response.json()
                    embeds.append(
                        self.make_user_embed(user)
                    )
            await interaction.followup.send(embeds=embeds, ephemeral=True)
        else:
            await interaction.response.send_message(content="This channel is not bridged.", ephemeral=True)

    async def user_info(self, interaction: Interaction, message: Message):
        if message.channel in self.se_forwarders:
            forwarder = self.se_forwarders[message.channel]
            if (bridge_record := await forwarder.engine.find_one(BridgedMessage, BridgedMessage.discord_message_id == message.id)) is not None:
                async with ClientSession() as session, session.get(f"https://chat.stackexchange.com/users/thumbs/{bridge_record.se_user_id}") as response:
                    user = await response.json()
                await interaction.response.send_message(embed=self.make_user_embed(user), ephemeral=True)
            else:
                await interaction.response.send_message("This message was not bridged.", ephemeral=True)
        else:
            await interaction.response.send_message(content="This channel is not bridged.", ephemeral=True)


    async def run(self) -> None:
        engine = AIOEngine(AsyncIOMotorClient(self.config["database"]["uri"]), self.config["database"]["name"])
        credentials = await Credentials.load_or_authenticate("credentials.dat", self.config["chat"]["email"], self.config["chat"]["password"])
        await self.login(self.config["token"])

        async with self, TaskGroup() as group:
            group.create_task(self.connect())
            await self.wait_until_ready()
            assert self.user is not None
            
            for one_way_config in self.config["single"]:
                webhook = await Webhook.from_url(one_way_config["hook"], client=self).fetch()
                assert webhook.channel is not None
                forwarder = SEToDiscordForwarder(one_way_config["room"], [credentials.user_id], one_way_config["noembed"], webhook, engine)
                group.create_task(forwarder.run())
                self.logger.info(f"Started one-way forwarder from room {one_way_config['room']} to channel {webhook.channel.name} in guild {webhook.channel.guild.name}")
            
            for two_way_config in self.config["dual"]:
                channel = await self.fetch_channel(two_way_config["channel"])
                assert isinstance(channel, TextChannel)
                if not isinstance(webhook := find(lambda webhook: webhook.user == self.user, await channel.webhooks()), Webhook):
                    webhook = await channel.create_webhook(name="Bridget", reason="Creating bridge webhook")
                room = await Room.join(credentials, two_way_config["room"])
                se_to_discord = SEToDiscordForwarder(room.room_id, [credentials.user_id], two_way_config.get("noembed", []), webhook, engine)
                discord_to_se = DiscordToSEForwarder(room, engine, channel, self.user.id, two_way_config["roleIcons"], two_way_config["ignore"])
                self.se_forwarders[channel] = discord_to_se
                group.create_task(se_to_discord.run())
                group.create_task(discord_to_se.run())
                self.logger.info(f"Started two-way forwarder between room {room.room_id} and channel {channel.name} in guild {channel.guild.name}")

            self.logger.info("Forwarders started.")