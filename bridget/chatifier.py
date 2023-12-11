from typing import Iterable, Optional
from dataclasses import dataclass
from itertools import chain

from discord import Guild
from discord_markdown_ast_parser import parse
from discord_markdown_ast_parser.parser import NodeType, Node

class Chatifier:
    def __init__(self, guild: Guild):
        self.guild = guild

    def convertChildren(self, node: Node) -> Iterable[str]:
        if node.children is None:
            return
        yield from chain(*map(self.convertNode, node.children))
    def wrap(self, node: Node, start: str, end: Optional[str] = None):
        yield start
        yield from self.convertChildren(node)
        yield end if end is not None else start

    def convertNode(self, node: Node) -> Iterable[str]:
        # hopefully nobody notices me using the tiny @ here >:)
        match node.node_type:
            case NodeType.TEXT:
                assert node.text_content is not None
                yield node.text_content
            case NodeType.ITALIC | NodeType.BOLD | NodeType.UNDERLINE:
                yield from self.wrap(node, "_")
            case NodeType.STRIKETHROUGH:
                yield from self.wrap(node, "---")
            case NodeType.SPOILER:
                yield from self.wrap(node, "[spoiler](https://spoiler \"", "\")")
            case NodeType.CODE_INLINE:
                yield from self.wrap(node, "`")
            case NodeType.USER:
                assert node.discord_id is not None
                member = self.guild.get_member(node.discord_id)
                if member is None:
                    yield "﹫<unknown user>"
                else:
                    yield f"﹫{member.display_name}"
            case NodeType.ROLE:
                assert node.discord_id is not None
                role = self.guild.get_role(node.discord_id)
                if role is None:
                    yield "﹫<unknown role>"
                else:
                    yield f"﹫{role.name}"
            case NodeType.CHANNEL:
                assert node.discord_id is not None
                channel = self.guild.get_channel(node.discord_id)
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
                for line in "".join(self.convertChildren(node)).splitlines(True):
                    yield f"> {line}"
            case NodeType.CODE_BLOCK:
                for line in "".join(self.convertChildren(node)).splitlines(True):
                    yield f"    {line}"

    def convert(self, message: str):
        nodes = parse(message)
        if len(nodes) == 1 and nodes[0].node_type in (NodeType.QUOTE_BLOCK, NodeType.CODE_BLOCK):
            return "".join(self.convertNode(nodes[0]))
        else:
            return "".join(chain(*map(self.convertNode, nodes))).strip("\n")