from __future__ import annotations

import hashlib
import random

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands

ACCENT = 0xC193CC

EIGHT_BALL = [
    # Positive
    "It is certain. ✨",
    "Without a doubt. ✨",
    "Yes, definitely! ✨",
    "You may rely on it. ✨",
    "Most likely. 🌸",
    "Signs point to yes. 🌸",
    "As I see it, yes. 🌸",
    "Outlook good. 🌸",
    # Neutral
    "Ask again later. 🌙",
    "Better not tell you now. 🌙",
    "Cannot predict now. 🌙",
    "Concentrate and ask again. 🌙",
    # Negative
    "Don't count on it. 💫",
    "My reply is no. 💫",
    "My sources say no. 💫",
    "Very doubtful. 💫",
    "Outlook not so good. 💫",
]


def _stable_score(text: str) -> int:
    """Return a consistent 0-10 score for the given text."""
    digest = hashlib.md5(text.lower().encode()).hexdigest()
    return int(digest, 16) % 11


class Fun(commands.Cog):
    """Fun commands: 8ball, rate, meme, fact."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._session: aiohttp.ClientSession | None = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def cog_unload(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    # ------------------------------------------------------------------ #

    @commands.hybrid_command(name="8ball", description="Ask the magic 8-ball a question.")
    @app_commands.describe(question="Your question")
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def eight_ball(self, ctx: commands.Context, *, question: str) -> None:
        await ctx.defer()
        answer = random.choice(EIGHT_BALL)
        embed = discord.Embed(title="🎱 Magic 8-Ball", color=ACCENT)
        embed.add_field(name="Question", value=question, inline=False)
        embed.add_field(name="Answer", value=f"**{answer}**", inline=False)
        await ctx.send(embed=embed)

    # ------------------------------------------------------------------ #

    @commands.hybrid_command(name="rate", aliases=["Rate"], description="Rate anything out of 10.")
    @app_commands.describe(thing="What to rate")
    @commands.cooldown(1, 3, commands.BucketType.user)
    async def rate(self, ctx: commands.Context, *, thing: str) -> None:
        await ctx.defer()
        score = _stable_score(thing)
        bar = "█" * score + "░" * (10 - score)

        if score <= 2:
            verdict = "💀 Absolutely terrible."
        elif score <= 4:
            verdict = "😬 Not great..."
        elif score <= 6:
            verdict = "🤔 Pretty average."
        elif score <= 8:
            verdict = "😊 Quite good!"
        else:
            verdict = "🌟 Outstanding!"

        embed = discord.Embed(title="⭐ Rating", color=ACCENT)
        embed.add_field(name="Thing", value=thing, inline=False)
        embed.add_field(
            name="Score",
            value=f"`[{bar}]` **{score}/10** — {verdict}",
            inline=False,
        )
        await ctx.send(embed=embed)

    # ------------------------------------------------------------------ #

    @commands.hybrid_command(name="meme", aliases=["Meme"], description="Get a random meme.")
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def meme(self, ctx: commands.Context) -> None:
        await ctx.defer()
        session = await self._get_session()
        try:
            async with session.get(
                "https://meme-api.com/gimme",
                timeout=aiohttp.ClientTimeout(total=8),
            ) as resp:
                if resp.status != 200:
                    await ctx.send("❌ Couldn't fetch a meme right now — try again later.", ephemeral=True)
                    return
                data = await resp.json()
        except Exception:
            await ctx.send("❌ Couldn't fetch a meme right now — try again later.", ephemeral=True)
            return

        embed = discord.Embed(
            title=data.get("title", "Meme"),
            url=data.get("postLink"),
            color=ACCENT,
        )
        embed.set_image(url=data.get("url"))
        embed.set_footer(text=f"👍 {data.get('ups', 0)} · r/{data.get('subreddit', 'memes')}")
        await ctx.send(embed=embed)

    # ------------------------------------------------------------------ #

    @commands.hybrid_command(name="fact", aliases=["Fact"], description="Get a random fun fact.")
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def fact(self, ctx: commands.Context) -> None:
        await ctx.defer()
        session = await self._get_session()
        try:
            async with session.get(
                "https://uselessfacts.jsph.pl/api/v2/facts/random",
                params={"language": "en"},
                timeout=aiohttp.ClientTimeout(total=8),
            ) as resp:
                if resp.status != 200:
                    await ctx.send("❌ Couldn't fetch a fact right now — try again later.", ephemeral=True)
                    return
                data = await resp.json()
        except Exception:
            await ctx.send("❌ Couldn't fetch a fact right now — try again later.", ephemeral=True)
            return

        embed = discord.Embed(
            title="💡 Random Fact",
            description=data.get("text", "No fact found."),
            color=ACCENT,
        )
        embed.set_footer(text="uselessfacts.jsph.pl")
        await ctx.send(embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Fun(bot))
