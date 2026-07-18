from __future__ import annotations

import asyncio
import hashlib
import io
import random
import time

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands

# Banner generator — imported lazily so a missing Pillow doesn't crash the cog
try:
    from utils.ship_banner import generate as _gen_banner
    _BANNER_OK = True
except Exception as _banner_import_err:
    _BANNER_OK = False
    print(f"[couples] ship_banner unavailable: {_banner_import_err}", flush=True)

ACCENT = 0xC193CC

# In-memory marriage store: {guild_id: {user_id: spouse_id}}
_marriages: dict[int, dict[int, int]] = {}


# ------------------------------------------------------------------ #
#  GIF fetching — multi-source with pool cache + Giphy fallbacks
# ------------------------------------------------------------------ #

# nekos.best — returns {"results": [{"url": "..."}]}
# Requires a descriptive User-Agent; without it the server returns 403.
_NEKOS_BEST = "https://nekos.best/api/v2/{action}"
_BOT_UA     = "Seraph-DiscordBot/1.0 (Discord couples & music bot; +https://github.com/seraph-bot)"

# waifu.pics — returns {"url": "..."} — DNS blocked on Replit datacenter IPs;
# kept as source slot 2 so it is attempted and skipped gracefully.
_WAIFU_PICS = "https://api.waifu.pics/sfw/{action}"

# waifu.im — returns {"images": [{"url": "..."}]}
_WAIFU_IM   = "https://api.waifu.im/search/?included_tags={action}&gif=true"

# Ordered (api_name, action_slug) sources to try per command.
# nekos.best is primary (works on Replit with the UA header above).
# waifu.pics / waifu.im are fallbacks tried in order.
_SOURCES: dict[str, list[tuple[str, str]]] = {
    "kiss":      [("nekos", "kiss"),     ("waifu", "kiss"),     ("waifuim", "kiss")],
    "cuddle":    [("nekos", "cuddle"),   ("waifu", "cuddle"),   ("waifuim", "cuddle")],
    "pat":       [("nekos", "pat"),      ("waifu", "pat"),      ("waifuim", "pat")],
    "wink":      [("nekos", "wink"),     ("waifu", "wink"),     ("waifuim", "wink")],
    "highfive":  [("nekos", "highfive"), ("waifu", "highfive"), ("waifuim", "highfive")],
    # marry proposal — handhold is the most romantic endpoint
    "marry":     [("nekos", "handhold"), ("waifu", "handhold"), ("waifuim", "handhold")],
    # celebrate when accepted — hug
    "marry_win": [("nekos", "hug"),      ("waifu", "hug"),      ("waifuim", "hug")],
    # divorce — closest semantic match is cry
    "divorce":   [("nekos", "cry"),      ("waifu", "cry"),      ("waifuim", "cry")],
    # ship — holding hands / romantic
    "ship":      [("nekos", "handhold"), ("waifu", "handhold"), ("waifuim", "handhold")],
}

# Guaranteed Giphy CDN fallbacks — verified reachable from Replit datacenter IPs.
# Used only when every live API source fails.
_GIPHY = "https://media.giphy.com/media/{}/giphy.gif"
_FALLBACK_GIFS: dict[str, list[str]] = {
    "kiss":      [_GIPHY.format("3o7abKhOpu0NwenH3O"), _GIPHY.format("G3va31oEEnIkM")],
    "cuddle":    [_GIPHY.format("OkJat1YNdoD3W"),      _GIPHY.format("f31DK1KpGsyMU")],
    "pat":       [_GIPHY.format("ARSp9T7wwxNcs"),      _GIPHY.format("IoP0PvbbSWGAM")],
    "wink":      [_GIPHY.format("ToMjGpx9F5ktZw8qPUQ"), _GIPHY.format("RJzm826vu7WbJvBtxX"),
                  _GIPHY.format("YA6dmVW0gfIw8")],
    "highfive":  [_GIPHY.format("l4Jz3a8jO92crUlWM")],
    "marry":     [_GIPHY.format("3o7TKP9ln2Dr6ze6f6")],
    "marry_win": [_GIPHY.format("od5H3PmEG5EVq")],
    "divorce":   [_GIPHY.format("L95W4wv8nnb9K")],
    "ship":      [_GIPHY.format("3o7abKhOpu0NwenH3O")],
}

