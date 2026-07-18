from __future__ import annotations

import json
import os

import discord
from discord.ext import commands

ACCENT = 0xC193CC   # #c193cc — matches bot-wide accent
ERROR_COLOR = 0xFF5555

# The three permanent Bot Owners — always have No Prefix access, can never be
# added / removed via ?np add / ?np remove (they're hardcoded, not stored).
OWNER_IDS: frozenset[int] = frozenset({
    1487483128309223614,
    1332245879444340789,
    1239442859103621243,
})

# Persistent storage path  (bot/data/no_prefix.json)
_DATA_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "no_prefix.json")


# ------------------------------------------------------------------ #
#  JSON helpers
# ------------------------------------------------------------------ #

def load_np_users() -> set[int]:
    """Return the persisted No Prefix user IDs (excludes owners)."""
    try:
        with open(_DATA_PATH, encoding="utf-8") as fh:
            data = json.load(fh)
            if isinstance(data, list):
                return {int(uid) for uid in data}
    except (FileNotFoundError, json.JSONDecodeError, ValueError):
        pass
    return set()


def save_np_users(users: set[int]) -> None:
    """Persist the No Prefix user IDs to disk."""
    os.makedirs(os.path.dirname(_DATA_PATH), exist_ok=True)
    with open(_DATA_PATH, "w", encoding="utf-8") as fh:
        json.dump(sorted(users), fh, indent=2)


# ------------------------------------------------------------------ #
#  Shared guard
# ------------------------------------------------------------------ #

def _owner_only(ctx: commands.Context) -> bool:
    """Return True if the invoker is one of the three Bot Owners."""
    return ctx.author.id in OWNER_IDS


def _deny(reason: str) -> discord.Embed:
    return discord.Embed(description=f"❌ {reason}", color=ERROR_COLOR)


# ------------------------------------------------------------------ #
#  Cog
# ------------------------------------------------------------------ #

class NoPrefix(commands.Cog):
    """No Prefix system — owner-only management commands."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    # ── Group ──────────────────────────────────────────────────────── #

    @commands.group(
        name="np",
        invoke_without_command=True,
        brief="No Prefix management (Bot Owners only).",
    )
    async def np_group(self, ctx: commands.Context) -> None:
        """Show No Prefix usage when called with no subcommand."""
        if not _owner_only(ctx):
            return await ctx.send(
                embed=_deny("Only Bot Owners can use No Prefix commands."),
                delete_after=8,
            )
        embed = discord.Embed(
            title="⚡ No Prefix System",
            description=(
                "Grant or revoke the ability to run any command without the `?` prefix.\n\n"
                "**Subcommands**\n"
                "`np add <user>` — Grant No Prefix access\n"
                "`np remove <user>` — Revoke No Prefix access\n"
                "`np list` — List everyone with No Prefix access"
            ),
            color=ACCENT,
        )
        embed.set_footer(text="Bot Owners always have No Prefix access by default.")
        await ctx.send(embed=embed)

    # ── np add ─────────────────────────────────────────────────────── #

    @np_group.command(name="add", brief="Grant No Prefix access to a user.")
    async def np_add(self, ctx: commands.Context, user: discord.User) -> None:
        """?np add <user>  —  Grant a user permanent No Prefix access."""
        if not _owner_only(ctx):
            return await ctx.send(
                embed=_deny("Only Bot Owners can grant No Prefix access."),
                delete_after=8,
            )

        # Bot Owners are hardcoded — no point adding them to the JSON list.
        if user.id in OWNER_IDS:
            embed = discord.Embed(
                description=f"⚡ {user.mention} is already a **Bot Owner** and permanently has No Prefix access.",
                color=ACCENT,
            )
            return await ctx.send(embed=embed)

        # Duplicate guard
        if user.id in self.bot.no_prefix_users:
            embed = discord.Embed(
                description=f"⚠️ {user.mention} already has No Prefix access.",
                color=ACCENT,
            )
            return await ctx.send(embed=embed)

        self.bot.no_prefix_users.add(user.id)
        save_np_users(self.bot.no_prefix_users)

        embed = discord.Embed(
            description=f"✅ {user.mention} (`{user.id}`) has been granted **No Prefix** access.",
            color=ACCENT,
        )
        embed.set_footer(text=f"Added by {ctx.author} • ID {ctx.author.id}")
        await ctx.send(embed=embed)

    # ── np remove ──────────────────────────────────────────────────── #

    @np_group.command(name="remove", aliases=["rm"], brief="Revoke No Prefix access from a user.")
    async def np_remove(self, ctx: commands.Context, user: discord.User) -> None:
        """?np remove <user>  —  Revoke a user's No Prefix access."""
        if not _owner_only(ctx):
            return await ctx.send(
                embed=_deny("Only Bot Owners can revoke No Prefix access."),
                delete_after=8,
            )

        if user.id in OWNER_IDS:
            embed = discord.Embed(
                description=f"🔒 {user.mention} is a **Bot Owner** — their No Prefix access is permanent and cannot be removed.",
                color=ERROR_COLOR,
            )
            return await ctx.send(embed=embed)

        if user.id not in self.bot.no_prefix_users:
            embed = discord.Embed(
                description=f"⚠️ {user.mention} does not have No Prefix access.",
                color=ACCENT,
            )
            return await ctx.send(embed=embed)

        self.bot.no_prefix_users.discard(user.id)
        save_np_users(self.bot.no_prefix_users)

        embed = discord.Embed(
            description=f"✅ No Prefix access has been **revoked** from {user.mention} (`{user.id}`).",
            color=ACCENT,
        )
        embed.set_footer(text=f"Removed by {ctx.author} • ID {ctx.author.id}")
        await ctx.send(embed=embed)

    # ── np list ────────────────────────────────────────────────────── #

    @np_group.command(name="list", brief="List all users with No Prefix access.")
    async def np_list(self, ctx: commands.Context) -> None:
        """?np list  —  Display every user who currently has No Prefix access."""
        if not _owner_only(ctx):
            return await ctx.send(
                embed=_deny("Only Bot Owners can view the No Prefix list."),
                delete_after=8,
            )

        embed = discord.Embed(title="⚡ No Prefix Access List", color=ACCENT)

        # Hardcoded owners
        owner_lines = "\n".join(f"<@{uid}> (`{uid}`)" for uid in sorted(OWNER_IDS))
        embed.add_field(
            name="🔒 Bot Owners (permanent)",
            value=owner_lines or "—",
            inline=False,
        )

        # Granted users
        granted = sorted(self.bot.no_prefix_users)
        if granted:
            user_lines = "\n".join(f"<@{uid}> (`{uid}`)" for uid in granted)
        else:
            user_lines = "*No users added yet.*"
        embed.add_field(
            name=f"✅ Granted Users ({len(granted)})",
            value=user_lines,
            inline=False,
        )

        embed.set_footer(text=f"Total with access: {len(OWNER_IDS) + len(granted)}")
        await ctx.send(embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(NoPrefix(bot))
