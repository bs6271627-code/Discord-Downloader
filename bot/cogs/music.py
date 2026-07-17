import asyncio
import discord
from discord import app_commands
from discord.ext import commands
from collections import deque
from utils.ytdl import YTDLSource


class GuildState:
    """Holds per-guild playback state."""

    def __init__(self):
        self.queue: deque[dict] = deque()   # list of {"query": str, "requester": Member}
        self.current: YTDLSource | None = None
        self.loop = False
        self._lock = asyncio.Lock()

    def clear(self):
        self.queue.clear()
        self.current = None


class Music(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.states: dict[int, GuildState] = {}

    def get_state(self, guild_id: int) -> GuildState:
        if guild_id not in self.states:
            self.states[guild_id] = GuildState()
        return self.states[guild_id]

    # ------------------------------------------------------------------ #
    #  Internal helpers
    # ------------------------------------------------------------------ #

    async def _ensure_voice(
        self, interaction: discord.Interaction
    ) -> discord.VoiceClient | None:
        """Return the guild's VoiceClient, or send an error and return None."""
        vc: discord.VoiceClient = interaction.guild.voice_client
        if vc is None:
            await interaction.followup.send(
                "❌ I'm not in a voice channel. Use `/join` first.", ephemeral=True
            )
            return None
        return vc

    async def _join_channel(
        self, interaction: discord.Interaction
    ) -> discord.VoiceClient | None:
        """Join the user's voice channel. Returns VoiceClient or None."""
        if interaction.user.voice is None:
            await interaction.followup.send(
                "❌ You must be in a voice channel.", ephemeral=True
            )
            return None

        channel = interaction.user.voice.channel
        vc: discord.VoiceClient = interaction.guild.voice_client

        if vc:
            if vc.channel == channel:
                return vc
            await vc.move_to(channel)
            return vc

        return await channel.connect()

    def _play_next(self, interaction: discord.Interaction, state: GuildState):
        """Called (synchronously) after a track finishes; schedules the next one."""
        if state.queue:
            asyncio.run_coroutine_threadsafe(
                self._advance(interaction, state),
                self.bot.loop,
            )

    async def _advance(self, interaction: discord.Interaction, state: GuildState):
        """Resolve and play the next queued item."""
        if not state.queue:
            state.current = None
            return

        item = state.queue.popleft()
        vc: discord.VoiceClient = interaction.guild.voice_client
        if vc is None or not vc.is_connected():
            state.current = None
            return

        try:
            source = await YTDLSource.from_query(
                item["query"], loop=self.bot.loop
            )
        except Exception as exc:
            await interaction.channel.send(f"⚠️ Skipping — could not load track: {exc}")
            self._play_next(interaction, state)
            return

        state.current = source

        vc.play(
            discord.PCMVolumeTransformer(source.source, volume=0.5),
            after=lambda _: self._play_next(interaction, state),
        )

        embed = discord.Embed(
            title="🎵 Now Playing",
            description=f"[{source.title}]({source.url})",
            color=discord.Color.green(),
        )
        embed.add_field(name="Duration", value=source.formatted_duration())
        embed.add_field(name="Uploader", value=source.uploader)
        embed.add_field(name="Requested by", value=item["requester"].display_name)
        if source.thumbnail:
            embed.set_thumbnail(url=source.thumbnail)
        await interaction.channel.send(embed=embed)

    # ------------------------------------------------------------------ #
    #  Slash commands
    # ------------------------------------------------------------------ #

    @app_commands.command(name="join", description="Join your current voice channel.")
    async def join(self, interaction: discord.Interaction):
        await interaction.response.defer()
        vc = await self._join_channel(interaction)
        if vc:
            await interaction.followup.send(
                f"✅ Joined **{vc.channel.name}**.", ephemeral=True
            )

    @app_commands.command(name="leave", description="Leave the voice channel and clear the queue.")
    async def leave(self, interaction: discord.Interaction):
        await interaction.response.defer()
        vc: discord.VoiceClient = interaction.guild.voice_client
        if vc is None:
            await interaction.followup.send("❌ I'm not in a voice channel.", ephemeral=True)
            return

        state = self.get_state(interaction.guild_id)
        state.clear()
        await vc.disconnect()
        await interaction.followup.send("👋 Left the voice channel and cleared the queue.")

    @app_commands.command(name="play", description="Play a song from YouTube (URL or search query).")
    @app_commands.describe(query="YouTube URL or search terms")
    async def play(self, interaction: discord.Interaction, query: str):
        await interaction.response.defer()

        vc = await self._join_channel(interaction)
        if vc is None:
            return

        state = self.get_state(interaction.guild_id)

        # If something is already playing, add to queue
        if vc.is_playing() or vc.is_paused():
            state.queue.append({"query": query, "requester": interaction.user})
            pos = len(state.queue)
            await interaction.followup.send(
                f"➕ Added to queue (position **#{pos}**): `{query}`"
            )
            return

        # Nothing playing — resolve and start immediately
        await interaction.followup.send(f"🔍 Searching for `{query}`…")
        try:
            source = await YTDLSource.from_query(query, loop=self.bot.loop)
        except Exception as exc:
            await interaction.followup.send(f"❌ Could not load track: {exc}")
            return

        state.current = source

        vc.play(
            discord.PCMVolumeTransformer(source.source, volume=0.5),
            after=lambda _: self._play_next(interaction, state),
        )

        embed = discord.Embed(
            title="🎵 Now Playing",
            description=f"[{source.title}]({source.url})",
            color=discord.Color.green(),
        )
        embed.add_field(name="Duration", value=source.formatted_duration())
        embed.add_field(name="Uploader", value=source.uploader)
        embed.add_field(name="Requested by", value=interaction.user.display_name)
        if source.thumbnail:
            embed.set_thumbnail(url=source.thumbnail)
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="pause", description="Pause the current track.")
    async def pause(self, interaction: discord.Interaction):
        await interaction.response.defer()
        vc = await self._ensure_voice(interaction)
        if vc is None:
            return

        if vc.is_playing():
            vc.pause()
            await interaction.followup.send("⏸ Paused.")
        elif vc.is_paused():
            await interaction.followup.send("⚠️ Already paused.", ephemeral=True)
        else:
            await interaction.followup.send("⚠️ Nothing is playing.", ephemeral=True)

    @app_commands.command(name="resume", description="Resume a paused track.")
    async def resume(self, interaction: discord.Interaction):
        await interaction.response.defer()
        vc = await self._ensure_voice(interaction)
        if vc is None:
            return

        if vc.is_paused():
            vc.resume()
            await interaction.followup.send("▶️ Resumed.")
        elif vc.is_playing():
            await interaction.followup.send("⚠️ Already playing.", ephemeral=True)
        else:
            await interaction.followup.send("⚠️ Nothing is playing.", ephemeral=True)

    @app_commands.command(name="skip", description="Skip the current track.")
    async def skip(self, interaction: discord.Interaction):
        await interaction.response.defer()
        vc = await self._ensure_voice(interaction)
        if vc is None:
            return

        if not vc.is_playing() and not vc.is_paused():
            await interaction.followup.send("⚠️ Nothing is playing.", ephemeral=True)
            return

        vc.stop()   # triggers the `after` callback → plays next
        await interaction.followup.send("⏭ Skipped.")

    @app_commands.command(name="stop", description="Stop playback and clear the queue.")
    async def stop(self, interaction: discord.Interaction):
        await interaction.response.defer()
        vc = await self._ensure_voice(interaction)
        if vc is None:
            return

        state = self.get_state(interaction.guild_id)
        state.queue.clear()
        state.current = None

        if vc.is_playing() or vc.is_paused():
            vc.stop()

        await interaction.followup.send("⏹ Stopped and queue cleared.")

    @app_commands.command(name="queue", description="Show the current queue.")
    async def queue_cmd(self, interaction: discord.Interaction):
        await interaction.response.defer()
        state = self.get_state(interaction.guild_id)

        embed = discord.Embed(title="📋 Queue", color=discord.Color.blurple())

        if state.current:
            embed.add_field(
                name="Now Playing",
                value=f"🎵 {state.current.title}",
                inline=False,
            )

        if state.queue:
            lines = [
                f"`{i + 1}.` {item['query']} — *{item['requester'].display_name}*"
                for i, item in enumerate(state.queue)
            ]
            embed.add_field(
                name=f"Up Next ({len(state.queue)} track{'s' if len(state.queue) != 1 else ''})",
                value="\n".join(lines[:20]) + ("\n…and more" if len(state.queue) > 20 else ""),
                inline=False,
            )
        else:
            embed.add_field(name="Up Next", value="Queue is empty.", inline=False)

        await interaction.followup.send(embed=embed)

    @app_commands.command(name="nowplaying", description="Show what's currently playing.")
    async def nowplaying(self, interaction: discord.Interaction):
        await interaction.response.defer()
        state = self.get_state(interaction.guild_id)

        if state.current is None:
            await interaction.followup.send("⚠️ Nothing is currently playing.", ephemeral=True)
            return

        src = state.current
        embed = discord.Embed(
            title="🎵 Now Playing",
            description=f"[{src.title}]({src.url})",
            color=discord.Color.green(),
        )
        embed.add_field(name="Duration", value=src.formatted_duration())
        embed.add_field(name="Uploader", value=src.uploader)
        if src.thumbnail:
            embed.set_thumbnail(url=src.thumbnail)
        await interaction.followup.send(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(Music(bot))
