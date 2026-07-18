from __future__ import annotations

import asyncio
import re

import discord
from discord import app_commands
from discord.ext import commands

MOD_COLOR      = 0xD1ABED  # #d1abed — standard mod embed colour
PURGEALL_COLOR = 0xB18EDE  # #b18ede — purgeall confirmation

# Matches any discord.com / discordapp.com / ptb / canary message link
_MSG_LINK_RE = re.compile(
    r"https?://(?:ptb\.|canary\.)?discord(?:app)?\.com/channels/"
    r"(\d+)/(\d+)/(\d+)",
    re.IGNORECASE,
)


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


# ------------------------------------------------------------------ #
#  Cog
# ------------------------------------------------------------------ #

class Moderation(commands.Cog):
    """Channel moderation and bulk-deletion commands."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    # ------------------------------------------------------------------ #
    #  purge — delete N recent messages
    # ------------------------------------------------------------------ #

    @commands.hybrid_command(
        name="purge",
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


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Moderation(bot))
