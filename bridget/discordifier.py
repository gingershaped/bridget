from dataclasses import dataclass
from datetime import datetime
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
    def fixUrl(cls, url: str):
        # Screw with URLs to make them work outside of a browser
        scheme, netloc, path, query, fragment = urlsplit(url)
        if not scheme:
            scheme = "https"
        if not netloc:
            netloc = "chat.stackexchange.com"
        return urlunsplit((scheme, netloc, path, query, fragment))

    def convertOnebox(self, soup: BeautifulSoup, div: Tag):
        # welcome to assert hell, you may not leave
        # I love HTML parsing in Python so much
        if "onebox" in div["class"]:
            if "ob-post" in div["class"]:
                # post onebox
                assert isinstance(titleWrapper := div.find(class_="ob-post-title"), Tag)
                assert isinstance(title := titleWrapper.find("a"), Tag)
                assert isinstance(body := div.find(class_="ob-post-body"), Tag)
                assert isinstance(userImg := body.find(class_="user-gravatar32"), Tag)
                assert isinstance(siteIcon := div.find(class_="ob-post-siteicon"), Tag)
                assert isinstance(score := div.find(class_="ob-post-votes"), Tag)
                userImg.extract() # it'll go in the body unless we do this

                return Embed(
                    title=title.text,
                    url=self.fixUrl(title.attrs["href"]),
                    description=self.convertHTMLToMarkdown(soup, body)
                ).set_thumbnail(
                    url=userImg["src"]
                ).set_author(
                    name=userImg["title"]
                ).set_footer(
                    text=siteIcon["title"], icon_url=self.fixUrl(siteIcon.attrs["src"])
                ).add_field(
                    name="Score", value=score.text, inline=True
                )
            elif "ob-user" in div["class"]:
                # user onebox
                assert isinstance(gravatarWrapper := div.find(class_="user-gravatar64"), Tag)
                assert isinstance(userPfp := gravatarWrapper.find("img"), Tag)
                assert isinstance(userName := div.find(class_="ob-user-username"), Tag)
                assert isinstance(siteIcon := userName.find_previous_sibling("img"), Tag)
                assert isinstance(reputation := div.find(class_="reputation-score"), Tag)

                return Embed(
                    title=userName.text,
                    url=self.fixUrl(userName.attrs["href"])
                ).set_thumbnail(
                    url=userPfp["src"]
                ).set_footer(
                    text=siteIcon["title"], icon_url=self.fixUrl(siteIcon.attrs["src"])
                ).add_field(
                    name="Reputation", value=reputation.text, inline=True
                )
            elif "ob-message" in div["class"]:
                # message onebox
                assert isinstance(messageLink := div.find(class_="roomname"), Tag)
                assert isinstance(timestamp := messageLink.find("span"), Tag)
                assert isinstance(userName := div.find(class_="user-name"), Tag)
                assert isinstance(content := div.find(class_="quote"), Tag)
                embed = Embed(
                    description=self.convertHTMLToMarkdown(soup, content)
                ).set_author(
                    name=userName.text,
                    url=self.fixUrl(messageLink.attrs["href"])
                )
                embed.timestamp = datetime.fromisoformat(timestamp.attrs["title"].strip())
                return embed
            elif "ob-youtube" in div["class"]:
                # YT onebox
                assert isinstance(link := div.find("a"), Tag)
                return link.attrs["href"]
            elif "ob-image" in div["class"]:
                # image onebox
                assert isinstance(img := div.find("img"), Tag)
                return self.fixUrl(img.attrs["src"])

    def convertRoomOnebox(self, soup: BeautifulSoup, div: Tag):
        assert isinstance(roomNameContainer := div.find(class_="room-name"), Tag)
        assert isinstance(roomName := roomNameContainer.find("a"), Tag)
        assert isinstance(roomDescription := div.find(class_="room-mini-description"), Tag)
        assert isinstance(roomUsers := div.find(class_="room-current-user-count"), Tag)

        return Embed(
            title=roomName.text,
            url=self.fixUrl(roomName.attrs["href"]),
            description=roomDescription["title"]
        ).set_footer(
            text=f"{roomUsers.text} users chatting"
        )
    def convertConvoOnebox(self, soup: BeautifulSoup, div: Tag):
        assert isinstance(title := div.find("h3"), Tag)
        assert isinstance(titleLink := title.find("a"), Tag)
        assert isinstance(description := div.find("p"), Tag)
        assert isinstance(user := div.find(class_="bookmark-user"), Tag)
        assert isinstance(userLink := user.find("a"), Tag)

        return Embed(
            title=titleLink.text,
            url=self.fixUrl(titleLink.attrs["href"]),
            description=self.convertHTMLToMarkdown(soup, description)
        ).set_author(
            name=userLink.text,
            url=self.fixUrl(userLink.attrs["href"])
        )
    
    def convertMultiline(self, soup: BeautifulSoup, element: Tag):
        if "quote" in element["class"]:
            element.name = "blockquote"
        return self.convertHTMLToMarkdown(soup, element)

    def convertHTMLToMarkdown(self, soup: BeautifulSoup, element: Tag) -> str:
        # preprocessing steps
        # convert all quotedivs to blockquotes
        for quote in element.find_all("div", class_="quote"):
            assert isinstance(quote, Tag)
            quote.name = "blockquote"
        # patch all links
        # (magic links use bare URLs for some reason)
        for a in element.find_all("a"):
            a.attrs["href"] = self.fixUrl(a.attrs["href"])
        return self.converter.process_tag(element, False, False)

    def convert(self, soup: BeautifulSoup):
        if isinstance(div := soup.find(class_="full", recursive=False), Tag):
            # multiline message
            return self.convertMultiline(soup, div)
        elif isinstance(div := soup.find(class_="partial", recursive=False), Tag):
            # partial message
            # can't be assed to get the full thing, sorry
            return self.convertMultiline(soup, div)
        elif isinstance(div := soup.find("div", recursive=False), Tag):
            # This is a onebox
            if "onebox" in div["class"]:
                return self.convertOnebox(soup, div)
            elif "room-mini" in div["class"]:
                # room onebox
                return self.convertRoomOnebox(soup, div)
            elif "conversation-info" in div["class"]:
                # convo onebox
                return self.convertConvoOnebox(soup, div)
        else:
            # single-line message
            return self.convertHTMLToMarkdown(soup, soup)