from datetime import datetime
from typing import TypedDict

from odmantic import Field, Model


class BridgedMessage(Model):
    se_message_id: int = Field(primary_field=True)
    discord_message_id: int = Field(unique=True, index=True)
    se_user_id: int
    discord_user_id: int
    received_at: datetime

class DualBridge(TypedDict):
    channel: int
    room: int
    roleIcons: dict[str, str]
    ignore: list[int]
    noembed: list[int]

class SingleBridge(TypedDict):
    hook: str
    room: int
    noembed: list[int]

class DatabaseConfig(TypedDict):
    uri: str
    name: str

class ChatConfig(TypedDict):
    email: str
    password: str

class Configuration(TypedDict):
    token: str
    chat: ChatConfig
    database: DatabaseConfig
    dual: list[DualBridge]
    single: list[SingleBridge]