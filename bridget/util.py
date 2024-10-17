from datetime import timedelta
from urllib.parse import urlparse, urlunparse
from aiohttp import ClientSession

STACK_IMGUR = "i.sstatic.net"

MINUTE = 60
HOUR = MINUTE * 60
DAY = HOUR * 24
YEAR = DAY * 365 # I do like making assumptions

def approximate_delta(delta: timedelta):
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

def pretty_delta(delta: timedelta):
    if delta.seconds <= 10:
        return "just now"
    return approximate_delta(delta) + " ago"

def resolve_chat_pfp(pfp: str):
    if pfp.startswith("!"):
        pfp = pfp.removeprefix("!")
        url = urlparse(pfp)
        if url.netloc == STACK_IMGUR:
            return urlunparse(("https", STACK_IMGUR, url.path, "", "s=256", ""))
        return pfp
    return f"https://www.gravatar.com/avatar/{pfp}?s=256&d=identicon&r=PG"

class ChatPFPFetcher:
    def __init__(self):
        self.pfp_cache = {}

    async def fetch_pfp_url(self, user: int):
        if user not in self.pfp_cache:
            async with ClientSession() as session, session.get(f"https://chat.stackexchange.com/users/thumbs/{user}") as response:
                self.pfp_cache[user] = resolve_chat_pfp((await response.json())["email_hash"])
        return self.pfp_cache[user]