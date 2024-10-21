from dataclasses import dataclass
from datetime import datetime
from typing import cast
from urllib.parse import urlsplit, urlunsplit

from discord import Embed
from markdownify import MarkdownConverter, chomp
from bs4 import BeautifulSoup, PageElement, Tag, NavigableString

class PatchedConverter(MarkdownConverter):
    convert_strike = MarkdownConverter.convert_s
    def convert_a(self, el, text, convert_as_inline):
        # hacky spoiler converter
        # matches links where the title is "spoiler"
        # or the address is http(s)://spoiler
        prefix, suffix, text = chomp(text)
        if not text:
            return ""
        href = el.get("href")
        title = el.get("title")
        if text == "spoiler" or urlsplit(href).netloc == "spoiler":
            return f"||{title}||"
        return super().convert_a(el, text, convert_as_inline)

class Discordifier:
    def __init__(self):
        self.converter = PatchedConverter()

    @classmethod
    def fix_url(cls, url: str):
        # Screw with URLs to make them work outside of a browser
        scheme, netloc, path, query, fragment = urlsplit(url)
        if not scheme:
            scheme = "https"
        if not netloc:
            netloc = "chat.stackexchange.com"
        return urlunsplit((scheme, netloc, path, query, fragment))

    def convert_onebox(self, div: Tag):
        # welcome to assert hell, you may not leave
        # I love HTML parsing in Python so much
        if "ob-post" in div["class"]:
            # post onebox
            assert isinstance(title_wrapper := div.find(class_="ob-post-title"), Tag)
            assert isinstance(title := title_wrapper.find("a"), Tag)
            assert isinstance(body := div.find(class_="ob-post-body"), Tag)
            assert isinstance(user_avatar := body.find(class_="user-gravatar32"), Tag)
            assert isinstance(site_icon := div.find(class_="ob-post-siteicon"), Tag)
            assert isinstance(score := div.find(class_="ob-post-votes"), Tag)
            user_avatar.extract() # it'll go in the body unless we do this

            return Embed(
                title=title.text,
                url=self.fix_url(title.attrs["href"]),
                description=self.convert_message(body)
            ).set_thumbnail(
                url=user_avatar["src"]
            ).set_author(
                name=user_avatar["title"]
            ).set_footer(
                text=site_icon["title"], icon_url=self.fix_url(site_icon.attrs["src"])
            ).add_field(
                name="Score", value=score.text, inline=True
            )
        elif "ob-user" in div["class"]:
            # user onebox
            assert isinstance(user_avatar_container := div.find(class_="user-gravatar64"), Tag)
            assert isinstance(user_avatar := user_avatar_container.find("img"), Tag)
            assert isinstance(user_name := div.find(class_="ob-user-username"), Tag)
            assert isinstance(site_icon := user_name.find_previous_sibling("img"), Tag)
            assert isinstance(reputation := div.find(class_="reputation-score"), Tag)

            return Embed(
                title=user_name.text,
                url=self.fix_url(user_name.attrs["href"])
            ).set_thumbnail(
                url=user_avatar["src"]
            ).set_footer(
                text=site_icon["title"], icon_url=self.fix_url(site_icon.attrs["src"])
            ).add_field(
                name="Reputation", value=reputation.text, inline=True
            )
        elif "ob-message" in div["class"]:
            # message onebox
            assert isinstance(message_permalink := div.find(class_="roomname"), Tag)
            assert isinstance(timestamp := message_permalink.find("span"), Tag)
            assert isinstance(user_name := div.find(class_="user-name"), Tag)
            assert isinstance(content := div.find(class_="quote"), Tag)
            embed = Embed(
                description=self.convert_message(content)
            ).set_author(
                name=user_name.text,
                url=self.fix_url(message_permalink.attrs["href"])
            )
            embed.timestamp = datetime.fromisoformat(timestamp.attrs["title"].strip())
            return embed
        elif "ob-youtube" in div["class"]:
            # YT onebox
            assert isinstance(link := div.find("a"), Tag)
            return cast(str, link.attrs["href"])
        elif "ob-image" in div["class"]:
            # image onebox
            assert isinstance(img := div.find("img"), Tag)
            return self.fix_url(img.attrs["src"])
        elif "ob-wikipedia" in div["class"]:
            # wikipedia onebox
            assert isinstance(title := div.find(class_="ob-wikipedia-title"), Tag)
            assert isinstance(link := title.find("a"), Tag)
            return cast(str, link.attrs["href"])

    def convert_room_onebox(self, div: Tag):
        assert isinstance(room_name_wrapper := div.find(class_="room-name"), Tag)
        assert isinstance(room_name := room_name_wrapper.find("a"), Tag)
        assert isinstance(room_description := div.find(class_="room-mini-description"), Tag)
        assert isinstance(room_users := div.find(class_="room-current-user-count"), Tag)

        return Embed(
            title=room_name.text,
            url=self.fix_url(room_name.attrs["href"]),
            description=room_description["title"]
        ).set_footer(
            text=f"{room_users.text} users chatting"
        )
    def convert_bookmark_onebox(self, div: Tag):
        assert isinstance(title := div.find("h3"), Tag)
        assert isinstance(title_link := title.find("a"), Tag)
        assert isinstance(description := div.find("p"), Tag)
        assert isinstance(user := div.find(class_="bookmark-user"), Tag)
        assert isinstance(user_link := user.find("a"), Tag)

        return Embed(
            title=title_link.text,
            url=self.fix_url(title_link.attrs["href"]),
            description=self.convert_message(description)
        ).set_author(
            name=user_link.text,
            url=self.fix_url(user_link.attrs["href"])
        )
    
    def convert_multiline_message(self, element: Tag):
        if "quote" in element["class"]:
            element.name = "blockquote"
        return self.convert_message(element)

    def convert_message(self, element: Tag) -> str:
        # preprocessing steps
        # convert all quotedivs to blockquotes
        for quote in element.find_all("div", class_="quote"):
            assert isinstance(quote, Tag)
            quote.name = "blockquote"
        # patch all links
        # (magic links use bare URLs for some reason)
        for a in element.find_all("a"):
            a.attrs["href"] = self.fix_url(a.attrs["href"])
        return self.converter.process_tag(element, False, False)

    def convert(self, body: Tag):
        if isinstance(div := body.find(class_="full", recursive=False), Tag):
            # multiline message
            return self.convert_multiline_message(div)
        elif isinstance(div := body.find(class_="partial", recursive=False), Tag):
            # partial message
            # can't be assed to get the full thing, sorry
            return self.convert_multiline_message(div)
        elif isinstance(div := body.find("div", recursive=False), Tag):
            # This is a onebox
            if "onebox" in div["class"]:
                return self.convert_onebox(div)
            elif "room-mini" in div["class"]:
                # room onebox
                return self.convert_room_onebox(div)
            elif "conversation-info" in div["class"]:
                # convo onebox
                return self.convert_bookmark_onebox(div)
        else:
            # single-line message
            return self.convert_message(body)