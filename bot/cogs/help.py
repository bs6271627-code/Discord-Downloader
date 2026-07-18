from __future__ import annotations

import discord
from discord.ext import commands

ACCENT = 0xC193CC  # #c193cc

# Each tuple: (category header, commands text)
CATEGORIES: list[tuple[str, str]] = [
    (
        "ꪆ Playback ৻",
        "`join` — Join your voice channel\n"
        "`leave` — Disconnect and clear the queue\n"
        "`play <query>` — Play a track or playlist\n"
        "`pause` — Pause the current track\n"
        "`resume` — Resume playback\n"
        "`skip` — Skip the current track\n"
        "`stop` — Stop playback and clear the queue\n"
        "`nowplaying` — Show what's currently playing",
    ),
    (
        "ꪆ Queue ৻",
        "`queue` — View the current queue\n"
        "`remove <pos>` — Remove a track from the queue\n"
        "`clearqueue` — Clear the entire queue\n"
        "`shuffle` — Shuffle the queue\n"
        "`loop` — Toggle loop mode",
    ),
    (
        "ꪆ Audio ৻",
        "`volume <0-200>` — Set the playback volume\n"
        "`bassboost` — Toggle bass boost\n"
        "`nightcore` — Toggle nightcore filter\n"
        "`filter <name>` — Apply an audio filter",
    ),
    (
        "ꪆ Premium ৻",
        "`247` — Enable 24/7 mode (stay in VC)\n"
        "`autoplay` — Toggle autoplay related tracks\n"
        "`lyrics` — Fetch lyrics for the current track\n"
        "`history` — View recently played tracks\n"
        "`playlist` — Manage your saved playlists",
    ),
    (
        "ꪆ User Lookup ৻",
        "`avatar [user]` — Show a user's avatar\n"
        "`banner [user]` — Show a user's banner\n"
        "`userinfo [user]` — Display user information\n"
        "`nick [name]` — Change your nickname\n"
        "`afk [reason]` — Set your AFK status",
    ),
    (
        "ꪆ Couples ৻",
        "`ship <user1> [user2]` — Check compatibility between two users\n"
        "`marry <user>` — Propose to someone\n"
        "`divorce` — End your marriage\n"
        "`kiss <user>` — Kiss someone\n"
        "`cuddle <user>` — Cuddle someone\n"
        "`pat <user>` — Pat someone\n"
        "`wink <user>` — Wink at someone\n"
        "`highfive <user>` — High five someone",
    ),
    (
        "ꪆ Games ৻",
        "`rps <choice>` — Rock, paper, scissors\n"
        "`coinflip` — Flip a coin\n"
        "`dice [sides]` — Roll a dice (default 6, max 100)\n"
        "`tictactoe <user>` — Play tic-tac-toe",
    ),
    (
        "ꪆ Fun ৻",
        "`8ball <question>` — Ask the magic 8-ball\n"
        "`rate <thing>` — Rate anything out of 10\n"
        "`meme` — Fetch a random meme\n"
        "`fact` — Get a random fun fact",
    ),
    (
        "ꪆ Utility ৻",
        "`help` — Show this help menu\n"
        "`ping` — Check the bot's latency\n"
        "`botinfo` — Display bot information\n"
        "`stats` — View bot statistics\n"
        "`serverinfo` — Display server information\n"
        "`poll <question>` — Create a poll",
    ),
]


def build_help_embed(bot: commands.Bot) -> discord.Embed:
    """Build and return the premium help embed. Shared by the command and the mention handler."""
    avatar_url = bot.user.display_avatar.url if bot.user else None

    embed = discord.Embed(
        title="🎧 Seraph world - commands 𖹭 ֶָ֢",
        color=ACCENT,
    )

    if avatar_url:
        embed.set_thumbnail(url=avatar_url)

    for name, value in CATEGORIES:
        embed.add_field(name=name, value=value, inline=False)

    embed.set_footer(
        text="Made by nova408  •  Use ? or / prefix, or mention @Seraph.",
        icon_url=avatar_url,
    )
    return embed


class Help(commands.Cog):
    """Custom premium help command."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    # ------------------------------------------------------------------ #
    #  Help command  (?help / /help)
    # ------------------------------------------------------------------ #

    @commands.hybrid_command(name="help", description="Show the Seraph command menu.")
    async def help_command(self, ctx: commands.Context) -> None:
        await ctx.defer()
        await ctx.send(embed=build_help_embed(self.bot))

    # ------------------------------------------------------------------ #
    #  Bare bot-mention handler  (@Seraph  →  help embed)
    # ------------------------------------------------------------------ #

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        """
        When someone mentions the bot with nothing else, respond with the
        help embed — the same one produced by ?help / /help.
        """
        if message.author.bot or self.bot.user is None:
            return

        # Accept both <@ID> and the legacy <@!ID> mention formats.
        uid = self.bot.user.id
        stripped = message.content.strip()
        if stripped not in (f"<@{uid}>", f"<@!{uid}>"):
            return

        await message.channel.send(embed=build_help_embed(self.bot))


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Help(bot))
