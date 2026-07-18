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
        intents.message_content = True
        super().__init__(
            command_prefix=commands.when_mentioned_or("?"),
            help_command=None,  # disabled; custom one in cogs/help.py
            intents=intents,
        )

    async def setup_hook(self) -> None:
        await self.load_extension("cogs.music")
        await self.load_extension("cogs.help")
        await self.load_extension("cogs.utility")
        await self.load_extension("cogs.couples")
        await self.load_extension("cogs.games")
        await self.load_extension("cogs.fun")
        await self.load_extension("cogs.queue")
        await self.load_extension("cogs.audio")
        await self.load_extension("cogs.premium")
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

    # ------------------------------------------------------------------ #
    #  Global command error handler
    #
    #  Catches every error that a command raises and was not already
    #  handled by a local @command.error decorator.  Gives the user a
    #  clear, friendly message for all common failure modes so nothing
    #  ever fails silently.
    # ------------------------------------------------------------------ #

    async def on_command_error(
        self, ctx: commands.Context, error: commands.CommandError
    ) -> None:
        # Unwrap CommandInvokeError so we work with the real exception.
        original = getattr(error, "original", error)

        # ── Silent / expected ────────────────────────────────────────────
        if isinstance(error, commands.CommandNotFound):
            # Unknown prefix command — ignore silently.
            return

        # ── User-facing error messages ───────────────────────────────────
        if isinstance(error, commands.CommandOnCooldown):
            await ctx.send(
                f"⏳ Slow down! Try again in **{error.retry_after:.1f}s**.",
                ephemeral=True,
                delete_after=6,
            )
            return

        if isinstance(error, commands.MissingRequiredArgument):
            await ctx.send(
                f"❌ Missing required argument: `{error.param.name}`.\n"
                "Use `?help` (or `/help`) to see the correct usage.",
                ephemeral=True,
            )
            return

        if isinstance(error, (commands.BadArgument, commands.BadUnionArgument)):
            await ctx.send(
                f"❌ Invalid argument — {original}\n"
                "Use `?help` (or `/help`) to see the correct usage.",
                ephemeral=True,
            )
            return

        if isinstance(error, commands.NoPrivateMessage):
            await ctx.send(
                "❌ This command can only be used inside a server.",
                ephemeral=True,
            )
            return

        if isinstance(error, commands.MissingPermissions):
            missing = ", ".join(f"`{p}`" for p in error.missing_permissions)
            await ctx.send(
                f"❌ You need the following permission(s) to use this command: {missing}",
                ephemeral=True,
            )
            return

        if isinstance(error, commands.BotMissingPermissions):
            missing = ", ".join(f"`{p}`" for p in error.missing_permissions)
            await ctx.send(
                f"❌ I'm missing the following permission(s): {missing}\n"
                "Please ask a server admin to fix my role permissions.",
                ephemeral=True,
            )
            return

        if isinstance(error, commands.CheckFailure):
            await ctx.send(
                "❌ You don't have permission to use this command here.",
                ephemeral=True,
            )
            return

        if isinstance(error, commands.DisabledCommand):
            await ctx.send("❌ This command is currently disabled.", ephemeral=True)
            return

        # ── Unexpected error — log it, notify the user ───────────────────
        print(
            f"[error] Unhandled exception in command "
            f"{ctx.command!r} invoked by {ctx.author} ({ctx.author.id}): "
            f"{original!r}",
            flush=True,
        )
        await ctx.send(
            "⚠️ An unexpected error occurred. Please try again in a moment.",
            ephemeral=True,
        )
        # Re-raise so the full traceback appears in the workflow log.
        raise original


async def main() -> None:
    bot = MusicBot()
    async with bot:
        await bot.start(TOKEN)


if __name__ == "__main__":
    asyncio.run(main())
