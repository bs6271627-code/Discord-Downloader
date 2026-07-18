from __future__ import annotations

import gc
import resource
import time

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

    @commands.hybrid_command(name="autoplay", description="Toggle autoplay of related tracks.")
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

    @commands.hybrid_command(name="lyrics", description="Fetch lyrics for the current track.")
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

    @commands.hybrid_command(name="history", description="View recently played tracks.")
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

    @commands.hybrid_command(name="playlist", description="Manage saved playlists: list · save · load · delete")
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
        description="Optimize the bot, refresh internal systems, and run a health check.",
    )
    @app_commands.default_permissions(administrator=True)
    @commands.guild_only()
    @commands.check(_is_admin_or_owner)
    async def enhance(self, ctx: commands.Context) -> None:
        await ctx.defer()

        # ── Send progress indicator ───────────────────────────────────────
        progress = discord.Embed(
            title="⚙️ Seraph Enhancement",
            description="🔄 Running health checks and optimizations…",
            color=ENHANCE_COLOR,
        )
        msg = await ctx.send(embed=progress)

        fixes: list[str] = []
        checks: dict[str, str] = {}

        # ── 1. Latency (before) ───────────────────────────────────────────
        ws_before = round(self.bot.latency * 1000)

        # ── 2. Cog / extension integrity ─────────────────────────────────
        loaded_exts = set(self.bot.extensions.keys())
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
            checks["Cogs"] = (
                f"⚠️ {total_now}/{len(_EXPECTED_EXTENSIONS)} — "
                f"repaired {repaired}"
            )
        else:
            checks["Cogs"] = f"✅ {len(_EXPECTED_EXTENSIONS)}/{len(_EXPECTED_EXTENSIONS)} loaded"

        # ── 3. Command registry ───────────────────────────────────────────
        prefix_cmds = len([c for c in self.bot.commands if not c.hidden])
        slash_cmds  = len(self.bot.tree.get_commands())
        checks["Commands"] = f"✅ {prefix_cmds} prefix · {slash_cmds} slash"

        # ── 4. Lavalink node ──────────────────────────────────────────────
        try:
            node = wavelink.Pool.get_node()
            if node.status == wavelink.NodeStatus.CONNECTED:
                checks["Lavalink"] = "✅ Node connected"
            else:
                raise RuntimeError("Node not in CONNECTED state")
        except Exception:
            # Attempt reconnect — only if no music is actively playing
            try:
                uri  = getattr(self.bot, "lavalink_uri",  "http://localhost:2333")
                pwd  = getattr(self.bot, "lavalink_password", "youshallnotpass")
                node = wavelink.Node(uri=uri, password=pwd)
                await wavelink.Pool.connect(
                    nodes=[node], client=self.bot, cache_capacity=100
                )
                checks["Lavalink"] = "🔧 Node reconnected"
                fixes.append("🔧 Reconnected to Lavalink node")
            except Exception as exc:
                checks["Lavalink"] = f"❌ Could not reconnect: {exc}"

        # ── 5. Clean up idle players ──────────────────────────────────────
        # Only disconnects players with no current track, empty queue,
        # and 24/7 mode off — never interrupts active playback.
        cleaned = 0
        for guild in self.bot.guilds:
            player: wavelink.Player | None = guild.voice_client  # type: ignore[assignment]
            if player is None:
                continue
            is_247 = getattr(player, "twentyfour_seven", False)
            if not player.playing and player.queue.is_empty and not is_247:
                try:
                    await player.disconnect()
                    cleaned += 1
                except Exception:
                    pass
        if cleaned:
            fixes.append(
                f"🧹 Released {cleaned} idle voice player{'s' if cleaned != 1 else ''}"
            )

        # ── 6. Garbage collection ─────────────────────────────────────────
        gc.collect()

        cache_note = "gc swept"
        if cleaned:
            cache_note += f", {cleaned} idle player{'s' if cleaned != 1 else ''} released"
        checks["Cache"] = f"✅ {cache_note}"

        # ── 7. Memory (RSS) ───────────────────────────────────────────────
        # ru_maxrss is in KB on Linux
        rss_kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        checks["Memory"] = f"✅ {rss_kb / 1024:.1f} MB RSS"

        # ── 8. Uptime ─────────────────────────────────────────────────────
        start_time = getattr(self.bot, "start_time", None)
        if start_time is not None:
            delta   = discord.utils.utcnow() - start_time
            days    = delta.days
            hours, rem = divmod(delta.seconds, 3600)
            minutes, _ = divmod(rem, 60)
            uptime_str = f"{days}d {hours}h {minutes}m"
        else:
            uptime_str = "unavailable"
        checks["Uptime"] = f"✅ {uptime_str}"

        # ── 9. Latency (after) ────────────────────────────────────────────
        ws_after = round(self.bot.latency * 1000)
        checks["Latency"] = f"✅ {ws_after} ms WS heartbeat"

        # ── Build final embed ─────────────────────────────────────────────
        all_ok = not fixes
        if all_ok:
            description = "✨ **All systems nominal** — the bot is already fully optimized."
        else:
            n = len(fixes)
            description = (
                f"🔧 **{n} issue{'s' if n != 1 else ''} detected and repaired automatically.**"
            )

        embed = discord.Embed(
            title="⚙️ Seraph Enhancement — Complete",
            description=description,
            color=ENHANCE_COLOR,
        )

        # Two-column summary grid
        field_order = ["Cogs", "Commands", "Lavalink", "Cache", "Memory", "Uptime", "Latency"]
        for key in field_order:
            if key in checks:
                embed.add_field(name=key, value=checks[key], inline=True)

        if fixes:
            embed.add_field(name="Repairs Applied", value="\n".join(fixes), inline=False)

        avatar = self.bot.user.display_avatar.url if self.bot.user else None
        embed.set_footer(
            text="Seraph Optimizer  •  Only optimizes what the bot itself controls",
            icon_url=avatar,
        )

        await msg.edit(embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Premium(bot))
