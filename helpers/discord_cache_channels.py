"""
Fetches all channels from the target Discord category and saves them to
local_discord_channels.json as a list of {id, name} objects.
Read-only — no mutations to Discord.
"""

import asyncio
import json
import logging
import os

import discord
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID = int(os.getenv("DISCORD_GUILD_ID"))
TARGET_CATEGORY_ID = 1078233039705997412
CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "cache")
CACHE_FILE = os.path.join(CACHE_DIR, "local_discord_channels.json")

intents = discord.Intents.default()
intents.guilds = True

client = discord.Client(intents=intents)


@client.event
async def on_ready():
    logging.info("Logged in as %s", client.user)

    guild = client.get_guild(GUILD_ID)
    if guild is None:
        logging.error("Guild %s not found", GUILD_ID)
        await client.close()
        return

    cat = guild.get_channel(TARGET_CATEGORY_ID)
    if cat is None or not isinstance(cat, discord.CategoryChannel):
        logging.error("Category %s not found", TARGET_CATEGORY_ID)
        await client.close()
        return

    channels = [
        {"id": ch.id, "name": ch.name}
        for ch in sorted(cat.channels, key=lambda c: c.position)
    ]

    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(channels, f, ensure_ascii=False, indent=2)

    logging.info("Saved %d channels to %s", len(channels), CACHE_FILE)
    await client.close()


asyncio.run(client.start(DISCORD_TOKEN))
