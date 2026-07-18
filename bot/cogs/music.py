from __future__ import annotations

import asyncio
import re
import xml.etree.ElementTree as ET
from urllib.parse import parse_qs, quote, urlparse

import aiohttp
import discord
import wavelink
from discord import app_commands
from discord.ext import commands


# ---------------------------------------------------------------------------
# URL detection patterns
# ---------------------------------------------------------------------------

# YouTube: watch, playlist, shorts pages + youtu.be short links
_YT_RE = re.compile(
    r"https?://(?:www\.)?(?:youtube\.com/(?:watch|playlist|shorts)\S*|youtu\.be/\S+)",
    re.IGNORECASE,
)

# Spotify: open.spotify.com tracks/albums/playlists + spotify.link short URLs
_SPOTIFY_RE = re.compile(
    r"https?://(?:open\.spotify\.com/(?:track|album|playlist|episode)|spotify\.link)\S*",
    re.IGNORECASE,
)

# Any other http/https URL
_URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)

_PLAY_COLOR = 0xD1ABED
_MAX_PLAYLIST = 25  # max tracks queued from a YouTube playlist

_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}


# ---------------------------------------------------------------------------
# Metadata resolvers  (return None / [] on any failure — callers handle it)
# ---------------------------------------------------------------------------

async def _youtube_oembed(
    session: aiohttp.ClientSession, url: str
) -> tuple[str, str] | None:
    """
    Resolve a YouTube URL to (title, author_name) using YouTube's own oEmbed API.

    The oEmbed endpoint uses different infrastructure from the video player and
    works reliably from datacenter IPs even when the player is blocked.

    Falls back to parsing ytInitialData from the page HTML when oEmbed returns
    a 404 (e.g. age-restricted or unavailable videos).

    Returns (title, author_name) on success, None if the video cannot be resolved.
    """
    # ── Primary: YouTube oEmbed API ──────────────────────────────────────
    try:
        oe_url = f"https://www.youtube.com/oembed?url={quote(url)}&format=json"
        async with session.get(
            oe_url,
            headers=_BROWSER_HEADERS,
            timeout=aiohttp.ClientTimeout(total=8),
        ) as r:
            if r.status == 200:
                d = await r.json(content_type=None)
                title = (d.get("title") or "").strip()
                author = (d.get("author_name") or "").strip()
                if title:
                    return (title, author)
    except Exception:
        pass

    # ── Fallback: ytInitialData runs extraction from page HTML ────────────
    try:
        async with session.get(
            url,
            headers=_BROWSER_HEADERS,
            timeout=aiohttp.ClientTimeout(total=10),
            allow_redirects=True,
        ) as r:
            if r.status != 200:
                return None
            html = await r.text(errors="replace")

        # "title":{"runs":[{"text":"Alan Walker - Faded"
        m = re.search(
            r'"title"\s*:\s*\{"runs"\s*:\s*\[\{"text"\s*:\s*"([^"]{3,200})"',
            html,
        )
        if m:
            title = m.group(1).strip()
            _junk = ("want to watch", "sign in", "youtube", "subscribe")
            if title and not any(j in title.lower() for j in _junk):
                return (title, "")
    except Exception:
        pass

    return None


async def _spotify_oembed(
    session: aiohttp.ClientSession, url: str
) -> dict | None:
    """
    Call Spotify's public oEmbed endpoint — no API key or login needed.
    Returns {"title": "Track Name", "author_name": "Artist", ...} or None.
    Works for tracks, albums, and playlists.
    """
    try:
        async with session.get(
            f"https://open.spotify.com/oembed?url={quote(url)}",
            headers=_BROWSER_HEADERS,
            timeout=aiohttp.ClientTimeout(total=8),
        ) as r:
            if r.status == 200:
                return await r.json(content_type=None)
    except Exception:
        pass
    return None


