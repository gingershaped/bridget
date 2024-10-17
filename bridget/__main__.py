import json
import asyncio

from discord.utils import setup_logging
from bridget import BridgetClient

with open("config.json") as file:
    config = json.load(file)

setup_logging()
bridget = BridgetClient(config)
asyncio.run(bridget.run())