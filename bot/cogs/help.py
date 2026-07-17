from __future__ import annotations

import discord
from discord.ext import commands

ACCENT = 0xC193CC  # #c193cc

# Each tuple: (category header, commands text)
CATEGORIES: list[tuple[str, str]] = [
    (
        "✰ Playback",
        "`join` — Join your voice channel\n"
        "`leave` — Disconnect and clear the queue\n"
        "`play <query>` — Play a track or playlist\n"
        "`pause` — Pause the current track\n"
        "`resume` — Resume playback\n"
        "`skip` — Skip the current track\n"
        "`stop` — Stop playback and clear the queue",
    ),
    (
        "✰ Queue",
        "`queue` — View the current queue\n"
        "`nowplaying` — Show what's currently playing",
    ),
    (
        "✰ Audio",
        "*Coming soon…*",
    ),
    (
        "✰ Premium",
        "*Coming soon…*",
    ),
    (
        "✰ User Lookup",
        "*Coming soon…*",
    ),
    (
        "✰ Couples",
        "*Coming soon…*",
    ),
    (
        "✰ Games",
        "*Coming soon…*",
    ),
    (
        "✰ Fun",
        "*Coming soon…*",
    ),
    (
        "✰ Utility",
        "`help` — Show this help menu",
    ),
]


class Help(commands.Cog):
    """Custom premium help command."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @commands.hybrid_command(name="help", description="Show the Seraph command menu.")
    async def help_command(self, ctx: commands.Context) -> None:
        await ctx.defer()

        avatar_url = (
            self.bot.user.display_avatar.url if self.bot.user else None
        )

        embed = discord.Embed(
            title="🎧 Seraph world - commands 𖹭 ֶָ֢",
            color=ACCENT,
        )

        if avatar_url:
            embed.set_thumbnail(url=avatar_url)

        for name, value in CATEGORIES:
            embed.add_field(name=name, value=value, inline=False)

        embed.set_footer(
            text="Made by nova408  •  Use ?help <command> for more information.",
            icon_url=avatar_url,
        )

        await ctx.send(embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Help(bot))
