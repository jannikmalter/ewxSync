"""
Read-only script: connects to Discord and lists all categories and channels in the guild.
No mutations — safe to run at any time.
"""

import asyncio
import os
import logging

import discord
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID = int(os.getenv("DISCORD_GUILD_ID"))
TARGET_CATEGORY_ID = 1078233039705997412

print(DISCORD_TOKEN)
print(GUILD_ID)


intents = discord.Intents.default()
intents.guilds = True

client = discord.Client(intents=intents)


@client.event
async def on_ready():
    logging.info("Logged in as %s", client.user)

    guild = client.get_guild(GUILD_ID)
    if guild is None:
        logging.error("Guild %s not found — check DISCORD_GUILD_ID in .env", GUILD_ID)
        await client.close()
        return

    logging.info("Guild: %s (id=%s, %d members)", guild.name, guild.id, guild.member_count)

    cat = guild.get_channel(TARGET_CATEGORY_ID)
    if cat is None or not isinstance(cat, discord.CategoryChannel):
        logging.error("Category %s not found", TARGET_CATEGORY_ID)
        await client.close()
        return

    print(f"\n=== [{cat.name}] (id={cat.id}) ===\n")
    for ch in sorted(cat.channels, key=lambda c: c.position):
        topic = getattr(ch, "topic", None) or ""
        topic_str = f"  |  {topic}" if topic else ""
        print(f"  #{ch.name}  (id={ch.id}, type={ch.type.name}){topic_str}")

    await client.close()


asyncio.run(client.start(DISCORD_TOKEN))
