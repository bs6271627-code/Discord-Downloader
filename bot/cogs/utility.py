from __future__ import annotations

import traceback
import time
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands

ACCENT = 0xC193CC  # #c193cc

# ── Application emojis ───────────────────────────────────────────────────────
_CROWN_EMOJI = "<:crown:1527961577171451945>"

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

    @commands.hybrid_command(name="serverinfo", aliases=["si"], description="Display server information.")
    @commands.guild_only()
    async def serverinfo(self, ctx: commands.Context) -> None:  # noqa: C901
        await ctx.defer()
        guild = ctx.guild
        if guild is None:
            await ctx.send("❌ This command can only be used in a server.", ephemeral=True)
            return

        # ── About ────────────────────────────────────────────────────────
        owner_mention = f"<@{guild.owner_id}>"
        created_dt    = discord.utils.format_dt(guild.created_at, style="D")
        created_rel   = discord.utils.format_dt(guild.created_at, style="R")
        about_val = (
            f"> **Name** — {guild.name}\n"
            f"> **ID** — `{guild.id}`\n"
            f"> **Owner** {_CROWN_EMOJI} — {owner_mention}\n"
            f"> **Created** — {created_dt} *({created_rel})*\n"
            f"> **Members** — {guild.member_count or 0:,}"
        )

        # ── Description ──────────────────────────────────────────────────
        desc_val = (
            f"> {guild.description}"
            if guild.description
            else "> *No description set.*"
        )

        # ── Features ─────────────────────────────────────────────────────
        if guild.features:
            pills = "  ·  ".join(
                f"`{f.replace('_', ' ').title()}`" for f in sorted(guild.features)
            )
            features_val = pills if len(pills) <= 1000 else pills[:997] + "…"
        else:
            features_val = "*No features enabled.*"

        # ── Extras ───────────────────────────────────────────────────────
        afk_ch      = guild.afk_channel.mention if guild.afk_channel else "None"
        afk_timeout = f"{guild.afk_timeout // 60} min" if guild.afk_timeout else "None"
        sys_ch      = guild.system_channel.mention if guild.system_channel else "None"
        rules_ch    = guild.rules_channel.mention if guild.rules_channel else "None"
        extras_val = (
            f"**Verification** — {str(guild.verification_level).replace('_', ' ').title()}"
            f"  ·  **NSFW Level** — {str(guild.nsfw_level).replace('_', ' ').title()}\n"
            f"**Explicit Filter** — {str(guild.explicit_content_filter).replace('_', ' ').title()}"
            f"  ·  **Locale** — {guild.preferred_locale}\n"
            f"**AFK Channel** — {afk_ch}  ·  **AFK Timeout** — {afk_timeout}\n"
            f"**System Channel** — {sys_ch}  ·  **Rules Channel** — {rules_ch}\n"
            f"**Max Bitrate** — {guild.bitrate_limit // 1000} kbps"
        )

        # ── Members ──────────────────────────────────────────────────────
        cached        = list(guild.members)   # partial without members intent
        total_members = guild.member_count or 0
        if cached:
            humans     = sum(1 for m in cached if not m.bot)
            bots_count = sum(1 for m in cached if m.bot)
            online     = sum(
                1 for m in cached
                if hasattr(m, "status") and m.status != discord.Status.offline
            )
            online_str = f"{online:,}"
        else:
            humans     = "N/A"
            bots_count = "N/A"
            online_str = "N/A"

        # 3-column inline grid — section header on first column
        mem_col1 = f"**Total**\n{total_members:,}\n\u200b\n**Online**\n{online_str}"
        mem_col2 = f"**Humans**\n{humans:,}" if isinstance(humans, int) else f"**Humans**\n{humans}"
        mem_col3 = f"**Bots**\n{bots_count:,}" if isinstance(bots_count, int) else f"**Bots**\n{bots_count}"

        # ── Channels ─────────────────────────────────────────────────────
        news_count = sum(
            1 for ch in guild.channels
            if isinstance(ch, discord.TextChannel) and ch.is_news()
        )
        channels_val = (
            f"**Text** — {len(guild.text_channels)}  ·  "
            f"**Voice** — {len(guild.voice_channels)}  ·  "
            f"**Categories** — {len(guild.categories)}\n"
            f"**Forum** — {len(guild.forums)}  ·  "
            f"**Stage** — {len(guild.stage_channels)}  ·  "
            f"**Announcement** — {news_count}  ·  "
            f"**Threads** — {len(guild.threads)}"
        )

        # ── Emoji Info ────────────────────────────────────────────────────
        regular_emojis  = sum(1 for e in guild.emojis if not e.animated)
        animated_emojis = sum(1 for e in guild.emojis if e.animated)
        sticker_count   = len(guild.stickers)
        total_emoji     = regular_emojis + animated_emojis + sticker_count
        emoji_val = (
            f"**Regular** — {regular_emojis}  ·  "
            f"**Animated** — {animated_emojis}  ·  "
            f"**Stickers** — {sticker_count}  ·  "
            f"**Total** — {total_emoji}"
        )

        # ── Boost Status ──────────────────────────────────────────────────
        boost_role = (
            guild.premium_subscriber_role.mention
            if guild.premium_subscriber_role
            else "None"
        )
        boost_count = guild.premium_subscription_count or 0
        # 3-column inline grid
        boost_col1 = f"**Level**\n{guild.premium_tier}"
        boost_col2 = f"**Boosts**\n{boost_count}"
        boost_col3 = f"**Booster Role**\n{boost_role}"

        # ── Server Roles ──────────────────────────────────────────────────
        roles = [r for r in reversed(guild.roles) if r.name != "@everyone"]
        if roles:
            shown      = roles[:20]
            role_pills = " ".join(r.mention for r in shown)
            if len(roles) > 20:
                role_pills += f"\n**+{len(roles) - 20} more…**"
        else:
            role_pills = "*No roles configured.*"

        # ── Build embed ───────────────────────────────────────────────────
        embed = discord.Embed(title=guild.name, color=ACCENT)

        # Thumbnail: server icon → bot avatar
        if guild.icon:
            embed.set_thumbnail(url=guild.icon.url)
        elif self.bot.user:
            embed.set_thumbnail(url=self.bot.user.display_avatar.url)

        # Server banner as bottom image
        if guild.banner:
            embed.set_image(url=guild.banner.url)

        # ── Fields (order preserved per spec) ─────────────────────────────
        embed.add_field(name="__**About**__",       value=about_val,    inline=False)
        embed.add_field(name="__**Description**__", value=desc_val,     inline=False)
        embed.add_field(name="__**Features**__",    value=features_val, inline=False)
        embed.add_field(name="__**Extras**__",      value=extras_val,   inline=False)

        # Members — 3-column inline grid
        embed.add_field(name="__**Members**__", value=mem_col1,  inline=True)
        embed.add_field(name="\u200b",          value=mem_col2,  inline=True)
        embed.add_field(name="\u200b",          value=mem_col3,  inline=True)

        embed.add_field(name="__**Channels**__",    value=channels_val, inline=False)
        embed.add_field(name="__**Emoji Info**__",  value=emoji_val,    inline=False)

        # Boost — 3-column inline grid
        embed.add_field(name="__**Boost Status**__", value=boost_col1, inline=True)
        embed.add_field(name="\u200b",               value=boost_col2, inline=True)
        embed.add_field(name="\u200b",               value=boost_col3, inline=True)

        embed.add_field(name="__**Server Roles**__", value=role_pills, inline=False)

        # Footer
        embed.set_footer(
            text=f"Requested by {ctx.author.display_name}",
            icon_url=ctx.author.display_avatar.url,
        )
        embed.timestamp = datetime.now(timezone.utc)

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

    @commands.hybrid_command(name="avatar", aliases=["av"], description="Show a user's avatar.")
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

    @commands.hybrid_command(name="userinfo", aliases=["ui"], description="Display information about a user.")
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
