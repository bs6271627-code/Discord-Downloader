from __future__ import annotations

import random

import discord
import wavelink
from discord import app_commands
from discord.ext import commands

ACCENT = 0xC193CC


class QueueCommands(commands.Cog, name="Queue"):
    """Queue management commands."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def _get_player(self, ctx: commands.Context) -> wavelink.Player | None:
        player: wavelink.Player | None = ctx.guild.voice_client  # type: ignore[assignment]
        if player is None:
            await ctx.send("❌ I'm not in a voice channel.", ephemeral=True)
            return None
        return player

    # ------------------------------------------------------------------ #

    @commands.hybrid_command(name="remove", description="Remove a track from the queue by position.")
    @app_commands.describe(position="Queue position to remove (1 = next track)")
    @commands.guild_only()
    async def remove(self, ctx: commands.Context, position: int) -> None:
        await ctx.defer()
        player = await self._get_player(ctx)
        if player is None:
            return
        if player.queue.is_empty:
            await ctx.send("❌ The queue is empty.", ephemeral=True)
            return
        tracks = list(player.queue)
        if not (1 <= position <= len(tracks)):
            await ctx.send(
                f"❌ Position must be between 1 and {len(tracks)}.", ephemeral=True
            )
            return
        removed = tracks.pop(position - 1)
        player.queue.clear()
        for t in tracks:
            player.queue.put(t)
        embed = discord.Embed(
            description=f"🗑️ Removed **{removed.title}** from the queue.",
            color=ACCENT,
        )
        await ctx.send(embed=embed)

    # ------------------------------------------------------------------ #

    @commands.hybrid_command(name="clearqueue", description="Clear the entire queue.")
    @commands.guild_only()
    async def clearqueue(self, ctx: commands.Context) -> None:
        await ctx.defer()
        player = await self._get_player(ctx)
        if player is None:
            return
        player.queue.clear()
        embed = discord.Embed(description="🗑️ Queue cleared.", color=ACCENT)
        await ctx.send(embed=embed)

    # ------------------------------------------------------------------ #

    @commands.hybrid_command(name="shuffle", description="Shuffle the queue.")
    @commands.guild_only()
    async def shuffle(self, ctx: commands.Context) -> None:
        await ctx.defer()
        player = await self._get_player(ctx)
        if player is None:
            return
        if player.queue.is_empty:
            await ctx.send("❌ The queue is empty.", ephemeral=True)
            return
        tracks = list(player.queue)
        random.shuffle(tracks)
        player.queue.clear()
        for t in tracks:
            player.queue.put(t)
        embed = discord.Embed(description=f"🔀 Shuffled **{len(tracks)}** tracks!", color=ACCENT)
        await ctx.send(embed=embed)

    # ------------------------------------------------------------------ #

    @commands.hybrid_command(name="loop", description="Cycle loop mode: off → track → queue → off.")
    @commands.guild_only()
    async def loop(self, ctx: commands.Context) -> None:
        await ctx.defer()
        player = await self._get_player(ctx)
        if player is None:
            return

        if player.queue.mode == wavelink.QueueMode.normal:
            player.queue.mode = wavelink.QueueMode.loop
            msg = "🔂 Now looping the **current track**."
        elif player.queue.mode == wavelink.QueueMode.loop:
            player.queue.mode = wavelink.QueueMode.loop_all
            msg = "🔁 Now looping the **entire queue**."
        else:
            player.queue.mode = wavelink.QueueMode.normal
            msg = "➡️ Loop **disabled**."

        await ctx.send(embed=discord.Embed(description=msg, color=ACCENT))


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(QueueCommands(bot))
