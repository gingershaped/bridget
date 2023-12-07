from typing import TypedDict, Optional
from datetime import datetime

from odmantic import Field, Model

class BridgedMessage(Model):
    chatIdent: int = Field(primary_field=True)
    discordIdent: int = Field(unique=True, index=True)
    chatUser: int
    discordUser: int
    recievedAt: datetime

class DualBridge(TypedDict):
    guild: int
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

class ShlinkConfig(TypedDict):
    url: str
    key: str
    threshold: int

class ChatConfig(TypedDict):
    email: str
    password: str
    host: str

class Configuration(TypedDict):
    token: str
    chat: ChatConfig
    database: DatabaseConfig
    shlink: Optional[ShlinkConfig]
    dual: list[DualBridge]
    single: list[SingleBridge]