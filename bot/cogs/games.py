from __future__ import annotations

import random

import discord
from discord import app_commands
from discord.ext import commands

ACCENT = 0xC193CC

# ------------------------------------------------------------------ #
#  Tic-Tac-Toe constants + helpers
# ------------------------------------------------------------------ #

EMPTY = 0
X_MARK = 1
O_MARK = 2

WIN_COMBOS = [
    (0, 1, 2), (3, 4, 5), (6, 7, 8),   # rows
    (0, 3, 6), (1, 4, 7), (2, 5, 8),   # cols
    (0, 4, 8), (2, 4, 6),              # diagonals
]

DICE_EMOJI = {1: "⚀", 2: "⚁", 3: "⚂", 4: "⚃", 5: "⚄", 6: "⚅"}

RPS_EMOJI = {"rock": "🪨", "paper": "📄", "scissors": "✂️"}
RPS_BEATS = {"rock": "scissors", "paper": "rock", "scissors": "paper"}


def _check_winner(board: list[int]) -> int:
    for a, b, c in WIN_COMBOS:
        if board[a] == board[b] == board[c] != EMPTY:
            return board[a]
    return EMPTY


def _ttt_embed(view: "TicTacToeView") -> discord.Embed:
    mark = "❌" if view.current_mark == X_MARK else "⭕"
    return discord.Embed(
        title="🎮 Tic-Tac-Toe",
        description=(
            f"❌ {view.players[0].mention} vs ⭕ {view.players[1].mention}\n\n"
            f"**{view.current_player.display_name}**'s turn — {mark}"
        ),
        color=ACCENT,
    )


# ------------------------------------------------------------------ #
#  Tic-Tac-Toe UI
# ------------------------------------------------------------------ #

class TTTButton(discord.ui.Button["TicTacToeView"]):
    def __init__(self, index: int) -> None:
        super().__init__(
            style=discord.ButtonStyle.secondary,
            label="\u200b",   # zero-width space so button renders without text
            row=index // 3,
        )
        self.index = index

    async def callback(self, interaction: discord.Interaction) -> None:
        view: TicTacToeView = self.view  # type: ignore[assignment]

        if interaction.user.id != view.current_player.id:
            await interaction.response.send_message("❌ It's not your turn!", ephemeral=True)
            return

        # Place mark
        view.board[self.index] = view.current_mark
        self.label = "❌" if view.current_mark == X_MARK else "⭕"
        self.style = (
            discord.ButtonStyle.danger
            if view.current_mark == X_MARK
            else discord.ButtonStyle.primary
        )
        self.disabled = True

        winner = _check_winner(view.board)
        if winner:
            view._disable_all()
            view.stop()
            winner_player = view.players[winner - 1]
            embed = discord.Embed(
                title="🎮 Tic-Tac-Toe",
                description=f"🏆 **{winner_player.display_name}** wins!",
                color=ACCENT,
            )
            await interaction.response.edit_message(embed=embed, view=view)
            return

        if EMPTY not in view.board:
            view._disable_all()
            view.stop()
            embed = discord.Embed(
                title="🎮 Tic-Tac-Toe",
                description="🤝 It's a draw!",
                color=ACCENT,
            )
            await interaction.response.edit_message(embed=embed, view=view)
            return

        # Switch turn
        view.current_mark = O_MARK if view.current_mark == X_MARK else X_MARK
        view.current_player = view.players[view.current_mark - 1]
        await interaction.response.edit_message(embed=_ttt_embed(view), view=view)


class TicTacToeView(discord.ui.View):
    def __init__(self, player1: discord.Member, player2: discord.Member) -> None:
        super().__init__(timeout=120)
        self.players: list[discord.Member] = [player1, player2]
        self.current_player: discord.Member = player1
        self.current_mark: int = X_MARK
        self.board: list[int] = [EMPTY] * 9

        for i in range(9):
            self.add_item(TTTButton(i))

    def _disable_all(self) -> None:
        for child in self.children:
            if isinstance(child, discord.ui.Button):
                child.disabled = True

    async def on_timeout(self) -> None:
        self._disable_all()


