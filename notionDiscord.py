import discord
import logging
import requests
import asyncio
import os
from dotenv import load_dotenv
from notion_client import AsyncClient

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# DISCORD SETUP
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID = os.getenv("DISCORD_GUILD_ID") 
AUTHOR_ID = os.getenv("DISCORD_AUTHOR_ID") 



# NOTION SETUP
NOTION_TOKEN = os.getenv("NOTION_TOKEN")
DATABASE_ID  = os.getenv("DATABASE_ID")
NOTION_API_URL = "https://api.notion.com/v1/pages"
NOTION_STATUS_VALUE = "Planned"  

notion = AsyncClient(auth=NOTION_TOKEN)

intents = discord.Intents.default()
intents.guilds = True

class MyClient(discord.Client):
    async def on_ready(self):
        logging.info('Logged in as %s', self.user)

    async def on_guild_channel_create(self, channel):
        if isinstance(channel, discord.TextChannel):
            logging.info("New channel: %s", channel.name)
            create_notion_page(channel.name)

    async def on_message(self, message):
        # Prevent the bot from responding to its own messages
        if message.author.id == AUTHOR_ID:
            logging.info("Jannik:")
            await create_notion_page(channel_name=message.content)
        logging.info(message.content)

def create_notion_page2(channel_name):
    headers = {
        "Authorization": f"Bearer {NOTION_TOKEN}",
        "Notion-Version": "2022-06-28",
        "Content-Type": "application/json",
    }

    data = {
        "parent": { "database_id": DATABASE_ID },
        "properties": {
            "Name": {
                "title": [
                    {
                        "text": {
                            "content": channel_name
                        }
                    }
                ]
            },
            "Status": {  # Example field
                "select": { "name": "Planned" }
            }
        }
    }

    try:
        response = requests.post(NOTION_API_URL, headers=headers, json=data)
        response.raise_for_status()
        logging.info("Notion response: %s %s", response.status_code, response.text)
    except requests.RequestException as exc:
        logging.exception("Notion request failed: %s", exc)


async def create_notion_page(channel_name: str) -> None:
        """Create a Notion page if one with the same title does not yet exist."""
        try:
            # 1. Quick existence check to avoid duplicates.
            query = await notion.databases.query(
                database_id=DATABASE_ID,
                filter={
                    "property": "Name",
                    "title": {"equals": channel_name}
                }
            )
            if query["results"]:
                logging.info("Notion page '%s' already exists; skipping.", channel_name)
                return

            # 2. Create page.
            await notion.pages.create(
                parent={"database_id": DATABASE_ID},
                properties={
                    "Name":   {"title": [{"text": {"content": channel_name}}]},
                    "Status": {"select": {"name": NOTION_STATUS_VALUE}},
                },
            )
            logging.info("Created Notion page for '%s'.", channel_name)

        except Exception as exc:
            logging.exception("Failed to sync channel '%s': %s", channel_name, exc)

client = MyClient(intents=intents)
client.run(DISCORD_TOKEN)
