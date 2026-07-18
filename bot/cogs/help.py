from __future__ import annotations

import time

import discord
from discord.ext import commands

ACCENT = 0xC193CC  # #c193cc
MENTION_COLOR = 0xD1ABED  # #d1abed  — premium mention embed

# Asset URLs used by the mention embed
_MENTION_AUTHOR_ICON = (
    "https://cdn.discordapp.com/attachments/1514986569789083810/"
    "1527886411921887333/Crown_Purple.gif"
    "?ex=6a5c4a9b&is=6a5af91b&hm=cc1a0634ff1e3dc055bb89cd9f6808e6649b50c506bc4504bfaffab95596ab21&"
)
_MENTION_THUMBNAIL = (
    "https://cdn.discordapp.com/attachments/1514986569789083810/"
    "1527899204628910223/New_Project_2000_88A3346.gif"
    "?ex=6a5c5685&is=6a5b0505&hm=1fd72a067ffc72300155c5ac79cf6414030d0822462422a2c09cb30ccae60442&"
)
_MENTION_FOOTER_ICON = (
    "https://cdn.discordapp.com/attachments/1514986569789083810/"
    "1527887674072105031/purple_arrow.png"
    "?ex=6a5c4bc8&is=6a5afa48&hm=b1cdb5daf52cb78c6ae83f568d3fde63084e04848beec520f507336d89ba49e5&"
)

# Per-user cooldown: user_id → unix timestamp of last mention reply
_mention_cooldown: dict[int, float] = {}
_MENTION_COOLDOWN_SECS = 10

# ------------------------------------------------------------------ #
#  Application emoji cache
#  Populated once on first /help call via bot.fetch_application_emojis().
#  Maps emoji name → formatted string, e.g. "playback" → "<:playback:123>"
# ------------------------------------------------------------------ #
_emoji_cache: dict[str, str] = {}
_emoji_cache_loaded: bool = False


async def _load_app_emojis(bot: commands.Bot) -> None:
    """Fetch and cache all application emojis by name (runs once)."""
    global _emoji_cache_loaded
    if _emoji_cache_loaded:
        return
    try:
        emojis = await bot.fetch_application_emojis()
        for emoji in emojis:
            prefix = "a" if emoji.animated else ""
            _emoji_cache[emoji.name] = f"<{prefix}:{emoji.name}:{emoji.id}>"
        _emoji_cache_loaded = True
        print(f"[help] Loaded {len(_emoji_cache)} application emoji(s).", flush=True)
    except Exception as exc:
        print(f"[help] Could not fetch application emojis: {exc}", flush=True)
        # Mark as loaded anyway so we don't retry on every call
        _emoji_cache_loaded = True


def _cat_header(emoji_key: str, display_name: str) -> str:
    """
    Return the field name for a category.
    Uses the application emoji if available; falls back to the bare display name.
    """
    emoji = _emoji_cache.get(emoji_key, "")
    if emoji:
        return f"{emoji} {display_name}"
    return display_name


