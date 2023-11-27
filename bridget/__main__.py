import json
import asyncio

from bridget import Bridget

with open("config.json") as file:
    config = json.load(file)

bridget = Bridget(config)
asyncio.run(bridget.run())