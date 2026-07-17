import asyncio
import os

import discord
import wavelink
from discord.ext import commands

TOKEN = os.environ.get("TOKEN")
if not TOKEN:
    raise RuntimeError("TOKEN environment variable is not set.")

LAVALINK_URI = "http://localhost:2333"
LAVALINK_PASSWORD = "youshallnotpass"


class MusicBot(commands.Bot):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        super().__init__(
            command_prefix=commands.when_mentioned_or("?"),
            intents=intents,
        )

    async def setup_hook(self) -> None:
        await self.load_extension("cogs.music")
        await self.tree.sync()
        print("Slash commands synced.", flush=True)

        # Connect to Lavalink — retry until the node is up.
        node = wavelink.Node(uri=LAVALINK_URI, password=LAVALINK_PASSWORD)
        for attempt in range(1, 13):
            try:
                await wavelink.Pool.connect(nodes=[node], client=self, cache_capacity=100)
                print("Connected to Lavalink node.", flush=True)
                return
            except Exception as exc:
                print(f"[lavalink] attempt {attempt}/12 failed: {exc}", flush=True)
                if attempt == 12:
                    print("[lavalink] giving up — start the Lavalink workflow first.", flush=True)
                else:
                    await asyncio.sleep(5)

    async def on_ready(self) -> None:
        print(f"Logged in as {self.user} (ID: {self.user.id})", flush=True)
        await self.change_presence(
            activity=discord.Activity(
                type=discord.ActivityType.listening, name="/play"
            )
        )


async def main() -> None:
    bot = MusicBot()
    async with bot:
        await bot.start(TOKEN)


if __name__ == "__main__":
    asyncio.run(main())
