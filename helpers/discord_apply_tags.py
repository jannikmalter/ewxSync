"""
Reads local_discord_eventworx_mapping.json and prepends an [EWX:P-NNNN] tag
to each mapped channel's topic.

Conventions:
  - EWX tag becomes the FIRST line of the topic.
  - Existing topic content is preserved (the tag is prepended, separated by \\n).
  - Channels already tagged with [EWX:...] are skipped (re-run safe).

Safety switches:
  - TEST_CHANNEL_ID: if set to an int, ONLY that channel is processed.
  - DRY_RUN: if True, no edits are sent to Discord — actions are only logged.
"""

import asyncio
import json
import logging
import os
import re

import discord
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID = int(os.getenv("DISCORD_GUILD_ID"))
MAPPING_FILE = "local_discord_eventworx_mapping.json"

# --- Safety switches -----------------------------------------------------
# Set to a channel id (int) to limit changes to a single channel for testing.
# Set to None to process every entry in the mapping file.
TEST_CHANNEL_ID: int | None = None

# If True, log intended changes but do not call channel.edit().
DRY_RUN = False
# -------------------------------------------------------------------------

EWX_TAG_RE = re.compile(r"^\s*\[EWX:[^\]]+\]")

intents = discord.Intents.default()
intents.guilds = True

client = discord.Client(intents=intents)


def build_new_topic(existing: str | None, project_number: str) -> str:
    tag = f"[EWX:{project_number}]"
    if not existing:
        return tag
    return f"{tag}\n{existing}"


@client.event
async def on_ready():
    logging.info("Logged in as %s (DRY_RUN=%s, TEST_CHANNEL_ID=%s)",
                 client.user, DRY_RUN, TEST_CHANNEL_ID)

    guild = client.get_guild(GUILD_ID)
    if guild is None:
        logging.error("Guild %s not found", GUILD_ID)
        await client.close()
        return

    with open(MAPPING_FILE, encoding="utf-8") as f:
        mapping = json.load(f)

    if TEST_CHANNEL_ID is not None:
        mapping = [m for m in mapping if m["id"] == TEST_CHANNEL_ID]
        if not mapping:
            logging.error("TEST_CHANNEL_ID %s not present in mapping file", TEST_CHANNEL_ID)
            await client.close()
            return

    updated = skipped = missing = 0

    for entry in mapping:
        channel_id = entry["id"]
        project_number = entry["projectNumber"]
        channel = guild.get_channel(channel_id)

        if channel is None:
            logging.warning("Channel %s (%s) not found", channel_id, entry.get("name"))
            missing += 1
            continue

        existing_topic = getattr(channel, "topic", None)

        if existing_topic and EWX_TAG_RE.match(existing_topic):
            logging.info("SKIP  #%s — already tagged", channel.name)
            skipped += 1
            continue

        new_topic = build_new_topic(existing_topic, project_number)
        logging.info("EDIT  #%s  →  prepending [EWX:%s]", channel.name, project_number)
        logging.info("       new topic: %r", new_topic)

        if not DRY_RUN:
            try:
                await channel.edit(topic=new_topic, reason="ewxSync: add EWX project tag")
                updated += 1
            except discord.HTTPException as exc:
                logging.exception("Failed to edit #%s: %s", channel.name, exc)
        else:
            updated += 1  # count as "would update"

    logging.info("Done. updated=%d skipped=%d missing=%d (dry_run=%s)",
                 updated, skipped, missing, DRY_RUN)
    await client.close()


asyncio.run(client.start(DISCORD_TOKEN))
