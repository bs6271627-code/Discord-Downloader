from __future__ import annotations

import discord
import wavelink
from discord import app_commands
from discord.ext import commands


class Music(commands.Cog):
    """All music slash commands, powered by Wavelink + Lavalink."""

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
        # Nothing to do here unless we want extra behaviour.
        pass

    @commands.Cog.listener()
    async def on_wavelink_track_exception(
        self, payload: wavelink.TrackExceptionEventPayload
    ) -> None:
        player: wavelink.Player = payload.player
        channel: discord.TextChannel | None = getattr(player, "home", None)
        if channel:
            await channel.send(
                f"⚠️ Playback error for **{payload.track.title}**: {payload.exception.get('message', 'unknown error')}",
            )

    @commands.Cog.listener()
    async def on_wavelink_inactive_player(
        self, player: wavelink.Player
    ) -> None:
        """Disconnect after 3 minutes of silence to save resources."""
        await player.disconnect()

    # ------------------------------------------------------------------ #
    #  Internal helpers
    # ------------------------------------------------------------------ #

    async def _get_player(
        self,
        interaction: discord.Interaction,
        *,
        join: bool = False,
    ) -> wavelink.Player | None:
        """
        Return the guild's Player.
        If *join* is True and the user is in a voice channel, create one.
        Sends an ephemeral error and returns None on failure.
        """
        player: wavelink.Player | None = interaction.guild.voice_client  # type: ignore[assignment]

        if player is None:
            if not join:
                await interaction.followup.send(
                    "❌ I'm not in a voice channel. Use `/join` first.",
                    ephemeral=True,
                )
                return None

            if interaction.user.voice is None:  # type: ignore[union-attr]
                await interaction.followup.send(
                    "❌ You must be in a voice channel first.", ephemeral=True
                )
                return None

            player = await interaction.user.voice.channel.connect(cls=wavelink.Player)  # type: ignore[union-attr]
            player.home = interaction.channel  # type: ignore[attr-defined]
            player.autoplay = wavelink.AutoPlayMode.partial

        return player

    # ------------------------------------------------------------------ #
    #  Slash commands
    # ------------------------------------------------------------------ #

    @app_commands.command(name="join", description="Join your current voice channel.")
    async def join(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)

        if interaction.user.voice is None:  # type: ignore[union-attr]
            await interaction.followup.send(
                "❌ You must be in a voice channel first.", ephemeral=True
            )
            return

        channel = interaction.user.voice.channel  # type: ignore[union-attr]
        player: wavelink.Player | None = interaction.guild.voice_client  # type: ignore[assignment]

        if player is not None:
            await player.move_to(channel)  # type: ignore[arg-type]
            await interaction.followup.send(
                f"✅ Moved to **{channel.name}**.", ephemeral=True
            )
            return

        player = await channel.connect(cls=wavelink.Player)
        player.home = interaction.channel  # type: ignore[attr-defined]
        player.autoplay = wavelink.AutoPlayMode.partial
        await interaction.followup.send(
            f"✅ Joined **{channel.name}**.", ephemeral=True
        )

    @app_commands.command(
        name="leave", description="Leave the voice channel and clear the queue."
    )
    async def leave(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()

        player: wavelink.Player | None = interaction.guild.voice_client  # type: ignore[assignment]
        if player is None:
            await interaction.followup.send(
                "❌ I'm not in a voice channel.", ephemeral=True
            )
            return

        await player.disconnect()
        await interaction.followup.send("👋 Disconnected and cleared the queue.")

    @app_commands.command(
        name="play", description="Play a track from YouTube (URL or search query)."
    )
    @app_commands.describe(query="YouTube URL or search terms")
    async def play(self, interaction: discord.Interaction, query: str) -> None:
        await interaction.response.defer()

        player = await self._get_player(interaction, join=True)
        if player is None:
            return

        # Keep home channel up to date.
        player.home = interaction.channel  # type: ignore[attr-defined]

        # Use SoundCloud search — YouTube search returns empty from datacenter IPs.
        # YouTube/SoundCloud direct URLs are detected automatically and bypass this prefix.
        tracks: wavelink.Search = await wavelink.Playable.search(
            query, source=wavelink.TrackSource.SoundCloud
        )

        if not tracks:
            await interaction.followup.send("❌ No results found.", ephemeral=True)
            return

        if isinstance(tracks, wavelink.Playlist):
            for track in tracks.tracks:
                player.queue.put(track)
            msg = (
                f"➕ Added playlist **{tracks.name}** "
                f"({len(tracks.tracks)} tracks) to the queue."
            )
        else:
            track: wavelink.Playable = tracks[0]
            player.queue.put(track)
            if player.playing:
                msg = (
                    f"➕ Added **{track.title}** to the queue "
                    f"(position **#{len(player.queue)}**)."
                )
            else:
                msg = f"🎵 Loading **{track.title}**…"

        # Start playback if nothing is currently playing.
        if not player.playing:
            await player.play(player.queue.get(), populate=False)

        await interaction.followup.send(msg)

    @app_commands.command(name="pause", description="Pause the current track.")
    async def pause(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()

        player = await self._get_player(interaction)
        if player is None:
            return

        if not player.playing:
            await interaction.followup.send(
                "⚠️ Nothing is playing.", ephemeral=True
            )
            return

        if player.paused:
            await interaction.followup.send(
                "⚠️ Already paused.", ephemeral=True
            )
            return

        await player.pause(True)
        await interaction.followup.send("⏸ Paused.")

    @app_commands.command(name="resume", description="Resume the paused track.")
    async def resume(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()

        player = await self._get_player(interaction)
        if player is None:
            return

        if not player.paused:
            await interaction.followup.send(
                "⚠️ Not currently paused.", ephemeral=True
            )
            return

        await player.pause(False)
        await interaction.followup.send("▶️ Resumed.")

    @app_commands.command(name="skip", description="Skip the current track.")
    async def skip(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()

        player = await self._get_player(interaction)
        if player is None:
            return

        if not player.playing and not player.paused:
            await interaction.followup.send(
                "⚠️ Nothing is playing.", ephemeral=True
            )
            return

        await player.skip(force=True)
        await interaction.followup.send("⏭ Skipped.")

    @app_commands.command(
        name="stop", description="Stop playback and clear the queue."
    )
    async def stop(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()

        player = await self._get_player(interaction)
        if player is None:
            return

        player.queue.clear()
        await player.stop()
        await interaction.followup.send("⏹ Stopped and cleared the queue.")

    @app_commands.command(name="queue", description="View the current queue.")
    async def queue_cmd(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()

        player: wavelink.Player | None = interaction.guild.voice_client  # type: ignore[assignment]

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
            suffix = f"\n…and {len(queue_list) - 20} more" if len(queue_list) > 20 else ""
            embed.add_field(
                name=f"Up Next ({len(queue_list)} track{'s' if len(queue_list) != 1 else ''})",
                value="\n".join(lines) + suffix,
                inline=False,
            )
        else:
            embed.add_field(name="Up Next", value="Queue is empty.", inline=False)

        await interaction.followup.send(embed=embed)

    @app_commands.command(
        name="nowplaying", description="Show what's currently playing."
    )
    async def nowplaying(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()

        player: wavelink.Player | None = interaction.guild.voice_client  # type: ignore[assignment]

        if not player or not player.current:
            await interaction.followup.send(
                "⚠️ Nothing is currently playing.", ephemeral=True
            )
            return

        await interaction.followup.send(embed=_now_playing_embed(player.current))


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
