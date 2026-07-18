from __future__ import annotations

import asyncio
import gc
import resource

import aiohttp
import discord
import wavelink
from discord import app_commands
from discord.ext import commands

ACCENT = 0xC193CC
ENHANCE_COLOR = 0xD1ABED

# Cog extension names that must always be loaded.
_EXPECTED_EXTENSIONS: frozenset[str] = frozenset({
    "cogs.music", "cogs.help", "cogs.utility", "cogs.couples",
    "cogs.games", "cogs.fun", "cogs.queue", "cogs.audio", "cogs.premium",
})

# Per-guild history: list of (title, uri) newest-first, capped at 20
_history: dict[int, list[tuple[str, str]]] = {}

# Per-guild saved playlists: {playlist_name: [uri, ...]}
_playlists: dict[int, dict[str, list[str]]] = {}

# Guilds with 24/7 mode enabled — read by music.py via getattr on the player
# (we store it as a player attribute; see the 247 command below)


# ------------------------------------------------------------------ #
#  Permission check: Administrator OR bot owner
# ------------------------------------------------------------------ #

async def _is_admin_or_owner(ctx: commands.Context) -> bool:
    if await ctx.bot.is_owner(ctx.author):
        return True
    if ctx.guild and ctx.author.guild_permissions.administrator:
        return True
    raise commands.CheckFailure(
        "❌ You need **Administrator** permission (or be the bot owner) to use this command."
    )


