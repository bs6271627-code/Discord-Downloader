from __future__ import annotations

import asyncio
import json
import pathlib
import re

import discord
from discord import app_commands
from discord.ext import commands

MOD_COLOR      = 0xD1ABED  # #d1abed — standard mod embed colour
PURGEALL_COLOR = 0xB18EDE  # #b18ede — purgeall confirmation
VC_COLOR       = 0xC193CC  # #c193cc — VC mod embed accent

# Matches any discord.com / discordapp.com / ptb / canary message link
_MSG_LINK_RE = re.compile(
    r"https?://(?:ptb\.|canary\.)?discord(?:app)?\.com/channels/"
    r"(\d+)/(\d+)/(\d+)",
    re.IGNORECASE,
)

# ------------------------------------------------------------------ #
#  VC Ban persistent storage
# ------------------------------------------------------------------ #

_VCBAN_PATH = pathlib.Path(__file__).parent.parent / "data" / "vcban.json"


def _load_vcban() -> dict[str, list[int]]:
    """Load the VC ban database from disk.  Returns {} on any error."""
    try:
        with open(_VCBAN_PATH, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_vcban(data: dict[str, list[int]]) -> None:
    """Persist the VC ban database to disk."""
    _VCBAN_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(_VCBAN_PATH, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)


# ------------------------------------------------------------------ #
#  Shared helpers
# ------------------------------------------------------------------ #

def _usage_embed(title: str, usage: str, note: str = "") -> discord.Embed:
    embed = discord.Embed(title=title, color=MOD_COLOR)
    embed.add_field(name="Usage", value=f"`{usage}`", inline=False)
    if note:
        embed.add_field(name="Note", value=note, inline=False)
    return embed


async def _ack(ctx: commands.Context) -> None:
    """
    Acknowledge the invocation without polluting the channel:
    - Slash → ephemeral defer (private "thinking", never seen by others)
    - Prefix → silently delete the command message
    """
    if ctx.interaction:
        await ctx.defer(ephemeral=True)
    else:
        try:
            await ctx.message.delete()
        except Exception:
            pass


async def _resolve(ctx: commands.Context) -> None:
    """
    After sending the visible channel embed, clean up the ephemeral
    defer for slash commands so Discord doesn't show "failed".
    """
    if ctx.interaction:
        try:
            await ctx.interaction.delete_original_response()
        except Exception:
            pass


# ------------------------------------------------------------------ #
#  Permission predicates
# ------------------------------------------------------------------ #

async def _author_can_manage(ctx: commands.Context) -> bool:
    if ctx.guild is None:
        raise commands.NoPrivateMessage()
    perms: discord.Permissions = ctx.author.guild_permissions  # type: ignore[union-attr]
    if perms.manage_messages or perms.administrator:
        return True
    raise commands.CheckFailure(
        "❌ You need **Manage Messages** or **Administrator** permission to use this command."
    )


def _bot_can_manage(ctx: commands.Context) -> str | None:
    """Return an error string if the bot lacks Manage Messages in the channel."""
    me = ctx.guild.me  # type: ignore[union-attr]
    ch: discord.TextChannel = ctx.channel  # type: ignore[assignment]
    if not ch.permissions_for(me).manage_messages:
        return "❌ I need **Manage Messages** permission in this channel to delete messages."
    return None


async def _author_can_move(ctx: commands.Context) -> bool:
    """Require Move Members or Administrator to use VC kick/pull commands."""
    if ctx.guild is None:
        raise commands.NoPrivateMessage()
    perms: discord.Permissions = ctx.author.guild_permissions  # type: ignore[union-attr]
    if perms.move_members or perms.administrator:
        return True
    raise commands.CheckFailure(
        "❌ You need **Move Members** or **Administrator** permission to use this command."
    )


async def _author_can_vc_ban(ctx: commands.Context) -> bool:
    """Require Manage Guild or Administrator to VC-ban users."""
    if ctx.guild is None:
        raise commands.NoPrivateMessage()
    perms: discord.Permissions = ctx.author.guild_permissions  # type: ignore[union-attr]
    if perms.manage_guild or perms.administrator:
        return True
    raise commands.CheckFailure(
        "❌ You need **Manage Server** or **Administrator** permission to use this command."
    )


async def _author_can_mute(ctx: commands.Context) -> bool:
    """Require Mute Members or Administrator to VC-mute users."""
    if ctx.guild is None:
        raise commands.NoPrivateMessage()
    perms: discord.Permissions = ctx.author.guild_permissions  # type: ignore[union-attr]
    if perms.mute_members or perms.administrator:
        return True
    raise commands.CheckFailure(
        "❌ You need **Mute Members** or **Administrator** permission to use this command."
    )


async def _author_can_deafen(ctx: commands.Context) -> bool:
    """Require Deafen Members or Administrator to VC-deafen users."""
    if ctx.guild is None:
        raise commands.NoPrivateMessage()
    perms: discord.Permissions = ctx.author.guild_permissions  # type: ignore[union-attr]
    if perms.deafen_members or perms.administrator:
        return True
    raise commands.CheckFailure(
        "❌ You need **Deafen Members** or **Administrator** permission to use this command."
    )


# ------------------------------------------------------------------ #
#  Role-hierarchy helper
# ------------------------------------------------------------------ #

def _hierarchy_error(
    ctx: commands.Context,
    member: discord.Member,
) -> str | None:
    """
    Return an error string if acting on *member* is blocked by role hierarchy,
    or None if the action is allowed.
    """
    author: discord.Member = ctx.author  # type: ignore[assignment]
    bot_me: discord.Member = ctx.guild.me  # type: ignore[union-attr]

    if member == author:
        return "❌ You cannot use this command on yourself."
    if member.id == ctx.guild.owner_id:  # type: ignore[union-attr]
        return "❌ You cannot moderate the server owner."
    if member.top_role >= bot_me.top_role:
        return (
            f"❌ I cannot moderate **{member}** — "
            "their highest role is equal to or above mine."
        )
    if author.id != ctx.guild.owner_id and member.top_role >= author.top_role:  # type: ignore[union-attr]
        return (
            f"❌ You cannot moderate **{member}** — "
            "their highest role is equal to or above yours."
        )
    return None


# ------------------------------------------------------------------ #
#  Cog
# ------------------------------------------------------------------ #

class Moderation(commands.Cog):
    """Channel moderation, bulk-deletion, and voice moderation commands."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        # In-memory VC ban store: {"guild_id": [user_id, ...]}
        self._vcban_data: dict[str, list[int]] = _load_vcban()

    # ================================================================ #
    #  TEXT / CHANNEL MODERATION
    # ================================================================ #

    # ------------------------------------------------------------------ #
    #  purge — delete N recent messages
    # ------------------------------------------------------------------ #

    @commands.hybrid_command(
        name="purge",
        aliases=["Purge"],
        description="Delete a number of recent messages from this channel (1–100).",
    )
    @app_commands.describe(amount="Number of messages to delete (1–100).")
    @app_commands.default_permissions(manage_messages=True)
    @commands.guild_only()
    @commands.check(_author_can_manage)
    async def purge(self, ctx: commands.Context, amount: int) -> None:
        if amount < 1 or amount > 100:
            await _ack(ctx)
            await ctx.channel.send(  # type: ignore[union-attr]
                embed=_usage_embed(
                    "⚠️ Invalid Amount",
                    "?purge <amount>  |  /purge amount:<amount>",
                    "Amount must be between **1** and **100**.",
                ),
                delete_after=8,
            )
            await _resolve(ctx)
            return

        err = _bot_can_manage(ctx)
        if err:
            await _ack(ctx)
            await ctx.channel.send(err, delete_after=8)  # type: ignore[union-attr]
            await _resolve(ctx)
            return

        await _ack(ctx)

        try:
            # For prefix the command message is already deleted; for slash there is no
            # channel message, so in both cases we purge exactly `amount` messages.
            deleted = await ctx.channel.purge(limit=amount, bulk=True)  # type: ignore[union-attr]
            count = len(deleted)
        except discord.Forbidden:
            print(f"[purge] Forbidden in #{ctx.channel} (guild {ctx.guild})", flush=True)
            await ctx.channel.send("❌ I don't have permission to delete messages here.", delete_after=8)  # type: ignore[union-attr]
            await _resolve(ctx)
            return
        except discord.HTTPException as exc:
            print(f"[purge] HTTPException in #{ctx.channel}: {exc}", flush=True)
            await ctx.channel.send(  # type: ignore[union-attr]
                "❌ Could not delete all messages — some may be older than 14 days.", delete_after=8
            )
            await _resolve(ctx)
            return

        embed = discord.Embed(
            description=f"🧹 Successfully deleted **{count}** message{'s' if count != 1 else ''}.",
            color=MOD_COLOR,
        )
        embed.set_footer(
            text=f"Requested by {ctx.author}",
            icon_url=ctx.author.display_avatar.url,
        )
        msg = await ctx.channel.send(embed=embed)  # type: ignore[union-attr]
        await _resolve(ctx)
        await asyncio.sleep(5)
        try:
            await msg.delete()
        except Exception:
            pass

    # ------------------------------------------------------------------ #
    #  purgeuser — delete a user's recent messages
    # ------------------------------------------------------------------ #

    @commands.hybrid_command(
        name="purgeuser",
        aliases=["Purgeuser"],
        description="Delete a specific user's most recent messages from this channel.",
    )
    @app_commands.describe(
        member="The member whose messages to delete.",
        amount="How many of their messages to remove (1–100).",
    )
    @app_commands.default_permissions(manage_messages=True)
    @commands.guild_only()
    @commands.check(_author_can_manage)
    async def purgeuser(
        self,
        ctx: commands.Context,
        member: discord.Member,
        amount: int,
    ) -> None:
        if amount < 1 or amount > 100:
            await _ack(ctx)
            await ctx.channel.send(  # type: ignore[union-attr]
                embed=_usage_embed(
                    "⚠️ Invalid Amount",
                    "?purgeuser @user <amount>  |  /purgeuser",
                    "Amount must be between **1** and **100**.",
                ),
                delete_after=8,
            )
            await _resolve(ctx)
            return

        err = _bot_can_manage(ctx)
        if err:
            await _ack(ctx)
            await ctx.channel.send(err, delete_after=8)  # type: ignore[union-attr]
            await _resolve(ctx)
            return

        await _ack(ctx)

        # Track how many of that user's messages we have collected so far.
        collected = 0

        def _by_member(m: discord.Message) -> bool:
            nonlocal collected
            if m.author.id == member.id and collected < amount:
                collected += 1
                return True
            return False

        try:
            # Scan up to 500 recent messages to find `amount` from this user.
            deleted = await ctx.channel.purge(limit=500, check=_by_member, bulk=True)  # type: ignore[union-attr]
            count = len(deleted)
        except discord.Forbidden:
            print(
                f"[purgeuser] Forbidden in #{ctx.channel} (guild {ctx.guild})",
                flush=True,
            )
            await ctx.channel.send("❌ I don't have permission to delete messages here.", delete_after=8)  # type: ignore[union-attr]
            await _resolve(ctx)
            return
        except discord.HTTPException as exc:
            print(f"[purgeuser] HTTPException in #{ctx.channel}: {exc}", flush=True)
            await ctx.channel.send(  # type: ignore[union-attr]
                "❌ Could not delete all messages — some may be older than 14 days.", delete_after=8
            )
            await _resolve(ctx)
            return

        embed = discord.Embed(
            description=(
                f"🧹 Deleted **{count}** message{'s' if count != 1 else ''} "
                f"from {member.mention}."
            ),
            color=MOD_COLOR,
        )
        embed.set_footer(
            text=f"Requested by {ctx.author}",
            icon_url=ctx.author.display_avatar.url,
        )
        msg = await ctx.channel.send(embed=embed)  # type: ignore[union-attr]
        await _resolve(ctx)
        await asyncio.sleep(6)
        try:
            await msg.delete()
        except Exception:
            pass

    # ------------------------------------------------------------------ #
    #  purgeall — delete ALL available messages from a user
    # ------------------------------------------------------------------ #

    @commands.hybrid_command(
        name="purgeall",
        aliases=["Purgeall"],
        description="Delete all available messages from a user in this channel.",
    )
    @app_commands.describe(
        member="The member whose messages to remove.",
        amount="Maximum number of messages to delete (default 500, max 2000).",
    )
    @app_commands.default_permissions(manage_messages=True)
    @commands.guild_only()
    @commands.check(_author_can_manage)
    async def purgeall(
        self,
        ctx: commands.Context,
        member: discord.Member,
        amount: int = 500,
    ) -> None:
        if amount < 1 or amount > 2000:
            await _ack(ctx)
            await ctx.channel.send(  # type: ignore[union-attr]
                embed=_usage_embed(
                    "⚠️ Invalid Amount",
                    "?purgeall @user [amount]  |  /purgeall",
                    "Amount must be between **1** and **2000** (default: 500).",
                ),
                delete_after=8,
            )
            await _resolve(ctx)
            return

        err = _bot_can_manage(ctx)
        if err:
            await _ack(ctx)
            await ctx.channel.send(err, delete_after=8)  # type: ignore[union-attr]
            await _resolve(ctx)
            return

        await _ack(ctx)

        # Post a visible working embed that we'll edit when done.
        working_embed = discord.Embed(
            description=f"🔍 Scanning history for {member.mention}'s messages…",
            color=PURGEALL_COLOR,
        )
        status_msg = await ctx.channel.send(embed=working_embed)  # type: ignore[union-attr]
        await _resolve(ctx)

        deleted_count = 0
        remaining     = amount

        try:
            async for message in ctx.channel.history(limit=None):  # type: ignore[union-attr]
                if remaining <= 0:
                    break
                if message.author.id != member.id:
                    continue
                try:
                    await message.delete()
                    deleted_count += 1
                    remaining     -= 1
                    # Gentle rate-limit backoff — 1 delete per ~0.6 s
                    await asyncio.sleep(0.6)
                except discord.NotFound:
                    pass  # already gone
                except discord.Forbidden:
                    print(
                        f"[purgeall] Forbidden deleting message {message.id} "
                        f"in #{ctx.channel}",
                        flush=True,
                    )
                    break
                except discord.HTTPException as exc:
                    print(
                        f"[purgeall] HTTPException on message {message.id}: {exc}",
                        flush=True,
                    )

        except discord.Forbidden:
            print(
                f"[purgeall] Cannot read history in #{ctx.channel} "
                f"(guild {ctx.guild})",
                flush=True,
            )
            await status_msg.edit(
                content="❌ I don't have permission to read message history here.",
                embed=None,
            )
            return
        except discord.HTTPException as exc:
            print(f"[purgeall] HTTPException scanning history: {exc}", flush=True)

        embed = discord.Embed(
            title="🧹 Deep Purge Complete",
            description=(
                f"Removed **{deleted_count}** message{'s' if deleted_count != 1 else ''} "
                f"from {member.mention}."
            ),
            color=PURGEALL_COLOR,
        )
        embed.set_footer(
            text=f"Requested by {ctx.author}",
            icon_url=ctx.author.display_avatar.url,
        )
        await status_msg.edit(embed=embed)

    # ------------------------------------------------------------------ #
    #  purgemi — delete messages by ID or link
    # ------------------------------------------------------------------ #

    @commands.hybrid_command(
        name="purgemi",
        aliases=["Purgemi"],
        description="Delete specific messages by their IDs or Discord message links.",
    )
    @app_commands.describe(
        targets="One or more message IDs / links separated by spaces.",
    )
    @app_commands.default_permissions(manage_messages=True)
    @commands.guild_only()
    @commands.check(_author_can_manage)
    async def purgemi(self, ctx: commands.Context, *, targets: str = "") -> None:
        await _ack(ctx)

        if not targets.strip():
            await ctx.channel.send(  # type: ignore[union-attr]
                embed=_usage_embed(
                    "⚠️ No Targets Provided",
                    "?purgemi <id_or_link> [more…]  |  /purgemi",
                    "Provide one or more message IDs or Discord message links.",
                ),
                delete_after=10,
            )
            await _resolve(ctx)
            return

        err = _bot_can_manage(ctx)
        if err:
            await ctx.channel.send(err, delete_after=8)  # type: ignore[union-attr]
            await _resolve(ctx)
            return

        # ── Parse tokens into (message_id, is_valid) pairs ────────────────
        raw_tokens   = targets.split()
        message_ids  : list[int] = []
        invalid_items: list[str] = []

        for token in raw_tokens:
            link_match = _MSG_LINK_RE.search(token)
            if link_match:
                message_ids.append(int(link_match.group(3)))
            elif re.fullmatch(r"\d{17,20}", token):
                message_ids.append(int(token))
            else:
                invalid_items.append(token)

        succeeded = 0
        failed    = 0

        for msg_id in message_ids:
            try:
                target_msg = await ctx.channel.fetch_message(msg_id)  # type: ignore[union-attr]
                await target_msg.delete()
                succeeded += 1
            except discord.NotFound:
                print(
                    f"[purgemi] Message {msg_id} not found "
                    f"(deleted or wrong channel in #{ctx.channel})",
                    flush=True,
                )
                failed += 1
            except discord.Forbidden:
                print(
                    f"[purgemi] Forbidden deleting message {msg_id} "
                    f"in #{ctx.channel}",
                    flush=True,
                )
                failed += 1
            except discord.HTTPException as exc:
                print(f"[purgemi] HTTPException on message {msg_id}: {exc}", flush=True)
                failed += 1

        # ── Summary embed ─────────────────────────────────────────────────
        embed = discord.Embed(title="🧹 Message Purge Summary", color=MOD_COLOR)
        embed.add_field(name="✅ Deleted",  value=str(succeeded),         inline=True)
        embed.add_field(name="❌ Failed",   value=str(failed),            inline=True)
        embed.add_field(name="⚠️ Invalid", value=str(len(invalid_items)), inline=True)

        if invalid_items:
            # Show at most 10 invalid tokens to stay within embed limits.
            shown = invalid_items[:10]
            extra = len(invalid_items) - len(shown)
            value = "\n".join(f"`{t}`" for t in shown)
            if extra:
                value += f"\n*…and {extra} more*"
            embed.add_field(name="Invalid IDs / Links", value=value, inline=False)

        embed.set_footer(
            text=f"Requested by {ctx.author}",
            icon_url=ctx.author.display_avatar.url,
        )
        await ctx.channel.send(embed=embed)  # type: ignore[union-attr]
        await _resolve(ctx)

    # ================================================================ #
    #  VOICE MODERATION
    # ================================================================ #

    # ------------------------------------------------------------------ #
    #  VC Ban event listener — auto-kick banned users on VC join
    # ------------------------------------------------------------------ #

    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ) -> None:
        """Instantly disconnect any VC-banned user who joins a voice channel."""
        # Only act when a user joins a channel (not leaves or moves within)
        if after.channel is None:
            return

        guild_key = str(member.guild.id)
        banned_ids = self._vcban_data.get(guild_key, [])
        if member.id not in banned_ids:
            return

        # Check the bot has Move Members in the destination channel before trying
        bot_me = member.guild.me
        if not after.channel.permissions_for(bot_me).move_members:
            print(
                f"[vcban] Cannot kick {member} ({member.id}) from "
                f"#{after.channel} — bot lacks Move Members.",
                flush=True,
            )
            return

        try:
            await member.move_to(None, reason="Voice Ban — automatic enforcement")
            print(
                f"[vcban] Auto-kicked VC-banned user {member} ({member.id}) "
                f"from #{after.channel} in {member.guild}.",
                flush=True,
            )
        except discord.Forbidden:
            print(
                f"[vcban] Forbidden auto-kicking {member} ({member.id}) "
                f"from #{after.channel}.",
                flush=True,
            )
        except discord.HTTPException as exc:
            print(
                f"[vcban] HTTPException auto-kicking {member} ({member.id}): {exc}",
                flush=True,
            )

    # ------------------------------------------------------------------ #
    #  vckick — disconnect a member from their VC
    # ------------------------------------------------------------------ #

    @commands.hybrid_command(
        name="vckick",
        aliases=["Vckick"],
        description="Disconnect the specified member from their current voice channel.",
    )
    @app_commands.describe(member="The member to disconnect from voice.")
    @app_commands.default_permissions(move_members=True)
    @commands.guild_only()
    @commands.check(_author_can_move)
    async def vckick(self, ctx: commands.Context, member: discord.Member) -> None:
        await _ack(ctx)

        # Hierarchy check
        h_err = _hierarchy_error(ctx, member)
        if h_err:
            await ctx.channel.send(h_err, delete_after=8)  # type: ignore[union-attr]
            await _resolve(ctx)
            return

        # Target must be in a VC
        if member.voice is None or member.voice.channel is None:
            embed = discord.Embed(
                description=f"❌ **{member}** is not connected to any voice channel.",
                color=MOD_COLOR,
            )
            await ctx.channel.send(embed=embed, delete_after=8)  # type: ignore[union-attr]
            await _resolve(ctx)
            return

        # Bot permission check
        bot_me: discord.Member = ctx.guild.me  # type: ignore[union-attr]
        if not member.voice.channel.permissions_for(bot_me).move_members:
            await ctx.channel.send(  # type: ignore[union-attr]
                "❌ I need **Move Members** permission in that voice channel.",
                delete_after=8,
            )
            await _resolve(ctx)
            return

        channel_name = member.voice.channel.name
        try:
            await member.move_to(None, reason=f"VC Kick by {ctx.author} ({ctx.author.id})")
        except discord.Forbidden:
            await ctx.channel.send("❌ I don't have permission to disconnect that member.", delete_after=8)  # type: ignore[union-attr]
            await _resolve(ctx)
            return
        except discord.HTTPException as exc:
            print(f"[vckick] HTTPException: {exc}", flush=True)
            await ctx.channel.send("❌ Something went wrong. Please try again.", delete_after=8)  # type: ignore[union-attr]
            await _resolve(ctx)
            return

        embed = discord.Embed(
            description=(
                f"🔇 **{member}** has been disconnected from **{channel_name}**."
            ),
            color=VC_COLOR,
        )
        embed.set_footer(
            text=f"Requested by {ctx.author}",
            icon_url=ctx.author.display_avatar.url,
        )
        await ctx.channel.send(embed=embed)  # type: ignore[union-attr]
        await _resolve(ctx)

    # ------------------------------------------------------------------ #
    #  vcpull — move a member into the author's VC
    # ------------------------------------------------------------------ #

    @commands.hybrid_command(
        name="vcpull",
        aliases=["Vcpull"],
        description="Move the specified member into the voice channel you are in.",
    )
    @app_commands.describe(member="The member to pull into your voice channel.")
    @app_commands.default_permissions(move_members=True)
    @commands.guild_only()
    @commands.check(_author_can_move)
    async def vcpull(self, ctx: commands.Context, member: discord.Member) -> None:
        await _ack(ctx)

        author: discord.Member = ctx.author  # type: ignore[assignment]

        # Author must be in a VC
        if author.voice is None or author.voice.channel is None:
            embed = discord.Embed(
                description="❌ You must be connected to a voice channel to use this command.",
                color=MOD_COLOR,
            )
            await ctx.channel.send(embed=embed, delete_after=8)  # type: ignore[union-attr]
            await _resolve(ctx)
            return

        # Target must be in a VC
        if member.voice is None or member.voice.channel is None:
            embed = discord.Embed(
                description=f"❌ **{member}** is not connected to any voice channel.",
                color=MOD_COLOR,
            )
            await ctx.channel.send(embed=embed, delete_after=8)  # type: ignore[union-attr]
            await _resolve(ctx)
            return

        # Don't pull someone who's already in the same channel
        if member.voice.channel == author.voice.channel:
            embed = discord.Embed(
                description=f"❌ **{member}** is already in your voice channel.",
                color=MOD_COLOR,
            )
            await ctx.channel.send(embed=embed, delete_after=8)  # type: ignore[union-attr]
            await _resolve(ctx)
            return

        # Hierarchy check
        h_err = _hierarchy_error(ctx, member)
        if h_err:
            await ctx.channel.send(h_err, delete_after=8)  # type: ignore[union-attr]
            await _resolve(ctx)
            return

        # Bot permission check in destination channel
        bot_me: discord.Member = ctx.guild.me  # type: ignore[union-attr]
        dest_channel = author.voice.channel
        if not dest_channel.permissions_for(bot_me).move_members:
            await ctx.channel.send(  # type: ignore[union-attr]
                "❌ I need **Move Members** permission in your voice channel.",
                delete_after=8,
            )
            await _resolve(ctx)
            return

        try:
            await member.move_to(
                dest_channel,
                reason=f"VC Pull by {ctx.author} ({ctx.author.id})",
            )
        except discord.Forbidden:
            await ctx.channel.send("❌ I don't have permission to move that member.", delete_after=8)  # type: ignore[union-attr]
            await _resolve(ctx)
            return
        except discord.HTTPException as exc:
            print(f"[vcpull] HTTPException: {exc}", flush=True)
            await ctx.channel.send("❌ Something went wrong. Please try again.", delete_after=8)  # type: ignore[union-attr]
            await _resolve(ctx)
            return

        embed = discord.Embed(
            description=(
                f"📥 **{member}** has been pulled into **{dest_channel.name}**."
            ),
            color=VC_COLOR,
        )
        embed.set_footer(
            text=f"Requested by {ctx.author}",
            icon_url=ctx.author.display_avatar.url,
        )
        await ctx.channel.send(embed=embed)  # type: ignore[union-attr]
        await _resolve(ctx)

    # ------------------------------------------------------------------ #
    #  vcpullall — move every member from a VC into the author's VC
    # ------------------------------------------------------------------ #

    @commands.hybrid_command(
        name="vcpullall",
        aliases=["Vcpullall"],
        description="Move every member from a voice channel into your current voice channel.",
    )
    @app_commands.describe(channel="The voice channel to pull all members from.")
    @app_commands.default_permissions(move_members=True)
    @commands.guild_only()
    @commands.check(_author_can_move)
    async def vcpullall(
        self,
        ctx: commands.Context,
        channel: discord.VoiceChannel,
    ) -> None:
        await _ack(ctx)

        author: discord.Member = ctx.author  # type: ignore[assignment]

        # Author must be in a VC
        if author.voice is None or author.voice.channel is None:
            embed = discord.Embed(
                description="❌ You must be connected to a voice channel to use this command.",
                color=MOD_COLOR,
            )
            await ctx.channel.send(embed=embed, delete_after=8)  # type: ignore[union-attr]
            await _resolve(ctx)
            return

        dest_channel = author.voice.channel

        # Can't pull from the same channel the author is in
        if channel == dest_channel:
            embed = discord.Embed(
                description="❌ The source channel is the same as your current voice channel.",
                color=MOD_COLOR,
            )
            await ctx.channel.send(embed=embed, delete_after=8)  # type: ignore[union-attr]
            await _resolve(ctx)
            return

        # Snapshot members currently in the source channel
        members_to_move = list(channel.members)

        if not members_to_move:
            embed = discord.Embed(
                description=f"❌ **{channel.name}** is empty — no one to pull.",
                color=MOD_COLOR,
            )
            await ctx.channel.send(embed=embed, delete_after=8)  # type: ignore[union-attr]
            await _resolve(ctx)
            return

        # Post a working status embed
        status_embed = discord.Embed(
            description=f"📡 Pulling **{len(members_to_move)}** member(s) from **{channel.name}**…",
            color=VC_COLOR,
        )
        status_msg = await ctx.channel.send(embed=status_embed)  # type: ignore[union-attr]
        await _resolve(ctx)

        bot_me: discord.Member = ctx.guild.me  # type: ignore[union-attr]
        moved   = 0
        skipped = 0

        for m in members_to_move:
            # Skip members that are no longer in a VC (left between snapshot and now)
            if m.voice is None or m.voice.channel is None:
                skipped += 1
                continue
            # Skip if the bot can't move this member (hierarchy)
            if m.top_role >= bot_me.top_role:
                skipped += 1
                continue
            try:
                await m.move_to(
                    dest_channel,
                    reason=f"VC Pull All by {ctx.author} ({ctx.author.id})",
                )
                moved += 1
                # Brief sleep to avoid rate-limiting
                await asyncio.sleep(0.3)
            except discord.Forbidden:
                print(f"[vcpullall] Forbidden moving {m} ({m.id})", flush=True)
                skipped += 1
            except discord.HTTPException as exc:
                print(f"[vcpullall] HTTPException moving {m} ({m.id}): {exc}", flush=True)
                skipped += 1

        result_embed = discord.Embed(
            title="📥 VC Pull All Complete",
            color=VC_COLOR,
        )
        result_embed.add_field(
            name="✅ Moved",
            value=f"**{moved}** member{'s' if moved != 1 else ''}",
            inline=True,
        )
        result_embed.add_field(
            name="⏭️ Skipped",
            value=f"**{skipped}** member{'s' if skipped != 1 else ''}",
            inline=True,
        )
        result_embed.add_field(
            name="📢 Destination",
            value=f"**{dest_channel.name}**",
            inline=True,
        )
        result_embed.set_footer(
            text=f"Requested by {ctx.author}",
            icon_url=ctx.author.display_avatar.url,
        )
        await status_msg.edit(embed=result_embed)

    # ------------------------------------------------------------------ #
    #  vcban — prefix group  (?vcban  /  ?vcban remove)
    # ------------------------------------------------------------------ #

    @commands.group(
        name="vcban",
        aliases=["Vcban"],
        invoke_without_command=True,
        brief="Voice ban one or more users.",
    )
    @commands.guild_only()
    @commands.check(_author_can_vc_ban)
    async def vcban_group(
        self,
        ctx: commands.Context,
        members: commands.Greedy[discord.Member],
    ) -> None:
        """Voice ban one or more members. Banned users are auto-kicked whenever they join a VC."""
        await _ack(ctx)

        if not members:
            await ctx.channel.send(  # type: ignore[union-attr]
                embed=_usage_embed(
                    "⚠️ No Members Provided",
                    "?vcban @user1 [@user2 …]  |  /vcban",
                    "Mention one or more members to voice ban.",
                ),
                delete_after=10,
            )
            await _resolve(ctx)
            return

        guild_key = str(ctx.guild.id)  # type: ignore[union-attr]
        banned_ids = self._vcban_data.setdefault(guild_key, [])

        banned_now : list[str] = []
        already    : list[str] = []
        failed     : list[str] = []

        for member in members:
            # Hierarchy check — skip silently-invalid targets with an error entry
            h_err = _hierarchy_error(ctx, member)
            if h_err:
                failed.append(f"**{member}** — {h_err.lstrip('❌ ')}")
                continue

            if member.id in banned_ids:
                already.append(f"**{member}**")
                continue

            banned_ids.append(member.id)
            banned_now.append(f"**{member}**")

            # If they're already in a VC, kick them immediately
            if member.voice and member.voice.channel:
                try:
                    await member.move_to(
                        None,
                        reason=f"VC Ban by {ctx.author} ({ctx.author.id})",
                    )
                except Exception:
                    pass  # Enforcement will still work via event listener

        _save_vcban(self._vcban_data)

        embed = discord.Embed(title="🔇 Voice Ban", color=VC_COLOR)

        if banned_now:
            embed.add_field(
                name="✅ Banned",
                value="\n".join(banned_now),
                inline=False,
            )
        if already:
            embed.add_field(
                name="⚠️ Already Banned",
                value="\n".join(already),
                inline=False,
            )
        if failed:
            embed.add_field(
                name="❌ Skipped",
                value="\n".join(failed),
                inline=False,
            )

        if not banned_now and not already and not failed:
            embed.description = "No valid members were provided."

        embed.set_footer(
            text=f"Requested by {ctx.author}",
            icon_url=ctx.author.display_avatar.url,
        )
        await ctx.channel.send(embed=embed)  # type: ignore[union-attr]
        await _resolve(ctx)

    @vcban_group.command(
        name="remove",
        aliases=["Remove", "rm", "Rm"],
        brief="Remove a voice ban from a user.",
    )
    @commands.guild_only()
    @commands.check(_author_can_vc_ban)
    async def vcban_remove_prefix(
        self,
        ctx: commands.Context,
        member: discord.Member,
    ) -> None:
        """Remove a VC ban so the member can join voice channels normally again."""
        await _ack(ctx)

        guild_key  = str(ctx.guild.id)  # type: ignore[union-attr]
        banned_ids = self._vcban_data.get(guild_key, [])

        if member.id not in banned_ids:
            embed = discord.Embed(
                description=f"❌ **{member}** does not have an active voice ban.",
                color=MOD_COLOR,
            )
            await ctx.channel.send(embed=embed, delete_after=8)  # type: ignore[union-attr]
            await _resolve(ctx)
            return

        banned_ids.remove(member.id)
        self._vcban_data[guild_key] = banned_ids
        _save_vcban(self._vcban_data)

        embed = discord.Embed(
            description=f"✅ Voice ban removed for **{member}**. They can now join voice channels normally.",
            color=VC_COLOR,
        )
        embed.set_footer(
            text=f"Requested by {ctx.author}",
            icon_url=ctx.author.display_avatar.url,
        )
        await ctx.channel.send(embed=embed)  # type: ignore[union-attr]
        await _resolve(ctx)

    # ------------------------------------------------------------------ #
    #  /vcban  — slash command (single member)
    # ------------------------------------------------------------------ #

    @app_commands.command(
        name="vcban",
        description="Voice ban a user — they will be instantly kicked from any VC they join.",
    )
    @app_commands.describe(member="The member to voice ban.")
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.guild_only()
    async def vcban_slash(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
    ) -> None:
        await interaction.response.defer(ephemeral=True)

        # Hierarchy check using a lightweight inline helper
        author: discord.Member = interaction.user  # type: ignore[assignment]
        bot_me: discord.Member = interaction.guild.me  # type: ignore[union-attr]

        if member == author:
            await interaction.followup.send("❌ You cannot voice ban yourself.", ephemeral=True)
            return
        if member.id == interaction.guild.owner_id:  # type: ignore[union-attr]
            await interaction.followup.send("❌ You cannot moderate the server owner.", ephemeral=True)
            return
        if member.top_role >= bot_me.top_role:
            await interaction.followup.send(
                f"❌ I cannot moderate **{member}** — their highest role is equal to or above mine.",
                ephemeral=True,
            )
            return
        if author.id != interaction.guild.owner_id and member.top_role >= author.top_role:  # type: ignore[union-attr]
            await interaction.followup.send(
                f"❌ You cannot moderate **{member}** — their highest role is equal to or above yours.",
                ephemeral=True,
            )
            return

        guild_key  = str(interaction.guild_id)
        banned_ids = self._vcban_data.setdefault(guild_key, [])

        if member.id in banned_ids:
            await interaction.followup.send(
                f"⚠️ **{member}** is already voice banned.", ephemeral=True
            )
            return

        banned_ids.append(member.id)
        _save_vcban(self._vcban_data)

        # Kick immediately if they're in a VC
        if member.voice and member.voice.channel:
            try:
                await member.move_to(None, reason=f"VC Ban via /vcban by {author} ({author.id})")
            except Exception:
                pass

        embed = discord.Embed(
            title="🔇 Voice Ban",
            description=f"✅ **{member}** has been voice banned and will be auto-kicked from any VC they join.",
            color=VC_COLOR,
        )
        embed.set_footer(
            text=f"Requested by {author}",
            icon_url=author.display_avatar.url,
        )
        await interaction.channel.send(embed=embed)  # type: ignore[union-attr]
        try:
            await interaction.delete_original_response()
        except Exception:
            pass

    # ------------------------------------------------------------------ #
    #  /vcban_remove  — slash command
    # ------------------------------------------------------------------ #

    @app_commands.command(
        name="vcban_remove",
        description="Remove a voice ban from a user so they can join voice channels normally.",
    )
    @app_commands.describe(member="The member whose voice ban to remove.")
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.guild_only()
    async def vcban_remove_slash(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
    ) -> None:
        await interaction.response.defer(ephemeral=True)

        guild_key  = str(interaction.guild_id)
        banned_ids = self._vcban_data.get(guild_key, [])

        if member.id not in banned_ids:
            await interaction.followup.send(
                f"❌ **{member}** does not have an active voice ban.", ephemeral=True
            )
            return

        banned_ids.remove(member.id)
        self._vcban_data[guild_key] = banned_ids
        _save_vcban(self._vcban_data)

        embed = discord.Embed(
            description=(
                f"✅ Voice ban removed for **{member}**. "
                "They can now join voice channels normally."
            ),
            color=VC_COLOR,
        )
        author: discord.Member = interaction.user  # type: ignore[assignment]
        embed.set_footer(
            text=f"Requested by {author}",
            icon_url=author.display_avatar.url,
        )
        await interaction.channel.send(embed=embed)  # type: ignore[union-attr]
        try:
            await interaction.delete_original_response()
        except Exception:
            pass

    # ------------------------------------------------------------------ #
    #  vcmute — server mute / unmute a member
    # ------------------------------------------------------------------ #

    @commands.hybrid_command(
        name="vcmute",
        aliases=["Vcmute"],
        description="Server mute (or unmute) the specified member in voice chat.",
    )
    @app_commands.describe(member="The member to server mute or unmute.")
    @app_commands.default_permissions(mute_members=True)
    @commands.guild_only()
    @commands.check(_author_can_mute)
    async def vcmute(self, ctx: commands.Context, member: discord.Member) -> None:
        await _ack(ctx)

        # Hierarchy check
        h_err = _hierarchy_error(ctx, member)
        if h_err:
            await ctx.channel.send(h_err, delete_after=8)  # type: ignore[union-attr]
            await _resolve(ctx)
            return

        # Target must be in a VC
        if member.voice is None or member.voice.channel is None:
            embed = discord.Embed(
                description=f"❌ **{member}** is not connected to any voice channel.",
                color=MOD_COLOR,
            )
            await ctx.channel.send(embed=embed, delete_after=8)  # type: ignore[union-attr]
            await _resolve(ctx)
            return

        # Bot permission check
        bot_me: discord.Member = ctx.guild.me  # type: ignore[union-attr]
        if not member.voice.channel.permissions_for(bot_me).mute_members:
            await ctx.channel.send(  # type: ignore[union-attr]
                "❌ I need **Mute Members** permission in that voice channel.",
                delete_after=8,
            )
            await _resolve(ctx)
            return

        currently_muted = member.voice.mute
        new_state = not currently_muted
        action    = "unmuted" if currently_muted else "muted"
        icon      = "🔊" if currently_muted else "🔇"

        try:
            await member.edit(
                mute=new_state,
                reason=f"VC {'Unmute' if currently_muted else 'Mute'} by {ctx.author} ({ctx.author.id})",
            )
        except discord.Forbidden:
            await ctx.channel.send("❌ I don't have permission to mute that member.", delete_after=8)  # type: ignore[union-attr]
            await _resolve(ctx)
            return
        except discord.HTTPException as exc:
            print(f"[vcmute] HTTPException: {exc}", flush=True)
            await ctx.channel.send("❌ Something went wrong. Please try again.", delete_after=8)  # type: ignore[union-attr]
            await _resolve(ctx)
            return

        embed = discord.Embed(
            description=f"{icon} **{member}** has been server **{action}**.",
            color=VC_COLOR,
        )
        embed.set_footer(
            text=f"Requested by {ctx.author}",
            icon_url=ctx.author.display_avatar.url,
        )
        await ctx.channel.send(embed=embed)  # type: ignore[union-attr]
        await _resolve(ctx)

    # ------------------------------------------------------------------ #
    #  vcdef — server deafen / undeafen a member
    # ------------------------------------------------------------------ #

    @commands.hybrid_command(
        name="vcdef",
        aliases=["Vcdef"],
        description="Server deafen (or undeafen) the specified member in voice chat.",
    )
    @app_commands.describe(member="The member to server deafen or undeafen.")
    @app_commands.default_permissions(deafen_members=True)
    @commands.guild_only()
    @commands.check(_author_can_deafen)
    async def vcdef(self, ctx: commands.Context, member: discord.Member) -> None:
        await _ack(ctx)

        # Hierarchy check
        h_err = _hierarchy_error(ctx, member)
        if h_err:
            await ctx.channel.send(h_err, delete_after=8)  # type: ignore[union-attr]
            await _resolve(ctx)
            return

        # Target must be in a VC
        if member.voice is None or member.voice.channel is None:
            embed = discord.Embed(
                description=f"❌ **{member}** is not connected to any voice channel.",
                color=MOD_COLOR,
            )
            await ctx.channel.send(embed=embed, delete_after=8)  # type: ignore[union-attr]
            await _resolve(ctx)
            return

        # Bot permission check
        bot_me: discord.Member = ctx.guild.me  # type: ignore[union-attr]
        if not member.voice.channel.permissions_for(bot_me).deafen_members:
            await ctx.channel.send(  # type: ignore[union-attr]
                "❌ I need **Deafen Members** permission in that voice channel.",
                delete_after=8,
            )
            await _resolve(ctx)
            return

        currently_deafened = member.voice.deaf
        new_state = not currently_deafened
        action    = "undeafened" if currently_deafened else "deafened"
        icon      = "🔊" if currently_deafened else "🔕"

        try:
            await member.edit(
                deafen=new_state,
                reason=f"VC {'Undeafen' if currently_deafened else 'Deafen'} by {ctx.author} ({ctx.author.id})",
            )
        except discord.Forbidden:
            await ctx.channel.send("❌ I don't have permission to deafen that member.", delete_after=8)  # type: ignore[union-attr]
            await _resolve(ctx)
            return
        except discord.HTTPException as exc:
            print(f"[vcdef] HTTPException: {exc}", flush=True)
            await ctx.channel.send("❌ Something went wrong. Please try again.", delete_after=8)  # type: ignore[union-attr]
            await _resolve(ctx)
            return

        embed = discord.Embed(
            description=f"{icon} **{member}** has been server **{action}**.",
            color=VC_COLOR,
        )
        embed.set_footer(
            text=f"Requested by {ctx.author}",
            icon_url=ctx.author.display_avatar.url,
        )
        await ctx.channel.send(embed=embed)  # type: ignore[union-attr]
        await _resolve(ctx)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Moderation(bot))
