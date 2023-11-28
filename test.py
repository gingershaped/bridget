import asyncio
import aiohttp

async def main():
    async with aiohttp.ClientSession() as s:
        async with s.ws_connect("wss://rydwolf.xyz/whos_typing") as c:
            await c.send_str("bridge\n1")
            print("conn")
            while True:
                await c.send_str("\n\"eggman\"")
                await asyncio.sleep(0.5)

asyncio.run(main())