# ------------------------------------------------------------------ #
#  Category definitions
#  Each tuple: (emoji_key, display_name, commands_text)
#  emoji_key must match the Discord Application Emoji name exactly.
# ------------------------------------------------------------------ #
CATEGORIES: list[tuple[str, str, str]] = [
    (
        "playback",
        "Playback",
        "`join` — Join your voice channel (`?j`)\n"
        "`leave` — Disconnect and clear the queue (`?lv`)\n"
        "`play <query>` — Play a track or playlist (`?p`)\n"
        "`pause` — Pause the current track (`?ps`)\n"
        "`resume` — Resume playback (`?res`)\n"
        "`skip` — Skip the current track\n"
        "`stop` — Stop playback and clear the queue\n"
        "`nowplaying` — Show what's currently playing (`?nwp`)",
    ),
    (
        "queue",
        "Queue",
        "`queue` — View the current queue (`?q`)\n"
        "`remove <pos>` — Remove a track from the queue\n"
        "`clearqueue` — Clear the entire queue (`?cq`)\n"
        "`shuffle` — Shuffle the queue\n"
        "`loop` — Toggle loop mode",
    ),
    (
        "audio",
        "Audio",
        "`volume <0-200>` — Set the playback volume (`?vol`)\n"
        "`bassboost` — Toggle bass boost\n"
        "`nightcore` — Toggle nightcore filter\n"
        "`filter <name>` — Apply an audio filter",
    ),
    (
        "premium",
        "Premium",
        "`247` — Enable 24/7 mode (stay in VC)\n"
        "`autoplay` — Toggle autoplay related tracks (`?ap`)\n"
        "`lyrics` — Fetch lyrics for the current track\n"
        "`history` — View recently played tracks (`?his`)\n"
        "`playlist` — Manage your saved playlists (`?pl`)\n"
        "`enhance` — Optimize the bot, refresh internal systems, and run a health check (`?en`)",
    ),
    (
        "user_lookup",
        "User Lookup",
        "`avatar [user]` — Show a user's avatar (`?av`)\n"
        "`banner [user]` — Show a user's banner\n"
        "`userinfo [user]` — Display user information (`?ui`)\n"
        "`nick [member] [name]` — Change your (or another member's) nickname\n"
        "`afk [reason]` — Set your AFK status",
    ),
    (
        "couples",
        "Couples",
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
        "games",
        "Games",
        "`rps <choice>` — Rock, paper, scissors\n"
        "`coinflip` — Flip a coin\n"
        "`dice [sides]` — Roll a dice (default 6, max 100)\n"
        "`tictactoe <user>` — Play tic-tac-toe",
    ),
    (
        "fun",
        "Fun",
        "`8ball <question>` — Ask the magic 8-ball\n"
        "`rate <thing>` — Rate anything out of 10\n"
        "`meme` — Fetch a random meme\n"
        "`fact` — Get a random fun fact",
    ),
    (
        "moderation",
        "Moderation",
        "`purge <amount>` — Delete recent messages (1–100)\n"
        "`purgeuser @user <amount>` — Delete a user's recent messages (1–100)\n"
        "`purgeall @user [amount]` — Delete all of a user's messages (up to 2000)\n"
        "`purgemi <id/link> [more…]` — Delete specific messages by ID or link",
    ),
    (
        "utility",
        "Utility",
        "`help` — Show this help menu (`?h`)\n"
        "`ping` — Check the bot's latency\n"
        "`botinfo` — Display bot information\n"
        "`stats` — View bot statistics\n"
        "`serverinfo` — Display server information (`?si`)\n"
        "`poll <question>` — Create a poll",
    ),
]


async def build_help_embed(bot: commands.Bot) -> discord.Embed:
    """Build and return the premium help embed. Shared by the command and the mention handler."""
    # Ensure application emojis are loaded (cached after first call)
    await _load_app_emojis(bot)

    avatar_url = bot.user.display_avatar.url if bot.user else None

    embed = discord.Embed(
        title="🎧 Seraph world - commands 𖹭 ֶָ֢",
        color=ACCENT,
    )

    if avatar_url:
        embed.set_thumbnail(url=avatar_url)

    for emoji_key, display_name, value in CATEGORIES:
        embed.add_field(
            name=_cat_header(emoji_key, display_name),
            value=value,
            inline=False,
        )

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

    @commands.hybrid_command(name="help", aliases=["h", "H", "Help"], description="Show the Seraph command menu.")
    async def help_command(self, ctx: commands.Context) -> None:
        await ctx.defer()
        await ctx.send(embed=await build_help_embed(self.bot))

    # ------------------------------------------------------------------ #
    #  Bare bot-mention handler  (@Seraph  →  premium mention embed)
    # ------------------------------------------------------------------ #

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        """
        When someone mentions the bot with nothing else, respond with the
        premium mention embed. Ignores bots and enforces a 10-second
        per-user cooldown so the same user can't spam-trigger it.
        """
        if message.author.bot or self.bot.user is None:
            return

        # Accept both <@ID> and the legacy <@!ID> mention formats.
        uid = self.bot.user.id
        stripped = message.content.strip()
        if stripped not in (f"<@{uid}>", f"<@!{uid}>"):
            return

        # ── Per-user cooldown ────────────────────────────────────────────
        now = time.monotonic()
        last = _mention_cooldown.get(message.author.id, 0.0)
        if now - last < _MENTION_COOLDOWN_SECS:
            return  # silently ignore — no error message to keep it clean
        _mention_cooldown[message.author.id] = now

        # ── Build premium mention embed ──────────────────────────────────
        embed = discord.Embed(
            description=(
                "Hi I'm seraph <3 multi-purpose music bot built for a fast, smooth experience. "
                "Creators: <@1332245879444340789> & <@1487483128309223614>"
            ),
            color=MENTION_COLOR,
        )
        embed.set_author(name="Seraph — Premium ✨", icon_url=_MENTION_AUTHOR_ICON)
        embed.set_thumbnail(url=_MENTION_THUMBNAIL)
        embed.set_footer(
            text="Use `?help` or `/help` to explore all my commands.",
            icon_url=_MENTION_FOOTER_ICON,
        )

        await message.channel.send(embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Help(bot))
