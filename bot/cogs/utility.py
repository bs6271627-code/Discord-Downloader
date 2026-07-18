from __future__ import annotations

import traceback
import time
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands

ACCENT = 0xC193CC  # #c193cc

# In-memory AFK store: {guild_id: {user_id: reason}}
_afk: dict[int, dict[int, str]] = {}


class Utility(commands.Cog):
    """General utility commands."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    # ------------------------------------------------------------------ #
    #  ping
    # ------------------------------------------------------------------ #

    @commands.hybrid_command(name="ping", description="Check the bot's latency.")
    async def ping(self, ctx: commands.Context) -> None:
        await ctx.defer()
        latency = round(self.bot.latency * 1000)
        embed = discord.Embed(
            title="🏓 Pong!",
            description=f"Gateway latency: **{latency} ms**",
            color=ACCENT,
        )
        await ctx.send(embed=embed)

    # ------------------------------------------------------------------ #
    #  botinfo
    # ------------------------------------------------------------------ #

    @commands.hybrid_command(name="botinfo", description="Display bot information.")
    async def botinfo(self, ctx: commands.Context) -> None:
        await ctx.defer()
        bot_user = self.bot.user
        embed = discord.Embed(title="🤖 Bot Information", color=ACCENT)
        if bot_user:
            embed.set_thumbnail(url=bot_user.display_avatar.url)
            embed.add_field(name="Name", value=str(bot_user), inline=True)
            embed.add_field(name="ID", value=str(bot_user.id), inline=True)
            created = discord.utils.format_dt(bot_user.created_at, style="D")
            embed.add_field(name="Created", value=created, inline=True)
        embed.add_field(name="Servers", value=str(len(self.bot.guilds)), inline=True)
        embed.add_field(
            name="Latency", value=f"{round(self.bot.latency * 1000)} ms", inline=True
        )
        embed.set_footer(text="Made by nova408")
        await ctx.send(embed=embed)

    # ------------------------------------------------------------------ #
    #  stats
    # ------------------------------------------------------------------ #

    @commands.hybrid_command(name="stats", description="View bot statistics.")
    async def stats(self, ctx: commands.Context) -> None:
        await ctx.defer()
        total_members = sum(g.member_count or 0 for g in self.bot.guilds)
        embed = discord.Embed(title="📊 Bot Statistics", color=ACCENT)
        embed.add_field(name="Servers", value=str(len(self.bot.guilds)), inline=True)
        embed.add_field(name="Total Members", value=str(total_members), inline=True)
        embed.add_field(
            name="Latency", value=f"{round(self.bot.latency * 1000)} ms", inline=True
        )
        embed.set_footer(text="Made by nova408")
        await ctx.send(embed=embed)

    # ------------------------------------------------------------------ #
    #  serverinfo
    # ------------------------------------------------------------------ #

    @commands.hybrid_command(name="serverinfo", description="Display server information.")
    @commands.guild_only()
    async def serverinfo(self, ctx: commands.Context) -> None:
        await ctx.defer()
        guild = ctx.guild
        if guild is None:
            await ctx.send("❌ This command can only be used in a server.", ephemeral=True)
            return

        embed = discord.Embed(title=f"🏠 {guild.name}", color=ACCENT)
        if guild.icon:
            embed.set_thumbnail(url=guild.icon.url)
        embed.add_field(name="Owner", value=f"<@{guild.owner_id}>", inline=True)
        embed.add_field(name="Members", value=str(guild.member_count), inline=True)
        embed.add_field(name="Channels", value=str(len(guild.channels)), inline=True)
        embed.add_field(name="Roles", value=str(len(guild.roles)), inline=True)
        embed.add_field(
            name="Created",
            value=discord.utils.format_dt(guild.created_at, style="D"),
            inline=True,
        )
        embed.add_field(name="ID", value=str(guild.id), inline=True)
        await ctx.send(embed=embed)

    # ------------------------------------------------------------------ #
    #  poll
    # ------------------------------------------------------------------ #

    @commands.hybrid_command(name="poll", description="Create a yes/no poll.")
    @app_commands.describe(question="The poll question")
    async def poll(self, ctx: commands.Context, *, question: str) -> None:
        await ctx.defer()
        embed = discord.Embed(
            title="📊 Poll",
            description=f"**{question}**",
            color=ACCENT,
            timestamp=datetime.now(timezone.utc),
        )
        embed.set_footer(text=f"Poll by {ctx.author.display_name}")
        msg = await ctx.send(embed=embed)
        await msg.add_reaction("👍")
        await msg.add_reaction("👎")

    # ------------------------------------------------------------------ #
    #  nick
    # ------------------------------------------------------------------ #

    @commands.hybrid_command(name="nick", description="Change your nickname (or another member's with Manage Nicknames).")
    @app_commands.describe(
        member="Member whose nickname to change (default: yourself)",
        name="New nickname — leave empty to reset",
    )
    @commands.guild_only()
    async def nick(
        self,
        ctx: commands.Context,
        member: discord.Member | None = None,
        *,
        name: str | None = None,
    ) -> None:
        await ctx.defer(ephemeral=True)

        guild = ctx.guild
        if guild is None:
            await ctx.send("❌ This command can only be used in a server.", ephemeral=True)
            return

        invoker = ctx.author
        if not isinstance(invoker, discord.Member):
            await ctx.send("❌ This command can only be used in a server.", ephemeral=True)
            return

        # Resolve target — default to the invoker themselves.
        target: discord.Member = member or invoker
        is_self = target.id == invoker.id

        # ── Pre-flight: invoker permissions ─────────────────────────────
        # Changing someone else's nick requires Manage Nicknames.
        if not is_self and not invoker.guild_permissions.manage_nicknames:
            await ctx.send(
                "❌ You need the **Manage Nicknames** permission to change another member's nickname.",
                ephemeral=True,
            )
            return

        # ── Pre-flight: bot permissions ──────────────────────────────────
        me = guild.me
        if not me.guild_permissions.manage_nicknames:
            await ctx.send(
                "❌ I'm missing the **Manage Nicknames** permission.\n"
                "Please make sure my role has that permission (or Administrator).",
                ephemeral=True,
            )
            return

        # ── Pre-flight: cannot change the server owner's nickname ────────
        if target.id == guild.owner_id:
            await ctx.send(
                "❌ Discord does not allow bots to change the **server owner's** nickname.",
                ephemeral=True,
            )
            return

        # ── Pre-flight: role hierarchy ───────────────────────────────────
        # The bot's highest role must be strictly above the target's.
        if me.top_role <= target.top_role:
            await ctx.send(
                f"❌ I can't edit **{target.display_name}** — their highest role "
                f"(**{target.top_role.name}**, position {target.top_role.position}) "
                f"is at or above my highest role "
                f"(**{me.top_role.name}**, position {me.top_role.position}).",
                ephemeral=True,
            )
            return

        # ── Pre-flight: nickname length ──────────────────────────────────
        if name is not None and len(name) > 32:
            await ctx.send(
                f"❌ Nicknames must be 32 characters or fewer (yours is {len(name)}).",
                ephemeral=True,
            )
            return

        # ── Attempt the edit ─────────────────────────────────────────────
        try:
            await target.edit(
                nick=name,
                reason=f"nick command — requested by {invoker} ({invoker.id})",
            )
        except discord.Forbidden as exc:
            # Still log the raw error so the workflow console shows the real
            # Discord error code + message — useful for diagnosing edge cases.
            print(
                f"[nick] Forbidden when editing {target!r} ({target.id}) "
                f"in {guild!r} ({guild.id}) — code={exc.code} text={exc.text!r}",
                flush=True,
            )
            await ctx.send(
                f"❌ Discord rejected the nickname change (403 Forbidden).\n"
                f"Discord error code `{exc.code}`: {exc.text or '(no message)'}",
                ephemeral=True,
            )
            return
        except discord.HTTPException as exc:
            print(
                f"[nick] HTTPException editing {target!r} in {guild!r} "
                f"— status={exc.status} code={exc.code} text={exc.text!r}",
                flush=True,
            )
            await ctx.send(
                f"❌ Discord API error (HTTP {exc.status}, code `{exc.code}`):\n{exc.text or str(exc)}",
                ephemeral=True,
            )
            return
        except Exception as exc:
            print(
                f"[nick] Unexpected error editing {target!r} in {guild!r}:\n"
                + "".join(traceback.format_exc()),
                flush=True,
            )
            await ctx.send(
                f"❌ Unexpected error (`{type(exc).__name__}`): {exc}",
                ephemeral=True,
            )
            return

        # ── Success ──────────────────────────────────────────────────────
        if is_self:
            msg = "✅ Your nickname has been reset." if name is None else f"✅ Your nickname was changed to **{name}**."
        else:
            msg = (
                f"✅ Reset **{target.display_name}**'s nickname."
                if name is None
                else f"✅ Changed **{target.display_name}**'s nickname to **{name}**."
            )
        await ctx.send(msg, ephemeral=True)

    # ------------------------------------------------------------------ #
    #  afk
    # ------------------------------------------------------------------ #

    @commands.hybrid_command(name="afk", description="Set your AFK status.")
    @app_commands.describe(reason="Why you're going AFK")
    @commands.guild_only()
    async def afk(self, ctx: commands.Context, *, reason: str = "AFK") -> None:
        await ctx.defer()
        guild_store = _afk.setdefault(ctx.guild.id, {})  # type: ignore[union-attr]
        guild_store[ctx.author.id] = reason
        embed = discord.Embed(
            description=f"💤 **{ctx.author.display_name}** is now AFK: {reason}",
            color=ACCENT,
        )
        await ctx.send(embed=embed)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        """Notify when an AFK user is mentioned, and clear AFK on their next message."""
        if message.author.bot or message.guild is None:
            return

        # Resolve the command for this message so we can ignore the ?afk
        # invocation itself — otherwise the listener would remove the status
        # that the command just set, in the same message event.
        ctx = await self.bot.get_context(message)
        is_afk_command = ctx.command is not None and ctx.command.name == "afk"

        guild_store = _afk.get(message.guild.id, {})

        # Clear AFK only on the user's NEXT normal message, not the ?afk command itself.
        if not is_afk_command and message.author.id in guild_store:
            guild_store.pop(message.author.id)
            await message.channel.send(
                f"👋 Welcome back, {message.author.mention}! AFK status removed.",
                delete_after=8,
            )

        # Notify if an AFK user is mentioned.
        for mentioned in message.mentions:
            reason = guild_store.get(mentioned.id)
            if reason:
                await message.channel.send(
                    f"💤 **{mentioned.display_name}** is AFK: {reason}",
                    delete_after=10,
                )

    # ------------------------------------------------------------------ #
    #  avatar
    # ------------------------------------------------------------------ #

    @commands.hybrid_command(name="avatar", description="Show a user's avatar.")
    @app_commands.describe(user="The user whose avatar to show (defaults to you)")
    async def avatar(
        self, ctx: commands.Context, user: discord.User | None = None
    ) -> None:
        await ctx.defer()
        target = user or ctx.author
        embed = discord.Embed(title=f"🖼️ {target.display_name}'s Avatar", color=ACCENT)
        embed.set_image(url=target.display_avatar.url)
        embed.add_field(
            name="Download",
            value=f"[PNG]({target.display_avatar.with_format('png').url}) · "
                  f"[WEBP]({target.display_avatar.with_format('webp').url})",
        )
        await ctx.send(embed=embed)

    # ------------------------------------------------------------------ #
    #  banner
    # ------------------------------------------------------------------ #

    @commands.hybrid_command(name="banner", description="Show a user's banner.")
    @app_commands.describe(user="The user whose banner to show (defaults to you)")
    async def banner(
        self, ctx: commands.Context, user: discord.User | None = None
    ) -> None:
        await ctx.defer()
        target = user or ctx.author
        # Banner requires fetching the full user object.
        fetched = await self.bot.fetch_user(target.id)
        if fetched.banner is None:
            await ctx.send(
                f"❌ **{target.display_name}** has no banner set.", ephemeral=True
            )
            return
        embed = discord.Embed(title=f"🖼️ {target.display_name}'s Banner", color=ACCENT)
        embed.set_image(url=fetched.banner.url)
        await ctx.send(embed=embed)

    # ------------------------------------------------------------------ #
    #  userinfo
    # ------------------------------------------------------------------ #

    @commands.hybrid_command(name="userinfo", description="Display information about a user.")
    @app_commands.describe(user="The user to look up (defaults to you)")
    async def userinfo(
        self, ctx: commands.Context, user: discord.Member | None = None
    ) -> None:
        await ctx.defer()
        target = user or ctx.author
        if not isinstance(target, discord.Member):
            await ctx.send("❌ This command must be used in a server.", ephemeral=True)
            return

        embed = discord.Embed(
            title=f"👤 {target.display_name}",
            color=target.color if target.color.value else ACCENT,
        )
        embed.set_thumbnail(url=target.display_avatar.url)
        embed.add_field(name="Username", value=str(target), inline=True)
        embed.add_field(name="ID", value=str(target.id), inline=True)
        embed.add_field(name="Bot", value="Yes" if target.bot else "No", inline=True)
        embed.add_field(
            name="Account Created",
            value=discord.utils.format_dt(target.created_at, style="D"),
            inline=True,
        )
        embed.add_field(
            name="Joined Server",
            value=discord.utils.format_dt(target.joined_at, style="D") if target.joined_at else "Unknown",
            inline=True,
        )
        roles = [r.mention for r in reversed(target.roles) if r.name != "@everyone"]
        embed.add_field(
            name=f"Roles ({len(roles)})",
            value=" ".join(roles[:10]) + ("…" if len(roles) > 10 else "") if roles else "None",
            inline=False,
        )
        if target.premium_since:
            embed.add_field(
                name="Boosting Since",
                value=discord.utils.format_dt(target.premium_since, style="D"),
                inline=True,
            )
        await ctx.send(embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Utility(bot))
