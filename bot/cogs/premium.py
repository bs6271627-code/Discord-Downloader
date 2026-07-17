from __future__ import annotations

import aiohttp
import discord
import wavelink
from discord import app_commands
from discord.ext import commands

ACCENT = 0xC193CC

# Per-guild history: list of (title, uri) newest-first, capped at 20
_history: dict[int, list[tuple[str, str]]] = {}

# Per-guild saved playlists: {playlist_name: [uri, ...]}
_playlists: dict[int, dict[str, list[str]]] = {}

# Guilds with 24/7 mode enabled — read by music.py via getattr on the player
# (we store it as a player attribute; see the 247 command below)


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


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Premium(bot))