async def _youtube_rss_titles(
    session: aiohttp.ClientSession, playlist_id: str
) -> list[str]:
    """
    Fetch YouTube playlist track titles via the public Atom/RSS feed.
    Requires no auth or API key — it is a standard public endpoint.
    Returns up to _MAX_PLAYLIST titles in playlist order.
    """
    feed_url = (
        f"https://www.youtube.com/feeds/videos.xml?playlist_id={playlist_id}"
    )
    try:
        async with session.get(
            feed_url,
            headers=_BROWSER_HEADERS,
            timeout=aiohttp.ClientTimeout(total=10),
        ) as r:
            if r.status != 200:
                return []
            xml_text = await r.text()
        root = ET.fromstring(xml_text)
        ns = {"atom": "http://www.w3.org/2005/Atom"}
        titles: list[str] = []
        for entry in root.findall("atom:entry", ns)[:_MAX_PLAYLIST]:
            el = entry.find("atom:title", ns)
            if el is not None and el.text:
                titles.append(el.text.strip())
        return titles
    except Exception:
        return []


def _yt_video_id(url: str) -> str | None:
    p = urlparse(url)
    if p.hostname in ("youtu.be",):
        return p.path.lstrip("/").split("?")[0] or None
    return parse_qs(p.query).get("v", [None])[0]


def _yt_playlist_id(url: str) -> str | None:
    return parse_qs(urlparse(url).query).get("list", [None])[0]


def _spotify_type_id(url: str) -> tuple[str, str] | None:
    """Return (content_type, spotify_id) or None for any Spotify URL."""
    m = re.search(r"open\.spotify\.com/([a-z]+)/([A-Za-z0-9]+)", url)
    return (m.group(1), m.group(2)) if m else None


async def _sc_search(query: str) -> wavelink.Playable | None:
    """Search SoundCloud and return the best single result, or None."""
    try:
        results: wavelink.Search = await wavelink.Playable.search(
            query, source=wavelink.TrackSource.SoundCloud
        )
        return results[0] if results else None
    except Exception:
        return None


async def _sc_search_many(queries: list[str]) -> list[wavelink.Playable]:
    """
    Search SoundCloud for every query concurrently (max 5 parallel),
    returning found tracks in the original order.
    """
    sem = asyncio.Semaphore(5)

    async def _one(q: str) -> wavelink.Playable | None:
        async with sem:
            return await _sc_search(q)

    results = await asyncio.gather(*[_one(q) for q in queries], return_exceptions=True)
    return [r for r in results if isinstance(r, wavelink.Playable)]


def _error_embed(title: str, description: str, footer: str = "") -> discord.Embed:
    e = discord.Embed(title=title, description=description, color=_PLAY_COLOR)
    if footer:
        e.set_footer(text=footer)
    return e


# ---------------------------------------------------------------------------
# Cog
# ---------------------------------------------------------------------------

