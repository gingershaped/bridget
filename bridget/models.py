from typing import TypedDict
from datetime import datetime

from odmantic import Field, Model

class BridgedMessage(Model):
    chatIdent: int = Field(unique=True, index=True)
    discordIdent: int = Field(unique=True, index=True)
    chatUser: int
    discordUser: int
    recievedAt: datetime

class DualBridge(TypedDict):
    guild: int
    channel: int
    room: int
    roleIcons: dict[int, str]

class SingleBridge(TypedDict):
    hook: str
    room: int

class DatabaseConfig(TypedDict):
    uri: str
    name: str

class ChatConfig(TypedDict):
    email: str
    password: str
    host: str

class Configuration(TypedDict):
    token: str
    chat: ChatConfig
    database: DatabaseConfig
    dual: list[DualBridge]
    single: list[SingleBridge]