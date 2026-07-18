from __future__ import annotations

import re

import discord
import wavelink
from discord import app_commands
from discord.ext import commands

# ---------------------------------------------------------------------------
# URL detection patterns
# ---------------------------------------------------------------------------

# YouTube watch pages and youtu.be short links (with or without playlist param)
_YT_RE = re.compile(
    r"https?://(?:www\.)?(?:youtube\.com/(?:watch|playlist|shorts)\S*|youtu\.be/\S+)",
    re.IGNORECASE,
)

# Spotify open links and spotify.link short URLs
# Covers: track, album, playlist, episode (episode falls back gracefully)
_SPOTIFY_RE = re.compile(
    r"https?://(?:open\.spotify\.com/(?:track|album|playlist|episode)|spotify\.link)\S*",
    re.IGNORECASE,
)

# Any other http/https URL (Bandcamp, SoundCloud direct link, radio streams…)
_URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)

# Colour used for every play-related embed
_PLAY_COLOR = 0xD1ABED


class Music(commands.Cog):
    """All music commands, powered by Wavelink + Lavalink.

    Each command is a hybrid_command, so it registers as a slash command
    (/play), a prefix command (?play), and a mention command (@Bot play)
    from a single implementation — no logic is duplicated.
    """

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

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
        if channel is None:
            return

        embed = _now_playing_embed(track)
        await channel.send(embed=embed)

    @commands.Cog.listener()
    async def on_wavelink_track_end(
        self, payload: wavelink.TrackEndEventPayload
    ) -> None:
        # AutoPlayMode.partial handles queue advancement automatically.
        pass

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
        """Disconnect after 3 minutes of silence, unless 24/7 mode is on."""
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
        """
        Return the guild's Player.
        If *join* is True and the user is in a voice channel, create one.
        Sends an error and returns None on failure.
        """
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

        # ── No query → send usage guide ──────────────────────────────────
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

        # ── Join voice channel ────────────────────────────────────────────
        player = await self._get_player(ctx, join=True)
        if player is None:
            return

        player.home = ctx.channel  # type: ignore[attr-defined]

        # ── Detect input type and choose search strategy ──────────────────
        is_youtube = bool(_YT_RE.match(query))
        is_spotify = bool(_SPOTIFY_RE.match(query))
        is_url     = bool(_URL_RE.match(query))

        try:
            if is_youtube or is_spotify:
                # Pass the URL directly — the youtube-plugin resolves YouTube
                # natively and resolves Spotify → YouTube automatically.
                tracks: wavelink.Search = await wavelink.Playable.search(query)
            elif is_url:
                # Any other URL (SoundCloud link, Bandcamp, HTTP stream, etc.)
                # Lavalink's http source will handle it.
                tracks = await wavelink.Playable.search(query)
            else:
                # Plain text search — SoundCloud works reliably from
                # datacenter IPs; YouTube text search is blocked by default.
                tracks = await wavelink.Playable.search(
                    query, source=wavelink.TrackSource.SoundCloud
                )
        except Exception as exc:
            embed = discord.Embed(
                title="⚠️ Could Not Load Track",
                description=(
                    "Something went wrong while fetching that track.\n"
                    "Please double-check the link and try again."
                ),
                color=_PLAY_COLOR,
            )
            embed.add_field(
                name="Reason", value=f"```{exc}```", inline=False
            )
            embed.set_footer(
                text="Tip: Try searching by song name if the link isn't working."
            )
            await ctx.send(embed=embed, ephemeral=True)
            return

        # ── No results ────────────────────────────────────────────────────
        if not tracks:
            if is_spotify:
                title = "❌ Spotify Link Could Not Be Resolved"
                description = (
                    "That Spotify link didn't return any playable tracks.\n"
                    "The content may be unavailable in your region, or the link may be invalid.\n\n"
                    "**Try searching by song name instead:**\n"
                    "`?play Artist - Song Title`"
                )
            elif is_youtube:
                title = "❌ YouTube Video Unavailable"
                description = (
                    "That YouTube link couldn't be loaded.\n"
                    "The video may be private, age-restricted, or unavailable.\n\n"
                    "**Try a different link or search by name:**\n"
                    "`?play Song Title`"
                )
            elif is_url:
                title = "❌ Link Could Not Be Played"
                description = (
                    "That URL didn't return any audio.\n"
                    "Make sure it points to a supported source or a direct audio file."
                )
            else:
                title = "❌ No Results Found"
                description = (
                    f"No tracks matched **{discord.utils.escape_markdown(query)}**.\n"
                    "Try a different search term or paste a direct URL."
                )
            embed = discord.Embed(
                title=title, description=description, color=_PLAY_COLOR
            )
            embed.set_footer(
                text="Tip: Song names and direct links are both supported."
            )
            await ctx.send(embed=embed, ephemeral=True)
            return

        # ── Queue the result(s) ───────────────────────────────────────────
        if isinstance(tracks, wavelink.Playlist):
            added = len(tracks.tracks)
            for track in tracks.tracks:
                player.queue.put(track)
            source_tag = "Spotify" if is_spotify else ("YouTube" if is_youtube else "")
            label = f" ({source_tag})" if source_tag else ""
            msg = (
                f"➕ Added playlist **{discord.utils.escape_markdown(tracks.name)}**{label} "
                f"— **{added}** track{'s' if added != 1 else ''} queued."
            )
        else:
            track: wavelink.Playable = tracks[0]
            player.queue.put(track)
            if player.playing:
                msg = (
                    f"➕ Added **{discord.utils.escape_markdown(track.title)}** "
                    f"to the queue (position **#{len(player.queue)}**)."
                )
            else:
                msg = f"🎵 Loading **{discord.utils.escape_markdown(track.title)}**…"

        if not player.playing:
            await player.play(player.queue.get(), populate=False)

        await ctx.send(msg)

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
            lines = [
                f"`{i + 1}.` {t.title}" for i, t in enumerate(queue_list[:20])
            ]
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


# ------------------------------------------------------------------ #
#  Helpers
# ------------------------------------------------------------------ #


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
            f"{hours}:{minutes:02d}:{seconds:02d}" if hours
            else f"{minutes}:{seconds:02d}"
        )
        embed.add_field(name="Duration", value=duration, inline=True)
    if track.artwork:
        embed.set_thumbnail(url=track.artwork)
    return embed


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Music(bot))