class Premium(commands.Cog):
    """Premium feature commands."""

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

    async def _get_player(self, ctx: commands.Context) -> wavelink.Player | None:
        player: wavelink.Player | None = ctx.guild.voice_client  # type: ignore[assignment]
        if player is None:
            await ctx.send("❌ I'm not in a voice channel.", ephemeral=True)
            return None
        return player

    # ------------------------------------------------------------------ #
    #  Track history listener
    # ------------------------------------------------------------------ #

    @commands.Cog.listener()
    async def on_wavelink_track_start(
        self, payload: wavelink.TrackStartEventPayload
    ) -> None:
        player: wavelink.Player = payload.player
        track: wavelink.Playable = payload.track
        if player.guild is None:
            return
        gid = player.guild.id
        store = _history.setdefault(gid, [])
        entry = (track.title, track.uri or "")
        if entry not in store:
            store.insert(0, entry)
        if len(store) > 20:
            store.pop()

    # ------------------------------------------------------------------ #
    #  247
    # ------------------------------------------------------------------ #

    @commands.hybrid_command(name="247", description="Toggle 24/7 mode — bot stays in VC even when silent.")
    @commands.guild_only()
    async def twentyfour_seven(self, ctx: commands.Context) -> None:
        await ctx.defer()
        player = await self._get_player(ctx)
        if player is None:
            return
        current = getattr(player, "twentyfour_seven", False)
        player.twentyfour_seven = not current  # type: ignore[attr-defined]
        if player.twentyfour_seven:
            msg = "🔁 **24/7 mode enabled** — I'll stay in the voice channel."
        else:
            msg = "💤 **24/7 mode disabled** — I'll leave after inactivity."
        await ctx.send(embed=discord.Embed(description=msg, color=ACCENT))

    # ------------------------------------------------------------------ #
    #  autoplay
    # ------------------------------------------------------------------ #

    @commands.hybrid_command(name="autoplay", aliases=["ap", "Ap", "Autoplay"], description="Toggle autoplay of related tracks.")
    @commands.guild_only()
    async def autoplay(self, ctx: commands.Context) -> None:
        await ctx.defer()
        player = await self._get_player(ctx)
        if player is None:
            return
        if player.autoplay == wavelink.AutoPlayMode.partial:
            player.autoplay = wavelink.AutoPlayMode.disabled
            msg = "⏹️ Autoplay **disabled**."
        else:
            player.autoplay = wavelink.AutoPlayMode.partial
            msg = "▶️ Autoplay **enabled** — related tracks will play automatically."
        await ctx.send(embed=discord.Embed(description=msg, color=ACCENT))

    # ------------------------------------------------------------------ #
    #  lyrics
    # ------------------------------------------------------------------ #

    @commands.hybrid_command(name="lyrics", aliases=["Lyrics"], description="Fetch lyrics for the current track.")
    @commands.guild_only()
    async def lyrics(self, ctx: commands.Context) -> None:
        await ctx.defer()
        player = await self._get_player(ctx)
        if player is None:
            return
        if not player.current:
            await ctx.send("❌ Nothing is playing right now.", ephemeral=True)
            return

        track = player.current
        artist = track.author or ""
        title = track.title or ""

        session = await self._get_session()
        try:
            url = f"https://api.lyrics.ovh/v1/{artist}/{title}"
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=8)) as resp:
                data = await resp.json(content_type=None)
        except Exception:
            await ctx.send("❌ Could not fetch lyrics right now.", ephemeral=True)
            return

        if "error" in data or "lyrics" not in data:
            await ctx.send(
                f"❌ No lyrics found for **{title}**.", ephemeral=True
            )
            return

        raw = data["lyrics"].strip()
        # Discord embed description cap is 4096 chars
        if len(raw) > 3900:
            raw = raw[:3900] + "\n…*(truncated)*"

        embed = discord.Embed(
            title=f"🎵 {title}",
            description=raw,
            color=ACCENT,
        )
        if artist:
            embed.set_author(name=artist)
        embed.set_footer(text="Lyrics via lyrics.ovh")
        await ctx.send(embed=embed)

    # ------------------------------------------------------------------ #
    #  history
    # ------------------------------------------------------------------ #

    @commands.hybrid_command(name="history", aliases=["his", "His", "History"], description="View recently played tracks.")
    @commands.guild_only()
    async def history(self, ctx: commands.Context) -> None:
        await ctx.defer()
        gid = ctx.guild.id  # type: ignore[union-attr]
        store = _history.get(gid, [])

        embed = discord.Embed(title="📜 Recently Played", color=ACCENT)
        if not store:
            embed.description = "No tracks have been played yet."
        else:
            lines = []
            for i, (title, uri) in enumerate(store[:15], start=1):
                entry = f"`{i}.` [{title}]({uri})" if uri else f"`{i}.` {title}"
                lines.append(entry)
            embed.description = "\n".join(lines)
        await ctx.send(embed=embed)

    # ------------------------------------------------------------------ #
    #  playlist
    # ------------------------------------------------------------------ #

    @commands.hybrid_command(name="playlist", aliases=["pl", "Pl", "Playlist"], description="Manage saved playlists: list · save · load · delete")
    @app_commands.describe(
        action="Action to perform (list / save / load / delete)",
        name="Playlist name (required for save / load / delete)",
    )
    @app_commands.choices(action=[
        app_commands.Choice(name="list — show your playlists",   value="list"),
        app_commands.Choice(name="save — save current queue",    value="save"),
        app_commands.Choice(name="load — add playlist to queue", value="load"),
        app_commands.Choice(name="delete — remove a playlist",   value="delete"),
    ])
    @commands.guild_only()
    async def playlist(
        self,
        ctx: commands.Context,
        action: str = "list",
        *,
        name: str = "",
    ) -> None:
        await ctx.defer()
        gid = ctx.guild.id  # type: ignore[union-attr]
        store = _playlists.setdefault(gid, {})
        action = action.lower().strip()

        if action == "list":
            embed = discord.Embed(title="🎵 Saved Playlists", color=ACCENT)
            if not store:
                embed.description = "No playlists saved yet.\nUse `?playlist save <name>` to save the current queue."
            else:
                lines = [
                    f"`{i}.` **{pname}** — {len(uris)} track{'s' if len(uris) != 1 else ''}"
                    for i, (pname, uris) in enumerate(store.items(), start=1)
                ]
                embed.description = "\n".join(lines)
            await ctx.send(embed=embed)

        elif action == "save":
            if not name:
                await ctx.send("❌ Provide a name: `?playlist save <name>`", ephemeral=True)
                return
            player: wavelink.Player | None = ctx.guild.voice_client  # type: ignore[assignment]
            uris: list[str] = []
            if player:
                if player.current and player.current.uri:
                    uris.append(player.current.uri)
                for t in player.queue:
                    if t.uri:
                        uris.append(t.uri)
            if not uris:
                await ctx.send("❌ Nothing in the queue to save.", ephemeral=True)
                return
            store[name] = uris
            embed = discord.Embed(
                description=f"💾 Saved **{name}** with **{len(uris)}** track{'s' if len(uris) != 1 else ''}.",
                color=ACCENT,
            )
            await ctx.send(embed=embed)

        elif action == "load":
            if not name:
                await ctx.send("❌ Provide a name: `?playlist load <name>`", ephemeral=True)
                return
            if name not in store:
                await ctx.send(f"❌ No playlist named **{name}**.", ephemeral=True)
                return
            player = await self._get_player(ctx)
            if player is None:
                return
            uris = store[name]
            loaded = 0
            for uri in uris:
                try:
                    tracks = await wavelink.Playable.search(uri)
                    if tracks:
                        t = tracks[0] if not isinstance(tracks, wavelink.Playlist) else tracks.tracks[0]
                        player.queue.put(t)
                        loaded += 1
                except Exception:
                    pass
            if not player.playing and not player.queue.is_empty:
                await player.play(player.queue.get(), populate=False)
            embed = discord.Embed(
                description=f"📂 Loaded **{loaded}** track{'s' if loaded != 1 else ''} from **{name}**.",
                color=ACCENT,
            )
            await ctx.send(embed=embed)

        elif action == "delete":
            if not name:
                await ctx.send("❌ Provide a name: `?playlist delete <name>`", ephemeral=True)
                return
            if name not in store:
                await ctx.send(f"❌ No playlist named **{name}**.", ephemeral=True)
                return
            del store[name]
            embed = discord.Embed(
                description=f"🗑️ Deleted playlist **{name}**.",
                color=ACCENT,
            )
            await ctx.send(embed=embed)

        else:
            await ctx.send(
                "❌ Unknown action. Use `list`, `save`, `load`, or `delete`.",
                ephemeral=True,
            )

    # ------------------------------------------------------------------ #
    #  enhance
    # ------------------------------------------------------------------ #

    @commands.hybrid_command(
        name="enhance",
        aliases=["en", "En", "Enhance"],
        description="Optimize the bot, refresh the voice session, and run a full health check.",
    )
    @app_commands.default_permissions(administrator=True)
    @commands.guild_only()
    @commands.check(_is_admin_or_owner)
    async def enhance(self, ctx: commands.Context) -> None:
        await ctx.defer()

        avatar = self.bot.user.display_avatar.url if self.bot.user else None

        # ── helpers ──────────────────────────────────────────────────────
        def _progress(done: list[str], current: str | None = None) -> discord.Embed:
            lines = [f"✅ {s}" for s in done]
            if current:
                lines.append(f"🔄 {current}")
            return discord.Embed(
                title="⚙️ Seraph Enhancement",
                description="\n".join(lines) or "Starting…",
                color=ENHANCE_COLOR,
            ).set_footer(text="Seraph Optimizer", icon_url=avatar)

        done_steps: list[str] = []
        fixes: list[str] = []
        checks: dict[str, str] = {}

        # ── Step 0: initial embed ─────────────────────────────────────────
        msg = await ctx.send(embed=_progress([], "Scanning voice session…"))

        # ════════════════════════════════════════════════════════════════
        # PHASE 1 — Snapshot active player state (before anything changes)
        # ════════════════════════════════════════════════════════════════
        player: wavelink.Player | None = ctx.guild.voice_client  # type: ignore[assignment]
        has_session = player is not None and player.connected

        # Fields we'll restore after reconnect.
        snap_channel    = None
        snap_track      = None
        snap_pos        = 0
        snap_paused     = False
        snap_queue      : list[wavelink.Playable] = []
        snap_queue_mode = wavelink.QueueMode.normal
        snap_volume     = 100
        snap_filters    = None   # wavelink.Filters object
        snap_autoplay   = wavelink.AutoPlayMode.disabled
        snap_247        = False

        if has_session:
            snap_channel    = player.channel                            # type: ignore[union-attr]
            snap_track      = player.current                            # type: ignore[union-attr]
            snap_pos        = player.position                           # type: ignore[union-attr]  # ms
            snap_paused     = player.paused                             # type: ignore[union-attr]
            snap_queue      = list(player.queue)                        # type: ignore[union-attr]
            snap_queue_mode = player.queue.mode                         # type: ignore[union-attr]
            snap_volume     = player.volume                             # type: ignore[union-attr]
            snap_filters    = player.filters                            # type: ignore[union-attr]
            snap_autoplay   = player.autoplay                           # type: ignore[union-attr]
            snap_247        = getattr(player, "twentyfour_seven", False)

        done_steps.append("Player state saved")
        await msg.edit(embed=_progress(done_steps, "Running health checks…"))

        # ════════════════════════════════════════════════════════════════
        # PHASE 2 — Health checks (cogs, commands, Lavalink, gc, memory)
        # ════════════════════════════════════════════════════════════════

        # 2a. Cog / extension integrity
        loaded_exts  = set(self.bot.extensions.keys())
        missing_exts = _EXPECTED_EXTENSIONS - loaded_exts
        if missing_exts:
            repaired = 0
            for ext in missing_exts:
                try:
                    await self.bot.load_extension(ext)
                    fixes.append(f"🔧 Reloaded missing extension: `{ext}`")
                    repaired += 1
                except Exception as exc:
                    fixes.append(f"❌ Could not reload `{ext}`: {exc}")
            total_now = len(_EXPECTED_EXTENSIONS) - len(missing_exts) + repaired
            checks["Cogs"] = f"⚠️ {total_now}/{len(_EXPECTED_EXTENSIONS)} — repaired {repaired}"
        else:
            checks["Cogs"] = f"✅ {len(_EXPECTED_EXTENSIONS)}/{len(_EXPECTED_EXTENSIONS)} loaded"

        # 2b. Command registry
        prefix_cmds = len([c for c in self.bot.commands if not c.hidden])
        slash_cmds  = len(self.bot.tree.get_commands())
        checks["Commands"] = f"✅ {prefix_cmds} prefix · {slash_cmds} slash"

        # 2c. Lavalink node — verify/reconnect BEFORE touching the voice session
        lavalink_ok = False
        try:
            node = wavelink.Pool.get_node()
            if node.status == wavelink.NodeStatus.CONNECTED:
                checks["Lavalink"] = "✅ Node connected"
                lavalink_ok = True
            else:
                raise RuntimeError("Node not CONNECTED")
        except Exception:
            try:
                uri = getattr(self.bot, "lavalink_uri",      "http://localhost:2333")
                pwd = getattr(self.bot, "lavalink_password", "youshallnotpass")
                await wavelink.Pool.connect(
                    nodes=[wavelink.Node(uri=uri, password=pwd)],
                    client=self.bot,
                    cache_capacity=100,
                )
                checks["Lavalink"] = "🔧 Node reconnected"
                fixes.append("🔧 Reconnected to Lavalink node")
                lavalink_ok = True
            except Exception as exc:
                checks["Lavalink"] = f"❌ {exc}"

        # 2d. Garbage collection + memory
        gc.collect()
        rss_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
        checks["Memory"] = f"✅ {rss_mb:.1f} MB RSS"

        # 2e. Uptime
        start_time = getattr(self.bot, "start_time", None)
        if start_time is not None:
            delta = discord.utils.utcnow() - start_time
            d, rem = delta.days, delta.seconds
            h, rem = divmod(rem, 3600)
            m      = rem // 60
            checks["Uptime"] = f"✅ {d}d {h}h {m}m"
        else:
            checks["Uptime"] = "✅ (unavailable)"

        done_steps.append("Health checks passed")
        await msg.edit(embed=_progress(done_steps, "Refreshing voice connection…"))

        # ════════════════════════════════════════════════════════════════
        # PHASE 3 — Voice session refresh
        # ════════════════════════════════════════════════════════════════
        voice_refreshed  = False
        playback_restored = False
        queue_restored   = len(snap_queue)     # track count to report
        playback_error   = None

        if has_session and snap_channel is not None and lavalink_ok:
            # ── 3a. Graceful disconnect ───────────────────────────────────
            try:
                await player.disconnect()          # type: ignore[union-attr]
            except Exception:
                pass
            # Give Discord a moment to process the disconnect.
            await asyncio.sleep(1.2)

            done_steps.append("Voice connection closed")
            await msg.edit(embed=_progress(done_steps, "Reconnecting…"))

            # ── 3b. Reconnect to same channel ────────────────────────────
            try:
                new_player: wavelink.Player = await snap_channel.connect(
                    cls=wavelink.Player, self_deaf=True
                )
                voice_refreshed = True
                done_steps.append("Voice connection refreshed")
                await msg.edit(embed=_progress(done_steps, "Restoring playback…"))
            except Exception as exc:
                fixes.append(f"❌ Reconnect failed: {exc}")
                new_player = None   # type: ignore[assignment]

            if new_player is not None:
                # ── 3c. Restore settings ─────────────────────────────────
                new_player.autoplay          = snap_autoplay               # type: ignore[attr-defined]
                new_player.twentyfour_seven  = snap_247                    # type: ignore[attr-defined]
                new_player.queue.mode        = snap_queue_mode

                await new_player.set_volume(snap_volume)

                if snap_filters is not None:
                    await new_player.set_filters(snap_filters)

                # ── 3d. Re-populate queue ─────────────────────────────────
                for track in snap_queue:
                    new_player.queue.put(track)

                # ── 3e. Resume current track from saved position ──────────
                if snap_track is not None:
                    # Cap position to a safe range (avoid seeking past end).
                    track_len   = snap_track.length or 0
                    safe_start  = (
                        min(snap_pos, max(0, track_len - 2000))
                        if track_len > 0 and not snap_track.is_stream
                        else 0
                    )
                    seekable    = snap_track.is_seekable and not snap_track.is_stream

                    for attempt in range(1, 3):   # try twice
                        try:
                            await new_player.play(
                                snap_track,
                                start  = safe_start if seekable else 0,
                                paused = snap_paused,
                            )
                            playback_restored = True
                            break
                        except Exception as exc:
                            if attempt == 1:
                                await asyncio.sleep(1)
                                safe_start = 0   # retry from beginning
                            else:
                                playback_error = str(exc)
                                fixes.append(f"❌ Playback restore failed: {exc}")

                    if playback_restored and safe_start == 0 and seekable and snap_pos > 2000:
                        # Play succeeded from 0; note the position wasn't restored.
                        fixes.append(
                            "⚠️ Resumed from track start (position restore not supported "
                            "for this source)"
                        )
        elif not has_session:
            # No active voice session — skip voice phase entirely.
            checks["Voice"] = "✅ No active session (skipped)"

        # ════════════════════════════════════════════════════════════════
        # PHASE 4 — Latency + final embed
        # ════════════════════════════════════════════════════════════════
        ws_ms = round(self.bot.latency * 1000)
        checks["Latency"] = f"✅ {ws_ms} ms WS heartbeat"

        done_steps.append("Optimization complete")
        await msg.edit(embed=_progress(done_steps))
        await asyncio.sleep(0.4)   # let the user see the complete step list briefly

        # ── Summary fields ────────────────────────────────────────────────
        voice_line     = "✅ Voice connection refreshed" if voice_refreshed else (
                         "➖ No active voice session"   if not has_session  else
                         "❌ Voice reconnect failed"
                        )
        playback_line  = (
            "✅ Playback restored"              if playback_restored else
            "➖ Nothing was playing"             if snap_track is None else
            "❌ Playback restore failed"
        )
        queue_line     = (
            f"✅ Queue preserved ({queue_restored} track{'s' if queue_restored != 1 else ''})"
            if queue_restored else "➖ Queue was empty"
        )

        all_ok = not fixes
        embed  = discord.Embed(
            title       = "⚙️ Seraph Enhancement — Complete",
            description = (
                "✨ **All systems nominal.** The bot is already fully optimized."
                if all_ok else
                f"🔧 **{len(fixes)} action{'s' if len(fixes) != 1 else ''} taken.**"
            ),
            color = ENHANCE_COLOR,
        )

        # Voice session block (only when a session existed)
        if has_session:
            embed.add_field(name="​", value=(
                f"{voice_line}\n"
                f"{playback_line}\n"
                f"{queue_line}"
            ), inline=False)

        # Health check grid
        field_order = ["Cogs", "Commands", "Lavalink", "Memory", "Uptime", "Latency"]
        for key in field_order:
            if key in checks:
                embed.add_field(name=key, value=checks[key], inline=True)

        # Repairs / notes
        if fixes:
            embed.add_field(
                name  = "Actions Taken",
                value = "\n".join(fixes),
                inline = False,
            )

        # Final status line
        embed.add_field(
            name  = "​",
            value = "✅ **Optimization completed successfully.**",
            inline = False,
        )

        embed.set_footer(
            text     = "Seraph Optimizer  •  Only optimizes what the bot itself controls",
            icon_url = avatar,
        )
        await msg.edit(embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Premium(bot))
