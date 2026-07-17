from __future__ import annotations

import discord
import wavelink
from discord import app_commands
from discord.ext import commands

ACCENT = 0xC193CC

# Per-guild filter state
_bass_on: set[int] = set()
_nightcore_on: set[int] = set()

BASS_BANDS: list[dict] = [
    {"band": 0,  "gain":  0.30},
    {"band": 1,  "gain":  0.25},
    {"band": 2,  "gain":  0.20},
    {"band": 3,  "gain":  0.15},
    {"band": 4,  "gain":  0.10},
    {"band": 5,  "gain":  0.05},
    {"band": 6,  "gain":  0.00},
    {"band": 7,  "gain":  0.00},
    {"band": 8,  "gain": -0.05},
    {"band": 9,  "gain": -0.10},
    {"band": 10, "gain": -0.10},
    {"band": 11, "gain": -0.10},
    {"band": 12, "gain": -0.10},
    {"band": 13, "gain": -0.10},
    {"band": 14, "gain": -0.10},
]

PRESET_CHOICES = [
    app_commands.Choice(name="Bass Boost 🎸",  value="bassboost"),
    app_commands.Choice(name="Nightcore 🌙",   value="nightcore"),
    app_commands.Choice(name="Vaporwave 🌊",   value="vaporwave"),
    app_commands.Choice(name="Karaoke 🎤",     value="karaoke"),
    app_commands.Choice(name="8D Audio 🎧",    value="8d"),
    app_commands.Choice(name="Reset ✅",        value="reset"),
]
VALID_PRESETS = {c.value for c in PRESET_CHOICES}


class AudioCommands(commands.Cog, name="Audio"):
    """Audio filter commands."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def _get_player(self, ctx: commands.Context) -> wavelink.Player | None:
        player: wavelink.Player | None = ctx.guild.voice_client  # type: ignore[assignment]
        if player is None:
            await ctx.send("❌ I'm not in a voice channel.", ephemeral=True)
            return None
        return player

    # ------------------------------------------------------------------ #

    @commands.hybrid_command(name="volume", description="Set the playback volume (0–200).")
    @app_commands.describe(level="Volume level: 0–200  (100 = normal)")
    @commands.guild_only()
    @commands.cooldown(1, 3, commands.BucketType.user)
    async def volume(self, ctx: commands.Context, level: int) -> None:
        await ctx.defer()
        player = await self._get_player(ctx)
        if player is None:
            return
        if not (0 <= level <= 200):
            await ctx.send("❌ Volume must be between 0 and 200.", ephemeral=True)
            return
        await player.set_volume(level)
        filled = round(level / 200 * 10)
        bar = "█" * filled + "░" * (10 - filled)
        emoji = "🔇" if level == 0 else ("🔉" if level < 100 else "🔊")
        embed = discord.Embed(
            description=f"{emoji} Volume set to **{level}%**\n`[{bar}]`",
            color=ACCENT,
        )
        await ctx.send(embed=embed)

    # ------------------------------------------------------------------ #

    @commands.hybrid_command(name="bassboost", description="Toggle bass boost filter.")
    @commands.guild_only()
    async def bassboost(self, ctx: commands.Context) -> None:
        await ctx.defer()
        player = await self._get_player(ctx)
        if player is None:
            return
        gid = ctx.guild.id  # type: ignore[union-attr]
        filters: wavelink.Filters = player.filters
        if gid in _bass_on:
            _bass_on.discard(gid)
            filters.equalizer.reset()
            msg = "🎸 Bass boost **disabled**."
        else:
            _bass_on.add(gid)
            filters.equalizer.set(bands=BASS_BANDS)
            msg = "🎸 Bass boost **enabled**!"
        await player.set_filters(filters)
        await ctx.send(embed=discord.Embed(description=msg, color=ACCENT))

    # ------------------------------------------------------------------ #

    @commands.hybrid_command(name="nightcore", description="Toggle nightcore (speed + pitch up).")
    @commands.guild_only()
    async def nightcore(self, ctx: commands.Context) -> None:
        await ctx.defer()
        player = await self._get_player(ctx)
        if player is None:
            return
        gid = ctx.guild.id  # type: ignore[union-attr]
        filters: wavelink.Filters = player.filters
        if gid in _nightcore_on:
            _nightcore_on.discard(gid)
            filters.timescale.reset()
            msg = "🌙 Nightcore **disabled**."
        else:
            _nightcore_on.add(gid)
            filters.timescale.set(speed=1.3, pitch=1.3, rate=1.0)
            msg = "🌙 Nightcore **enabled**!"
        await player.set_filters(filters)
        await ctx.send(embed=discord.Embed(description=msg, color=ACCENT))

    # ------------------------------------------------------------------ #

    @commands.hybrid_command(name="filter", description="Apply an audio filter preset.")
    @app_commands.describe(name="Choose a preset filter to apply")
    @app_commands.choices(name=PRESET_CHOICES)
    @commands.guild_only()
    async def filter_cmd(self, ctx: commands.Context, name: str) -> None:
        await ctx.defer()
        player = await self._get_player(ctx)
        if player is None:
            return
        name = name.lower().strip()
        if name not in VALID_PRESETS:
            await ctx.send(
                f"❌ Unknown preset. Choose from: {', '.join(f'`{p}`' for p in VALID_PRESETS)}",
                ephemeral=True,
            )
            return

        gid = ctx.guild.id  # type: ignore[union-attr]
        filters: wavelink.Filters = player.filters

        if name == "bassboost":
            _bass_on.add(gid)
            filters.equalizer.set(bands=BASS_BANDS)
            msg = "🎸 Bass boost applied!"
        elif name == "nightcore":
            _nightcore_on.add(gid)
            filters.timescale.set(speed=1.3, pitch=1.3, rate=1.0)
            msg = "🌙 Nightcore applied!"
        elif name == "vaporwave":
            _nightcore_on.discard(gid)
            filters.timescale.set(speed=0.8, pitch=0.8, rate=1.0)
            filters.equalizer.set(bands=[{"band": 0, "gain": 0.3}, {"band": 1, "gain": 0.3}])
            msg = "🌊 Vaporwave applied!"
        elif name == "karaoke":
            filters.karaoke.set(
                level=1.0, mono_level=1.0, filter_band=220.0, filter_width=100.0
            )
            msg = "🎤 Karaoke applied!"
        elif name == "8d":
            filters.rotation.set(rotation_hz=0.2)
            msg = "🎧 8D audio applied!"
        else:  # reset
            _bass_on.discard(gid)
            _nightcore_on.discard(gid)
            filters = wavelink.Filters()
            msg = "✅ All filters reset."

        await player.set_filters(filters)
        await ctx.send(embed=discord.Embed(description=msg, color=ACCENT))


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AudioCommands(bot))
