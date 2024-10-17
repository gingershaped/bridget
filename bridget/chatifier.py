from typing import Optional
from itertools import chain

from discord import Guild
from discord_markdown_ast_parser import parse
from discord_markdown_ast_parser.parser import NodeType, Node

class Chatifier:
    def __init__(self, guild: Guild):
        self.guild = guild

    async def convertChildren(self, node: Node):
        if node.children is None:
            return
        for child in node.children:
            async for string in self.convertNode(child):
                yield string
    async def wrap(self, node: Node, start: str, end: Optional[str] = None):
        yield start
        async for string in self.convertChildren(node):
            yield string
        yield end if end is not None else start

    async def convertNode(self, node: Node):
        match node.node_type:
            case NodeType.TEXT:
                assert node.text_content is not None
                yield node.text_content
            case NodeType.ITALIC | NodeType.BOLD | NodeType.UNDERLINE:
                async for string in self.wrap(node, "_"):
                    yield string
            case NodeType.STRIKETHROUGH:
                async for string in self.wrap(node, "---"):
                    yield string
            case NodeType.SPOILER:
                async for string in self.wrap(node, "[spoiler](https://spoiler \"", "\")"):
                    yield string
            case NodeType.CODE_INLINE:
                async for string in self.wrap(node, "`"):
                    yield string
            case NodeType.USER:
                assert node.discord_id is not None
                member = await self.guild.fetch_member(node.discord_id)
                if member is None:
                    yield "`@<unknown user>`"
                else:
                    yield f"`@{member.display_name}`"
            case NodeType.ROLE:
                assert node.discord_id is not None
                role = self.guild.get_role(node.discord_id)
                if role is None:
                    yield "`@<unknown role>`"
                else:
                    yield f"`@{role.name}`"
            case NodeType.CHANNEL:
                assert node.discord_id is not None
                channel = await self.guild.fetch_channel(node.discord_id)
                if channel is None:
                    yield "#<unknown channel>"
                else:
                    yield f"#{channel.name}"
            case NodeType.EMOJI_CUSTOM | NodeType.EMOJI_UNICODE_ENCODED:
                assert node.emoji_name is not None
                yield f":{node.emoji_name}:"
            case NodeType.URL_WITH_PREVIEW | NodeType.URL_WITHOUT_PREVIEW:
                assert node.url is not None
                yield node.url
            case NodeType.QUOTE_BLOCK:
                for line in "".join([i async for i in self.convertChildren(node)]).splitlines(True):
                    yield f"> {line}"
            case NodeType.CODE_BLOCK:
                for line in "".join([i async for i in self.convertChildren(node)]).splitlines(True):
                    yield f"    {line}"

    async def convert(self, message: str):
        nodes = parse(message)
        if len(nodes) == 1 and nodes[0].node_type in (NodeType.QUOTE_BLOCK, NodeType.CODE_BLOCK):
            return "".join([i async for i in self.convertNode(nodes[0])])
        else:
            return "".join(chain(*[[i async for i in self.convertNode(node)] for node in nodes])).strip("\n")