# ------------------------------------------------------------------ #
#  Cog
# ------------------------------------------------------------------ #

class Games(commands.Cog):
    """Fun games: rps, coinflip, dice, tictactoe."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @commands.hybrid_command(name="rps", aliases=["Rps"], description="Play rock, paper, scissors against the bot.")
    @app_commands.describe(choice="rock, paper, or scissors")
    @app_commands.choices(choice=[
        app_commands.Choice(name="Rock 🪨", value="rock"),
        app_commands.Choice(name="Paper 📄", value="paper"),
        app_commands.Choice(name="Scissors ✂️", value="scissors"),
    ])
    @commands.cooldown(1, 3, commands.BucketType.user)
    async def rps(self, ctx: commands.Context, choice: str) -> None:
        await ctx.defer()
        choice = choice.strip().lower()
        if choice not in RPS_EMOJI:
            await ctx.send("❌ Choose `rock`, `paper`, or `scissors`.", ephemeral=True)
            return

        bot_choice = random.choice(list(RPS_EMOJI))

        if choice == bot_choice:
            result, color = "🤝 It's a tie!", ACCENT
        elif RPS_BEATS[choice] == bot_choice:
            result, color = "🏆 You win!", 0x57F287
        else:
            result, color = "💀 I win!", 0xED4245

        embed = discord.Embed(title="🪨 Rock · Paper · Scissors", color=color)
        embed.add_field(name="Your pick", value=f"{RPS_EMOJI[choice]} **{choice.capitalize()}**", inline=True)
        embed.add_field(name="My pick", value=f"{RPS_EMOJI[bot_choice]} **{bot_choice.capitalize()}**", inline=True)
        embed.add_field(name="Result", value=f"**{result}**", inline=False)
        await ctx.send(embed=embed)

    @commands.hybrid_command(name="coinflip", aliases=["Coinflip"], description="Flip a coin.")
    @commands.cooldown(1, 3, commands.BucketType.user)
    async def coinflip(self, ctx: commands.Context) -> None:
        await ctx.defer()
        result = random.choice(["Heads", "Tails"])
        emoji = "🌕" if result == "Heads" else "🌑"
        embed = discord.Embed(
            title="🪙 Coin Flip",
            description=f"The coin landed on… **{result}!** {emoji}",
            color=ACCENT,
        )
        await ctx.send(embed=embed)

    @commands.hybrid_command(name="dice", aliases=["Dice"], description="Roll a dice.")
    @app_commands.describe(sides="Number of sides (default 6, max 100)")
    @commands.cooldown(1, 3, commands.BucketType.user)
    async def dice(self, ctx: commands.Context, sides: int = 6) -> None:
        await ctx.defer()
        if not (2 <= sides <= 100):
            await ctx.send("❌ Sides must be between 2 and 100.", ephemeral=True)
            return
        result = random.randint(1, sides)
        emoji = DICE_EMOJI.get(result, "🎲")
        embed = discord.Embed(
            title=f"🎲 Dice Roll (d{sides})",
            description=f"You rolled… **{result}!** {emoji}",
            color=ACCENT,
        )
        await ctx.send(embed=embed)

    @commands.hybrid_command(name="tictactoe", aliases=["Tictactoe"], description="Challenge another user to tic-tac-toe.")
    @app_commands.describe(member="The player to challenge")
    @commands.guild_only()
    async def tictactoe(self, ctx: commands.Context, member: discord.Member) -> None:
        await ctx.defer()
        if member.id == ctx.author.id:
            await ctx.send("❌ You can't play against yourself!", ephemeral=True)
            return
        if member.bot:
            await ctx.send("❌ You can't challenge a bot!", ephemeral=True)
            return

        view = TicTacToeView(ctx.author, member)  # type: ignore[arg-type]
        await ctx.send(embed=_ttt_embed(view), view=view)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Games(bot))