class Music(commands.Cog):
    """
    All music commands, powered by Wavelink + Lavalink.

    Each command is a hybrid_command so it registers as a slash command (/play),
    a prefix command (?play), and a mention command (@Bot play) from a single
    implementation — no logic is duplicated.

    Audio source routing (YouTube datacenter IPs are blocked):
      • YouTube video  → fetch og:title from page → SoundCloud search
      • YouTube playlist → public Atom/RSS feed → SoundCloud search per track
      • Spotify track  → public oEmbed API → SoundCloud search (title + artist)
      • Spotify album/playlist → oEmbed name + helpful limitation message
      • Other URL      → pass to Lavalink http source directly
      • Text query     → SoundCloud search (fastest, most reliable)
    """

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
    #  Wavelink event listeners
    # ------------------------------------------------------------------ #

    @commands.Cog.listener()
    async def on_wavelink_node_ready(
        self, payload: wavelink.NodeReadyEventPayload
    ) -> None:
        print(
            f"[wavelink] Node ready: {payload.node.identifier!r} "
            f"| resumed={payload.resumed}",
            flush=True,
        )

    @commands.Cog.listener()
    async def on_wavelink_track_start(
        self, payload: wavelink.TrackStartEventPayload
    ) -> None:
        player: wavelink.Player = payload.player
        track: wavelink.Playable = payload.track
        channel: discord.TextChannel | None = getattr(player, "home", None)
        # A new track started — reset the queue-ended flag so a future empty
        # queue will produce a fresh notification rather than being silenced.
        player._queue_ended_sent = False  # type: ignore[attr-defined]
        if channel is None:
            return
        await channel.send(embed=_now_playing_embed(track))

    @commands.Cog.listener()
    async def on_wavelink_track_end(
        self, payload: wavelink.TrackEndEventPayload
    ) -> None:
        """
        Send a "Queue Ended" embed exactly once when playback genuinely
        exhausts the queue.

        Guards:
        • reason must be "finished" — skips ("replaced") and manual stops
          ("stopped") are excluded.
        • AutoPlayMode.enabled (recommendation mode) will add more tracks,
          so we stay silent.
        • 24/7 mode keeps the player alive intentionally — stay silent.
        • asyncio.sleep(0.5) lets Wavelink's own queue-advancement logic run
          before we inspect player.playing / queue.is_empty, avoiding a race
          where we'd fire mid-playlist.
        • _queue_ended_sent flag deduplicates across reconnects or any event
          that fires multiple times for the same idle session.
        """
        # Only act on natural track completion, not skips / stop / cleanup.
        if payload.reason != "finished":
            return

        player: wavelink.Player | None = payload.player
        if player is None:
            return

        # AutoPlayMode.enabled adds recommendation tracks — queue isn't "done".
        if player.autoplay == wavelink.AutoPlayMode.enabled:
            return

        # 24/7 mode keeps the session alive — suppress the notification.
        if getattr(player, "twentyfour_seven", False):
            return

        # Give Wavelink a moment to advance to the next queued track (if any).
        await asyncio.sleep(0.5)

        # If the player is now playing, or the queue still has items, a next
        # track was already started — this was a normal between-song transition.
        if player.playing or not player.queue.is_empty:
            return

        # Deduplicate: only send once per idle session.
        if getattr(player, "_queue_ended_sent", False):
            return
        player._queue_ended_sent = True  # type: ignore[attr-defined]

        channel: discord.TextChannel | None = getattr(player, "home", None)
        if channel is None:
            return

        embed = discord.Embed(
            title="🎵 Queue Ended",
            description=(
                "The music queue has finished and there are no more songs to play.\n\n"
                "Use `?play <song>` or `/play <song>` to start listening again."
            ),
            color=_PLAY_COLOR,
        )
        embed.set_footer(text="Thanks for listening! ✨")
        await channel.send(embed=embed)

    @commands.Cog.listener()
    async def on_wavelink_track_exception(
        self, payload: wavelink.TrackExceptionEventPayload
    ) -> None:
        player: wavelink.Player = payload.player
        channel: discord.TextChannel | None = getattr(player, "home", None)
        if channel:
            await channel.send(
                f"⚠️ Playback error for **{payload.track.title}**: "
                f"{payload.exception.get('message', 'unknown error')}",
            )

    @commands.Cog.listener()
    async def on_wavelink_inactive_player(
        self, player: wavelink.Player
    ) -> None:
        """Disconnect after inactivity, unless 24/7 mode is on."""
        if getattr(player, "twentyfour_seven", False):
            return
        await player.disconnect()

    # ------------------------------------------------------------------ #
    #  Internal helpers
    # ------------------------------------------------------------------ #

    async def _get_player(
        self,
        ctx: commands.Context,
        *,
        join: bool = False,
    ) -> wavelink.Player | None:
        player: wavelink.Player | None = ctx.guild.voice_client  # type: ignore[assignment]

        if player is None:
            if not join:
                await ctx.send(
                    "❌ I'm not in a voice channel. Use `join` first.",
                    ephemeral=True,
                )
                return None
            if ctx.author.voice is None:  # type: ignore[union-attr]
                await ctx.send(
                    "❌ You must be in a voice channel first.", ephemeral=True
                )
                return None
            player = await ctx.author.voice.channel.connect(cls=wavelink.Player)  # type: ignore[union-attr]
            player.home = ctx.channel  # type: ignore[attr-defined]
            player.autoplay = wavelink.AutoPlayMode.partial

        return player

    def _queue_msg(
        self,
        track: wavelink.Playable,
        player: wavelink.Player,
        *,
        is_loading: bool = False,
    ) -> str:
        name = discord.utils.escape_markdown(track.title)
        if is_loading:
            return f"🎵 Loading **{name}**…"
        return (
            f"➕ Added **{name}** to the queue "
            f"(position **#{len(player.queue)}**)."
        )

    # ------------------------------------------------------------------ #
    #  Commands  (hybrid = slash /cmd + prefix ?cmd + mention @Bot cmd)
    # ------------------------------------------------------------------ #

    @commands.hybrid_command(name="join", description="Join your current voice channel.")
    async def join(self, ctx: commands.Context) -> None:
        await ctx.defer(ephemeral=True)

        if ctx.author.voice is None:  # type: ignore[union-attr]
            await ctx.send("❌ You must be in a voice channel first.", ephemeral=True)
            return

        channel = ctx.author.voice.channel  # type: ignore[union-attr]
        player: wavelink.Player | None = ctx.guild.voice_client  # type: ignore[assignment]

        if player is not None:
            await player.move_to(channel)  # type: ignore[arg-type]
            await ctx.send(f"✅ Moved to **{channel.name}**.", ephemeral=True)
            return

        player = await channel.connect(cls=wavelink.Player)
        player.home = ctx.channel  # type: ignore[attr-defined]
        player.autoplay = wavelink.AutoPlayMode.partial
        await ctx.send(f"✅ Joined **{channel.name}**.", ephemeral=True)

    @commands.hybrid_command(
        name="leave", description="Leave the voice channel and clear the queue."
    )
    async def leave(self, ctx: commands.Context) -> None:
        await ctx.defer()
        player: wavelink.Player | None = ctx.guild.voice_client  # type: ignore[assignment]
        if player is None:
            await ctx.send("❌ I'm not in a voice channel.", ephemeral=True)
            return
        await player.disconnect()
        await ctx.send("👋 Disconnected and cleared the queue.")

    # ------------------------------------------------------------------ #
    #  play  (/play and ?play)
    # ------------------------------------------------------------------ #

    @commands.hybrid_command(
        name="play",
        description="Play a song — search by name or paste a YouTube / Spotify URL.",
    )
    @app_commands.describe(
        query="Song name, YouTube URL, Spotify URL, or any direct audio link"
    )
    async def play(self, ctx: commands.Context, *, query: str = "") -> None:
        await ctx.defer()
        query = query.strip()

        # ── No query → usage guide ────────────────────────────────────────
        if not query:
            embed = discord.Embed(
                title="🎵 Play Music",
                description="Queue a song using its name or a direct URL.",
                color=_PLAY_COLOR,
            )
            embed.add_field(
                name="Usage",
                value="`?play <song name or URL>`\n`/play <song name or URL>`",
                inline=False,
            )
            embed.add_field(
                name="Examples",
                value=(
                    "`?play Faded`\n"
                    "`?play https://youtu.be/60ItHLz5WEA`\n"
                    "`?play https://open.spotify.com/track/...`"
                ),
                inline=False,
            )
            embed.add_field(
                name="Supported Sources",
                value="🎵 Song names · 📺 YouTube · 🎧 Spotify · 🔗 Direct links",
                inline=False,
            )
            embed.set_footer(text="Tip: Song names and direct links are both supported.")
            await ctx.send(embed=embed, delete_after=5, ephemeral=True)
            return

        # ── Join voice ────────────────────────────────────────────────────
        player = await self._get_player(ctx, join=True)
        if player is None:
            return
        player.home = ctx.channel  # type: ignore[attr-defined]

        is_youtube = bool(_YT_RE.match(query))
        is_spotify = bool(_SPOTIFY_RE.match(query))
        is_url     = bool(_URL_RE.match(query))

        session = await self._get_session()
        msg: str = ""

        try:
            # ── YouTube ───────────────────────────────────────────────────
            if is_youtube:
                playlist_id = _yt_playlist_id(query)
                video_id    = _yt_video_id(query)

                if playlist_id:
                    # ── YouTube playlist ──────────────────────────────────
                    titles = await _youtube_rss_titles(session, playlist_id)

                    # If the URL also has a video ID (e.g. watch?v=…&list=…),
                    # and RSS is empty, fall back to that single video.
                    if not titles and video_id:
                        t = await _og_title(
                            session,
                            f"https://www.youtube.com/watch?v={video_id}",
                        )
                        if t:
                            titles = [t]

                    if not titles:
                        await ctx.send(
                            embed=_error_embed(
                                "❌ YouTube Playlist Unavailable",
                                "Couldn't load the playlist. It may be private or empty.\n"
                                "Try sharing individual video links instead.",
                                "Tip: Public playlists are loaded via the YouTube Atom feed.",
                            ),
                            ephemeral=True,
                        )
                        return

                    tracks = await _sc_search_many(titles)
                    if not tracks:
                        await ctx.send(
                            embed=_error_embed(
                                "❌ No Matches Found",
                                "Found the playlist on YouTube but couldn't match any tracks on SoundCloud.\n"
                                "Try searching by song name instead.",
                            ),
                            ephemeral=True,
                        )
                        return

                    for t in tracks:
                        player.queue.put(t)
                    msg = (
                        f"➕ Queued **{len(tracks)}** track{'s' if len(tracks) != 1 else ''}  "
                        f"from YouTube playlist (matched on SoundCloud)."
                    )

                else:
                    # ── YouTube single video ──────────────────────────────
                    # YouTube oEmbed API works from datacenter IPs; the player
                    # API (used by the Lavalink plugin) does not.
                    result = await _youtube_oembed(session, query)
                    if not result:
                        await ctx.send(
                            embed=_error_embed(
                                "❌ YouTube Video Unavailable",
                                "Couldn't retrieve the video title.\n"
                                "The video may be private, age-restricted, or the link is invalid.\n\n"
                                "**Try searching by name:**\n`?play Artist - Song Title`",
                                "Tip: SoundCloud search works with any song name.",
                            ),
                            ephemeral=True,
                        )
                        return

                    yt_title, yt_author = result
                    # If the channel name isn't already in the title (e.g. "Artist - Song"),
                    # append it so SoundCloud gets a more targeted query.
                    if yt_author and yt_author.lower() not in yt_title.lower():
                        search_q = f"{yt_title} {yt_author}"
                    else:
                        search_q = yt_title

                    track = await _sc_search(search_q)
                    if track is None:
                        await ctx.send(
                            embed=_error_embed(
                                "❌ No SoundCloud Match",
                                f"Couldn't match **{discord.utils.escape_markdown(yt_title)}** on SoundCloud.\n"
                                "Try a slightly different search term.",
                                "Tip: Remove featured artists or parenthetical info for better matches.",
                            ),
                            ephemeral=True,
                        )
                        return

                    player.queue.put(track)
                    msg = self._queue_msg(track, player, is_loading=not player.playing)

            # ── Spotify ───────────────────────────────────────────────────
            elif is_spotify:
                # Resolve spotify.link short URLs first via og:title fallback
                sp_info = _spotify_type_id(query)
                sp_type = sp_info[0] if sp_info else "track"

                oembed = await _spotify_oembed(session, query)

                if not oembed:
                    await ctx.send(
                        embed=_error_embed(
                            "❌ Spotify Link Unreadable",
                            "Couldn't fetch metadata for that Spotify link.\n"
                            "The link may be invalid or the content may be unavailable.\n\n"
                            "**Try searching by name instead:**\n`?play Artist - Song Title`",
                            "Tip: Individual Spotify track URLs work best.",
                        ),
                        ephemeral=True,
                    )
                    return

                if sp_type == "track":
                    # ── Spotify single track ──────────────────────────────
                    # Spotify's oEmbed only returns "title" (track name);
                    # author_name is not included in their oEmbed spec.
                    sp_title = (oembed.get("title") or "").strip()
                    if not sp_title:
                        await ctx.send(
                            embed=_error_embed(
                                "❌ Spotify Track Unreadable",
                                "Couldn't read the track name from that Spotify link.\n"
                                "Try searching by name: `?play Artist - Song Title`",
                            ),
                            ephemeral=True,
                        )
                        return

                    track = await _sc_search(sp_title)

                    if track is None:
                        await ctx.send(
                            embed=_error_embed(
                                "❌ No SoundCloud Match",
                                f"Couldn't match **{discord.utils.escape_markdown(sp_title)}** "
                                f"on SoundCloud.\n"
                                "Try searching by song name directly.",
                                "Tip: Remove remix/version info for broader matches.",
                            ),
                            ephemeral=True,
                        )
                        return

                    player.queue.put(track)
                    msg = self._queue_msg(track, player, is_loading=not player.playing)

                elif sp_type in ("album", "playlist"):
                    # ── Spotify album / playlist — no free track listing API ──
                    sp_label = sp_type.capitalize()
                    name   = oembed.get("title", "Unknown")
                    artist = oembed.get("author_name", "")

                    embed = discord.Embed(
                        title=f"🎧 Spotify {sp_label} Detected",
                        description=(
                            f"**{discord.utils.escape_markdown(name)}**"
                            + (f" · *{discord.utils.escape_markdown(artist)}*" if artist else "")
                            + "\n\n"
                            "Full track listing for Spotify albums and playlists requires "
                            "Spotify API credentials, which aren't configured yet.\n\n"
                            "**What you can do right now:**\n"
                            f"• Search for individual tracks: `?play {artist} - Song Title`\n"
                            "• Paste a single Spotify **track** URL — those work perfectly!"
                        ),
                        color=_PLAY_COLOR,
                    )
                    embed.set_footer(
                        text="Tip: Spotify track URLs → full metadata → best SoundCloud match."
                    )
                    await ctx.send(embed=embed, ephemeral=True)
                    return

                else:
                    await ctx.send(
                        embed=_error_embed(
                            "❌ Unsupported Spotify Content",
                            "Only Spotify **track**, **album**, and **playlist** URLs are supported.",
                        ),
                        ephemeral=True,
                    )
                    return

            # ── Other URL (Bandcamp, SoundCloud link, HTTP stream…) ───────
            elif is_url:
                results: wavelink.Search = await wavelink.Playable.search(query)
                if not results:
                    await ctx.send(
                        embed=_error_embed(
                            "❌ Link Could Not Be Played",
                            "That URL didn't return any audio.\n"
                            "Make sure it points to a supported source or a direct audio file.\n\n"
                            "**Supported direct sources:** SoundCloud, Bandcamp, Twitch, Vimeo, "
                            "and direct `.mp3` / `.ogg` / `.flac` links.",
                        ),
                        ephemeral=True,
                    )
                    return

                if isinstance(results, wavelink.Playlist):
                    count = len(results.tracks)
                    for t in results.tracks:
                        player.queue.put(t)
                    msg = (
                        f"➕ Added playlist **{discord.utils.escape_markdown(results.name)}** "
                        f"— **{count}** track{'s' if count != 1 else ''} queued."
                    )
                else:
                    track = results[0]
                    player.queue.put(track)
                    msg = self._queue_msg(track, player, is_loading=not player.playing)

            # ── Plain text search → SoundCloud ────────────────────────────
            else:
                results = await wavelink.Playable.search(
                    query, source=wavelink.TrackSource.SoundCloud
                )
                if not results:
                    await ctx.send(
                        embed=_error_embed(
                            "❌ No Results Found",
                            f"No tracks matched **{discord.utils.escape_markdown(query[:100])}**.\n"
                            "Try a different search term or paste a direct URL.",
                            "Tip: Song names and direct links are both supported.",
                        ),
                        ephemeral=True,
                    )
                    return

                track = results[0]
                player.queue.put(track)
                msg = self._queue_msg(track, player, is_loading=not player.playing)

        except Exception as exc:
            await ctx.send(
                embed=_error_embed(
                    "⚠️ Unexpected Error",
                    f"Something went wrong while loading that track.\n```{exc}```\n"
                    "Please try again or use a different query.",
                    "Tip: Try searching by song name if a URL isn't working.",
                ),
                ephemeral=True,
            )
            raise  # re-raise so the error appears in the bot's console log

        # ── Start playback if not already playing ─────────────────────────
        if not player.playing and not player.queue.is_empty:
            await player.play(player.queue.get(), populate=False)

        if msg:
            await ctx.send(msg)

    # ------------------------------------------------------------------ #
    #  Remaining playback commands (unchanged)
    # ------------------------------------------------------------------ #

    @commands.hybrid_command(name="pause", description="Pause the current track.")
    async def pause(self, ctx: commands.Context) -> None:
        await ctx.defer()
        player = await self._get_player(ctx)
        if player is None:
            return
        if not player.playing:
            await ctx.send("⚠️ Nothing is playing.", ephemeral=True)
            return
        if player.paused:
            await ctx.send("⚠️ Already paused.", ephemeral=True)
            return
        await player.pause(True)
        await ctx.send("⏸ Paused.")

    @commands.hybrid_command(name="resume", description="Resume the paused track.")
    async def resume(self, ctx: commands.Context) -> None:
        await ctx.defer()
        player = await self._get_player(ctx)
        if player is None:
            return
        if not player.paused:
            await ctx.send("⚠️ Not currently paused.", ephemeral=True)
            return
        await player.pause(False)
        await ctx.send("▶️ Resumed.")

    @commands.hybrid_command(name="skip", description="Skip the current track.")
    async def skip(self, ctx: commands.Context) -> None:
        await ctx.defer()
        player = await self._get_player(ctx)
        if player is None:
            return
        if not player.playing and not player.paused:
            await ctx.send("⚠️ Nothing is playing.", ephemeral=True)
            return
        await player.skip(force=True)
        await ctx.send("⏭ Skipped.")

    @commands.hybrid_command(
        name="stop", description="Stop playback and clear the queue."
    )
    async def stop(self, ctx: commands.Context) -> None:
        await ctx.defer()
        player = await self._get_player(ctx)
        if player is None:
            return
        player.queue.clear()
        await player.stop()
        await ctx.send("⏹ Stopped and cleared the queue.")

    @commands.hybrid_command(name="queue", description="View the current queue.")
    async def queue_cmd(self, ctx: commands.Context) -> None:
        await ctx.defer()
        player: wavelink.Player | None = ctx.guild.voice_client  # type: ignore[assignment]
        embed = discord.Embed(title="📋 Queue", color=discord.Color.blurple())

        if player and player.current:
            embed.add_field(
                name="Now Playing",
                value=f"🎵 **{player.current.title}**",
                inline=False,
            )

        if player and not player.queue.is_empty:
            queue_list = list(player.queue)
            lines = [f"`{i + 1}.` {t.title}" for i, t in enumerate(queue_list[:20])]
            suffix = (
                f"\n…and {len(queue_list) - 20} more" if len(queue_list) > 20 else ""
            )
            embed.add_field(
                name=f"Up Next ({len(queue_list)} track{'s' if len(queue_list) != 1 else ''})",
                value="\n".join(lines) + suffix,
                inline=False,
            )
        else:
            embed.add_field(name="Up Next", value="Queue is empty.", inline=False)

        await ctx.send(embed=embed)

    @commands.hybrid_command(
        name="nowplaying", description="Show what's currently playing."
    )
    async def nowplaying(self, ctx: commands.Context) -> None:
        await ctx.defer()
        player: wavelink.Player | None = ctx.guild.voice_client  # type: ignore[assignment]
        if not player or not player.current:
            await ctx.send("⚠️ Nothing is currently playing.", ephemeral=True)
            return
        await ctx.send(embed=_now_playing_embed(player.current))


# ---------------------------------------------------------------------------
# Helper: Now Playing embed
# ---------------------------------------------------------------------------

def _now_playing_embed(track: wavelink.Playable) -> discord.Embed:
    description = (
        f"[{track.title}]({track.uri})" if track.uri else track.title
    )
    embed = discord.Embed(
        title="🎵 Now Playing",
        description=description,
        color=discord.Color.green(),
    )
    if track.author:
        embed.add_field(name="Artist", value=track.author, inline=True)
    if track.length:
        total_seconds = track.length // 1000
        minutes, seconds = divmod(total_seconds, 60)
        hours, minutes = divmod(minutes, 60)
        duration = (
            f"{hours}:{minutes:02d}:{seconds:02d}"
            if hours
            else f"{minutes}:{seconds:02d}"
        )
        embed.add_field(name="Duration", value=duration, inline=True)
    if track.artwork:
        embed.set_thumbnail(url=track.artwork)
    return embed


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Music(bot))