# Pool cache: action_key → (list_of_urls, fetched_at)
# Pool holds _POOL_SIZE URLs; pick a random one per invocation.
# Refreshed after _CACHE_TTL seconds or when the pool is empty.
_gif_pool:    dict[str, list[str]] = {}
_gif_pool_ts: dict[str, float]     = {}
_CACHE_TTL  = 90.0   # seconds
_POOL_SIZE  = 8      # URLs to pre-fetch per action


async def _fetch_one(session: aiohttp.ClientSession, api: str, action: str) -> str | None:
    """Fetch a single GIF URL from nekos.best, waifu.pics, or waifu.im."""
    try:
        if api == "nekos":
            url = _NEKOS_BEST.format(action=action)
            async with session.get(
                url,
                timeout=aiohttp.ClientTimeout(total=5),
                headers={"User-Agent": _BOT_UA},
            ) as r:
                if r.status == 200:
                    data = await r.json(content_type=None)
                    results = data.get("results")
                    if results and isinstance(results, list):
                        return results[0].get("url")
        elif api == "waifu":
            url = _WAIFU_PICS.format(action=action)
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as r:
                if r.status == 200:
                    data = await r.json(content_type=None)
                    return data.get("url")
        elif api == "waifuim":
            url = _WAIFU_IM.format(action=action)
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as r:
                if r.status == 200:
                    data = await r.json(content_type=None)
                    imgs = data.get("images", [])
                    if imgs and isinstance(imgs, list):
                        return imgs[0].get("url")
    except Exception as exc:
        print(f"[gif] {api}/{action} error: {exc}", flush=True)
    return None


