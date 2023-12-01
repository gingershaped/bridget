from aiohttp import ClientSession

class Shlink:
    def __init__(self, url: str, key: str):
        self.url = url
        self.key = key
        
    async def shorten(self, url: str):
        async with ClientSession(self.url, headers={"X-Api-Key": self.key}) as session:
            async with session.post("/rest/v3/short-urls", data={
                "longUrl": url,
                "tags": ["bridget"]
            }) as response:
                response.raise_for_status()
                data = await response.json()
                return data["shortUrl"]