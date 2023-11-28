from datetime import timedelta
from urllib.parse import urlparse, urlunparse
from aiohttp import ClientSession

STACK_IMGUR = "i.stack.imgur.com"

MINUTE = 60
HOUR = MINUTE * 60
DAY = HOUR * 24
YEAR = DAY * 365 # I do like making assumptions

def approxDelta(delta: timedelta):
    def plural(n: int, suffix: str):
        if n == 1:
            return f"{n} {suffix}"
        return f"{n} {suffix}s"
    
    seconds = int(delta.total_seconds())
    if seconds < MINUTE:
        return plural(seconds, "second")
    if seconds < HOUR:
        return plural(seconds // MINUTE, "minute")
    if seconds < DAY:
        return plural(seconds // HOUR, "hour")
    if seconds < YEAR:
        return plural(seconds // DAY, "day")
    return plural(seconds // YEAR, "year")

def prettyDelta(delta: timedelta):
    if delta.seconds <= 10:
        return "just now"
    return approxDelta(delta) + " ago"

def resolveChatPFP(pfp: str):
    if pfp.startswith("!"):
        pfp = pfp.removeprefix("!")
        url = urlparse(pfp)
        if url.netloc == STACK_IMGUR:
            return urlunparse(("https", STACK_IMGUR, url.path, "", "s=256", ""))
        return pfp
    return f"https://www.gravatar.com/avatar/{pfp}?s=256&d=identicon&r=PG"

class ChatPFPFetcher:
    def __init__(self):
        self.pfpCache = {}

    async def getPFP(self, user: int):
        if user not in self.pfpCache:
            async with ClientSession() as session:
                async with session.get(
                    f"https://chat.stackexchange.com/users/thumbs/{user}"
                ) as response:
                    self.pfpCache[user] = resolveChatPFP((await response.json())["email_hash"])
        return self.pfpCache[user]