async def _fill_pool(
    session: aiohttp.ClientSession,
    key: str,
    sources: list[tuple[str, str]],
) -> None:
    """
    Populate the URL pool for `key` with up to _POOL_SIZE entries.
    Tries each source in order until enough URLs are gathered.
    Uses asyncio.gather so the pool fills quickly.
    """
    tasks = [_fetch_one(session, api, action) for api, action in sources
             for _ in range(_POOL_SIZE // max(len(sources), 1) + 1)]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    urls = [r for r in results if isinstance(r, str) and r.startswith("http")]
    random.shuffle(urls)
    _gif_pool[key]    = urls[:_POOL_SIZE]
    _gif_pool_ts[key] = time.monotonic()


async def _fetch_gif(session: aiohttp.ClientSession, action_key: str) -> str | None:
    """
    Return a random animated GIF URL for the given action key.

    Strategy:
      1. Return from cache pool if still fresh and non-empty.
      2. If cache is stale/empty, try each live API source in order.
      3. Kick off a background pool refill (non-blocking).
      4. If all live sources fail, return a random verified Giphy CDN URL
         so the embed always has a GIF banner.
    """
    sources = _SOURCES.get(action_key, [])
    if not sources:
        return None

    now = time.monotonic()
    pool_age = now - _gif_pool_ts.get(action_key, 0.0)
    pool     = _gif_pool.get(action_key, [])

    # ── Serve from cache pool ────────────────────────────────────────────
    if pool and pool_age < _CACHE_TTL:
        return pool.pop(random.randrange(len(pool)))

    # ── Cache is stale or exhausted — fetch one immediately ─────────────
    gif_url: str | None = None
    for api, action in sources:
        gif_url = await _fetch_one(session, api, action)
        if gif_url:
            break

    # ── Kick off background pool refill (non-blocking) ───────────────────
    if pool_age >= _CACHE_TTL or not pool:
        asyncio.create_task(_fill_pool(session, action_key, sources))

    # ── Guaranteed fallback — Giphy CDN (verified reachable on Replit) ───
    if not gif_url:
        fallbacks = _FALLBACK_GIFS.get(action_key, [])
        if fallbacks:
            gif_url = random.choice(fallbacks)
            print(f"[gif] using Giphy fallback for '{action_key}'", flush=True)

    return gif_url


# ------------------------------------------------------------------ #
#  Ship helpers  (unchanged from original)
# ------------------------------------------------------------------ #

def _stable_percent(id1: int, id2: int) -> int:
    """Deterministic 0-100 compatibility score for a pair of user IDs."""
    key    = "_".join(str(i) for i in sorted([id1, id2]))
    digest = hashlib.md5(key.encode()).hexdigest()
    return int(digest, 16) % 101


def _bar(percent: int, length: int = 10) -> str:
    filled = round(percent / 100 * length)
    return "█" * filled + "░" * (length - filled)


def _ship_label(percent: int) -> str:
    if percent < 20:
        return "💔 Not compatible at all..."
    if percent < 40:
        return "🤔 Unlikely, but who knows."
    if percent < 60:
        return "💛 Solid friendship!"
    if percent < 80:
        return "💜 A great match!"
    return "💕 A perfect match!"


# ------------------------------------------------------------------ #
#  Marriage confirmation view  (unchanged from original)
# ------------------------------------------------------------------ #

class MarryView(discord.ui.View):
    def __init__(self, proposer: discord.Member, target: discord.Member) -> None:
        super().__init__(timeout=60)
        self.proposer = proposer
        self.target   = target
        self.result: str | None = None  # "accepted" | "declined"

    def _disable_all(self) -> None:
        for child in self.children:
            if isinstance(child, discord.ui.Button):
                child.disabled = True

    @discord.ui.button(label="💍 Accept", style=discord.ButtonStyle.success)
    async def accept(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        if interaction.user.id != self.target.id:
            await interaction.response.send_message("This proposal isn't for you!", ephemeral=True)
            return
        self.result = "accepted"
        self._disable_all()
        self.stop()
        await interaction.response.edit_message(view=self)

    @discord.ui.button(label="💔 Decline", style=discord.ButtonStyle.danger)
    async def decline(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        if interaction.user.id != self.target.id:
            await interaction.response.send_message("This proposal isn't for you!", ephemeral=True)
            return
        self.result = "declined"
        self._disable_all()
        self.stop()
        await interaction.response.edit_message(view=self)


# ------------------------------------------------------------------ #
#  Cog
# ------------------------------------------------------------------ #

class Couples(commands.Cog):
    """Couples and interaction commands."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot      = bot
        self._session: aiohttp.ClientSession | None = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def cog_unload(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    # ------------------------------------------------------------------ #
    #  ship
    # ------------------------------------------------------------------ #

    @commands.hybrid_command(name="ship", description="Check the compatibility between two users.")
    @app_commands.describe(user1="First user", user2="Second user (defaults to you)")
    @commands.cooldown(1, 5, commands.BucketType.user)
    async def ship(
        self,
        ctx: commands.Context,
        user1: discord.Member,
        user2: discord.Member | None = None,
    ) -> None:
        await ctx.defer()
        a = user1
        b = user2 if user2 is not None else ctx.author  # type: ignore[assignment]

        if a.id == b.id:
            await ctx.send("❌ You can't ship someone with themselves!", ephemeral=True)
            return

        pct       = _stable_percent(a.id, b.id)
        half_a    = a.display_name[: max(1, len(a.display_name) // 2)]
        half_b    = b.display_name[max(0, len(b.display_name) // 2) :]
        ship_name = half_a + half_b

        # ── Generate premium banner ────────────────────────────────────
        # Use a per-pair seed so the same two users always see a fresh
        # random palette (seed changes each call via time component).
        banner_file: discord.File | None = None
        if _BANNER_OK:
            try:
                loop     = asyncio.get_running_loop()
                # Run CPU-bound Pillow work in a thread so the event loop
                # stays responsive.
                seed     = random.randint(0, 2**31)
                png_bytes = await loop.run_in_executor(
                    None,
                    lambda: _gen_banner(
                        name1=a.display_name,
                        name2=b.display_name,
                        pct=pct,
                        seed=seed,
                    ),
                )
                banner_file = discord.File(
                    io.BytesIO(png_bytes), filename="ship_banner.png"
                )
            except Exception as exc:
                print(f"[ship] banner generation failed: {exc}", flush=True)
                banner_file = None

        # ── Build embed ────────────────────────────────────────────────
        embed = discord.Embed(title="💘 Ship Score", color=ACCENT)
        embed.add_field(name="Couple",    value=f"{a.mention} 💞 {b.mention}", inline=False)
        embed.add_field(name="Ship Name", value=f"**{ship_name}**",             inline=True)
        embed.add_field(
            name="Compatibility",
            value=f"`[{_bar(pct)}]` **{pct}%**\n{_ship_label(pct)}",
            inline=False,
        )

        if banner_file:
            embed.set_image(url="attachment://ship_banner.png")
        else:
            # Fallback: fetch a romantic GIF from the API
            session = await self._get_session()
            gif     = await _fetch_gif(session, "ship")
            if gif:
                embed.set_image(url=gif)

        embed.set_footer(text=f"Requested by {ctx.author.display_name}")

        if banner_file:
            await ctx.send(embed=embed, file=banner_file)
        else:
            await ctx.send(embed=embed)

    # ------------------------------------------------------------------ #
    #  marry
    # ------------------------------------------------------------------ #

    @commands.hybrid_command(name="marry", description="Propose to another user.")
    @app_commands.describe(member="The person you want to marry")
    @commands.cooldown(1, 10, commands.BucketType.user)
    @commands.guild_only()
    async def marry(self, ctx: commands.Context, member: discord.Member) -> None:
        await ctx.defer()

        if member.id == ctx.author.id:
            await ctx.send("❌ You can't marry yourself!", ephemeral=True)
            return
        if member.bot:
            await ctx.send("❌ You can't marry a bot!", ephemeral=True)
            return

        store = _marriages.setdefault(ctx.guild.id, {})  # type: ignore[union-attr]

        if ctx.author.id in store:
            await ctx.send(
                f"❌ You're already married to <@{store[ctx.author.id]}>! Use `divorce` first.",
                ephemeral=True,
            )
            return
        if member.id in store:
            await ctx.send(f"❌ **{member.display_name}** is already married!", ephemeral=True)
            return

        # Fetch proposal GIF before showing the embed
        session      = await self._get_session()
        proposal_gif = await _fetch_gif(session, "marry")

        view = MarryView(ctx.author, member)  # type: ignore[arg-type]
        embed = discord.Embed(
            title="💍 Marriage Proposal",
            description=(
                f"{ctx.author.mention} is proposing to {member.mention}! 💕\n\n"
                f"{member.mention}, do you accept?"
            ),
            color=ACCENT,
        )
        if proposal_gif:
            embed.set_image(url=proposal_gif)
        msg = await ctx.send(embed=embed, view=view)
        await view.wait()

        if view.result == "accepted":
            store[ctx.author.id] = member.id
            store[member.id]     = ctx.author.id
            accepted_gif = await _fetch_gif(session, "marry_win")
            result_embed = discord.Embed(
                title="💒 Just Married!",
                description=(
                    f"🎊 {ctx.author.mention} and {member.mention} are now married! "
                    f"Congratulations! 💕"
                ),
                color=ACCENT,
            )
            if accepted_gif:
                result_embed.set_image(url=accepted_gif)
        elif view.result == "declined":
            result_embed = discord.Embed(
                title="💔 Proposal Declined",
                description=f"{member.mention} said no...",
                color=ACCENT,
            )
        else:
            result_embed = discord.Embed(
                title="⏰ Proposal Expired",
                description=f"{member.mention} didn't respond in time.",
                color=ACCENT,
            )

        await msg.edit(embed=result_embed, view=view)

    # ------------------------------------------------------------------ #
    #  divorce
    # ------------------------------------------------------------------ #

    @commands.hybrid_command(name="divorce", description="Divorce your current spouse.")
    @commands.cooldown(1, 10, commands.BucketType.user)
    @commands.guild_only()
    async def divorce(self, ctx: commands.Context) -> None:
        await ctx.defer()
        store = _marriages.get(ctx.guild.id, {})  # type: ignore[union-attr]

        if ctx.author.id not in store:
            await ctx.send("❌ You're not married to anyone!", ephemeral=True)
            return

        spouse_id = store.pop(ctx.author.id)
        store.pop(spouse_id, None)

        session = await self._get_session()
        gif     = await _fetch_gif(session, "divorce")

        embed = discord.Embed(
            title="💔 Divorced",
            description=(
                f"{ctx.author.mention} and <@{spouse_id}> are no longer married."
            ),
            color=ACCENT,
        )
        if gif:
            embed.set_image(url=gif)
        await ctx.send(embed=embed)

    # ------------------------------------------------------------------ #
    #  GIF interaction commands — shared helper
    # ------------------------------------------------------------------ #

    async def _gif_cmd(
        self,
        ctx: commands.Context,
        member: discord.Member,
        gif_key: str,
        title: str,
        description: str,
    ) -> None:
        await ctx.defer()
        session = await self._get_session()
        gif     = await _fetch_gif(session, gif_key)
        embed   = discord.Embed(title=title, description=description, color=ACCENT)
        if gif:
            embed.set_image(url=gif)
        embed.set_footer(text=f"Requested by {ctx.author.display_name}")
        await ctx.send(embed=embed)

    # ------------------------------------------------------------------ #

    @commands.hybrid_command(name="kiss", description="Kiss someone! 💋")
    @app_commands.describe(member="Who to kiss")
    @commands.cooldown(1, 5, commands.BucketType.user)
    @commands.guild_only()
    async def kiss(self, ctx: commands.Context, member: discord.Member) -> None:
        await self._gif_cmd(ctx, member, "kiss", "💋 Kiss",
                            f"{ctx.author.mention} kissed {member.mention}! 💋")

    @commands.hybrid_command(name="cuddle", description="Cuddle someone! 🤗")
    @app_commands.describe(member="Who to cuddle")
    @commands.cooldown(1, 5, commands.BucketType.user)
    @commands.guild_only()
    async def cuddle(self, ctx: commands.Context, member: discord.Member) -> None:
        await self._gif_cmd(ctx, member, "cuddle", "🤗 Cuddle",
                            f"{ctx.author.mention} cuddled {member.mention}! 🤗")

    @commands.hybrid_command(name="pat", description="Pat someone! 🥰")
    @app_commands.describe(member="Who to pat")
    @commands.cooldown(1, 5, commands.BucketType.user)
    @commands.guild_only()
    async def pat(self, ctx: commands.Context, member: discord.Member) -> None:
        await self._gif_cmd(ctx, member, "pat", "🥰 Pat",
                            f"{ctx.author.mention} gave {member.mention} a pat! 🥰")

    @commands.hybrid_command(name="wink", description="Wink at someone! 😉")
    @app_commands.describe(member="Who to wink at")
    @commands.cooldown(1, 5, commands.BucketType.user)
    @commands.guild_only()
    async def wink(self, ctx: commands.Context, member: discord.Member) -> None:
        await self._gif_cmd(ctx, member, "wink", "😉 Wink",
                            f"{ctx.author.mention} winked at {member.mention}! 😏")

    @commands.hybrid_command(name="highfive", description="High five someone! 🙌")
    @app_commands.describe(member="Who to high five")
    @commands.cooldown(1, 5, commands.BucketType.user)
    @commands.guild_only()
    async def highfive(self, ctx: commands.Context, member: discord.Member) -> None:
        await self._gif_cmd(ctx, member, "highfive", "🙌 High Five",
                            f"{ctx.author.mention} high-fived {member.mention}! 🙌")


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Couples(bot))
