import discord
from discord.ext import commands
import random
from datetime import datetime, timedelta
from utils.database import create_db_connection
from mysql.connector import Error
import asyncio
import math

class ConnectFourGame:
    def __init__(self, player1: discord.Member, player2: discord.Member, bot_mode: bool = False):
        self.board = [[0 for _ in range(8)] for _ in range(8)]
        self.players = {1: player1, 2: player2}
        self.current_turn = 1
        self.game_over = False
        self.winner = None
        self.bot_mode = bot_mode

    def drop_piece(self, column: int) -> bool:
        """
        Attempt to drop a piece in the specified column.
        Returns True if successful, False otherwise.
        """
        for row in range(7, -1, -1):
            if self.board[row][column] == 0:
                self.board[row][column] = self.current_turn
                return True
        return False

    def get_valid_columns(self) -> list:
        """
        Return list of columns that are not full.
        """
        return [col for col in range(8) if self.board[0][col] == 0]

    def bot_choose_move(self, difficulty: str = "medium") -> int:
        """
        Bot strategy for choosing a column.
        """
        valid_columns = self.get_valid_columns()
        
        if difficulty == "medium":
            # Medium difficulty: 
            # 1. Check for immediate winning move
            # 2. Block opponent's winning move
            # 3. Choose randomly from remaining columns
            
            # Check for winning move
            for col in valid_columns:
                temp_board = [row.copy() for row in self.board]
                for row in range(7, -1, -1):
                    if temp_board[row][col] == 0:
                        temp_board[row][col] = self.current_turn
                        if self.check_winner_on_board(temp_board, self.current_turn):
                            return col
                        break
            
            # Check for blocking opponent's winning move
            opponent_turn = 3 - self.current_turn
            for col in valid_columns:
                temp_board = [row.copy() for row in self.board]
                for row in range(7, -1, -1):
                    if temp_board[row][col] == 0:
                        temp_board[row][col] = opponent_turn
                        if self.check_winner_on_board(temp_board, opponent_turn):
                            return col
                        break
            
            # If no strategic move, choose randomly
            return random.choice(valid_columns)

    def check_winner_on_board(self, board, player):
        """
        Check winner on a specific board configuration.
        """
        # Horizontal check
        for r in range(8):
            for c in range(5):
                if (board[r][c] == player and 
                    board[r][c+1] == player and 
                    board[r][c+2] == player and 
                    board[r][c+3] == player):
                    return True

        # Vertical check
        for r in range(5):
            for c in range(8):
                if (board[r][c] == player and 
                    board[r+1][c] == player and 
                    board[r+2][c] == player and 
                    board[r+3][c] == player):
                    return True

        # Diagonal checks
        # Positive slope diagonal
        for r in range(5):
            for c in range(5):
                if (board[r][c] == player and 
                    board[r+1][c+1] == player and 
                    board[r+2][c+2] == player and 
                    board[r+3][c+3] == player):
                    return True

        # Negative slope diagonal
        for r in range(5):
            for c in range(3, 8):
                if (board[r][c] == player and 
                    board[r+1][c-1] == player and 
                    board[r+2][c-2] == player and 
                    board[r+3][c-3] == player):
                    return True

        return False

    def check_winner(self) -> bool:
        """
        Check if the current player has won.
        """
        return self.check_winner_on_board(self.board, self.current_turn)

    def is_board_full(self) -> bool:
        """
        Check if the board is completely full.
        """
        return all(self.board[0][c] != 0 for c in range(8))

class ConnectFourInvitationView(discord.ui.View):
    def __init__(self, challenger: discord.Member, challenged: discord.Member, parent_command, bot_mode: bool = False):
        super().__init__(timeout=60)  # 1 minute to respond
        self.challenger = challenger
        self.challenged = challenged
        self.parent_command = parent_command
        self.response_received = False
        self.bot_mode = bot_mode

    @discord.ui.button(label="Accept", style=discord.ButtonStyle.green, emoji="✅")
    async def accept_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Ensure only the challenged user can accept
        if not self.bot_mode and interaction.user != self.challenged:
            await interaction.response.send_message(
                "Only the challenged player can accept this invitation!", 
                ephemeral=True
            )
            return

        self.response_received = True
        self.stop()  # Stop the view to prevent further interactions

        # Create game view
        view = ConnectFourView(
            self.challenger, 
            self.challenged if not self.bot_mode else self.parent_command.bot.user, 
            self.parent_command,
            bot_mode=self.bot_mode
        )
        embed = self.parent_command.create_game_embed(view.game)
        await interaction.response.edit_message(
            content=None,
            embed=embed, 
            view=view
        )

    @discord.ui.button(label="Decline", style=discord.ButtonStyle.red, emoji="❌")
    async def decline_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Ensure only the challenged user can decline
        if not self.bot_mode and interaction.user != self.challenged:
            await interaction.response.send_message(
                "Only the challenged player can decline this invitation!", 
                ephemeral=True
            )
            return

        self.response_received = True
        self.stop()  # Stop the view to prevent further interactions

        # Create decline embed
        embed = discord.Embed(
            title="Game Invitation Declined",
            description=f"{self.challenged.mention} declined the Connect 4 challenge from {self.challenger.mention}." if not self.bot_mode 
                        else "Bot game cancelled.",
            color=discord.Color.red()
        )
        await interaction.response.edit_message(content=None, embed=embed, view=None)

    async def on_timeout(self):
        # If no response within timeout
        if not self.response_received:
            embed = discord.Embed(
                title="Game Invitation Expired",
                description=f"The Connect 4 invitation from {self.challenger.mention} to {self.challenged.mention} has expired." if not self.bot_mode 
                            else "Bot game timed out.",
                color=discord.Color.orange()
            )
            try:
                await self.message.edit(content=None, embed=embed, view=None)
            except:
                pass  # In case the message was already deleted

class ConnectFourView(discord.ui.View):
    def __init__(self, challenger: discord.Member, challenged: discord.Member, parent: object, bot_mode: bool = False):
        super().__init__(timeout=300)  # 5 minute timeout
        self.game = ConnectFourGame(challenger, challenged, bot_mode)
        self.parent = parent  # Store the parent command
        self.bot_mode = bot_mode
        
        # Create column buttons
        self.column_buttons = []
        for i in range(8):
            button = discord.ui.Button(
                style=discord.ButtonStyle.blurple, 
                label=str(i+1),
                custom_id=f"column_{i}"
            )
            button.callback = self.create_column_callback(i)
            self.add_item(button)
            self.column_buttons.append(button)
        
        # Forfeit button
        forfeit_button = discord.ui.Button(
            style=discord.ButtonStyle.red, 
            label="Forfeit",
            custom_id="forfeit"
        )
        forfeit_button.callback = self.forfeit_game
        self.add_item(forfeit_button)

        # Initial button state setup for bot mode
        if self.bot_mode:
            self.update_button_states()

    def disable_all_buttons(self):
        """
        Disable all buttons in the view.
        """
        for item in self.children:
            if isinstance(item, discord.ui.Button):
                item.disabled = True

    def update_button_states(self):
        """
        Update button states based on current turn and bot mode.
        """
        if self.bot_mode:
            # In bot mode, disable buttons when it's bot's turn
            is_user_turn = self.game.current_turn == 1
            for button in self.column_buttons:
                # Disable if it's bot's turn, or if column is full
                column = int(button.label) - 1
                button.disabled = (not is_user_turn) or (self.game.board[0][column] != 0)

    def create_column_callback(self, column: int):
        """
        Create a dynamic callback for each column button.
        """
        async def column_callback(interaction: discord.Interaction):
            try:
                # Validate user is in the game
                if not self.bot_mode and interaction.user not in [self.game.players[1], self.game.players[2]]:
                    await interaction.response.send_message(
                        "You're not part of this game. Use `sl c4 @user` to start your own!", 
                        ephemeral=True
                    )
                    return

                # Get current player's number
                player_number = 1 if interaction.user == self.game.players[1] else 2

                # Check if it's the correct player's turn
                if not self.bot_mode and player_number != self.game.current_turn:
                    await interaction.response.send_message(
                        f"It's {self.game.players[self.game.current_turn].mention}'s turn!", 
                        ephemeral=True
                    )
                    return

                # Attempt to drop piece
                if self.game.drop_piece(column):
                    # Check for winner or draw
                    if self.game.check_winner():
                        self.game.game_over = True
                        self.game.winner = interaction.user
                        self.disable_all_buttons()  # Disable all buttons when the game is over
                    
                    elif self.game.is_board_full():
                        self.game.game_over = True
                        self.disable_all_buttons()  # Disable all buttons when the game is over

                    # Switch turns if game continues
                    if not self.game.game_over:
                        self.game.current_turn = 3 - self.game.current_turn  # Toggle between 1 and 2
                        # Update button states for bot mode
                        self.update_button_states()

                    # Create an embed
                    embed = self.parent.create_game_embed(self.game)
                    
                    # Game over conditions
                    if self.game.game_over:
                        if self.game.winner:
                            embed.description += f"\n\n🏆 Winner: {self.game.winner.mention}! 🎉"
                        else:
                            embed.description += "\n\n📊 Draw! No more moves possible."

                    # Respond to interaction
                    await interaction.response.edit_message(embed=embed, view=self)

                    # Bot move if in bot mode and game not over
                    if self.bot_mode and not self.game.game_over:
                        await self.bot_move(interaction.message)

                else:
                    await interaction.response.send_message(
                        "This column is full. Choose another!", 
                        ephemeral=True
                    )
            except Exception as e:
                print(f"Error in column callback: {e}")
                await interaction.response.send_message(
                    "An error occurred. Please try again.", 
                    ephemeral=True
                )

        return column_callback


    async def bot_move(self, message):
        """
        Handles bot's move in bot mode.
        """
        # Disable user buttons before bot move
        self.update_button_states()
        
        # Wait 2 seconds to simulate thinking
        await asyncio.sleep(2)

        # Choose column
        column = self.game.bot_choose_move()

        # Drop piece
        if self.game.drop_piece(column):
            # Check for winner or draw
            if self.game.check_winner():
                self.game.game_over = True
                self.game.winner = self.game.players[2]  # Bot is always player 2
                self.disable_all_buttons()
            
            elif self.game.is_board_full():
                self.game.game_over = True
                self.disable_all_buttons()

            # Switch turns if game continues
            if not self.game.game_over:
                self.game.current_turn = 1
                # Update button states to enable user buttons
                self.update_button_states()

            # Create an embed
            embed = self.parent.create_game_embed(self.game)
            
            # Game over conditions
            if self.game.game_over:
                if self.game.winner:
                    embed.description += f"\n\n🏆 Winner: {self.game.winner.mention}! 🎉"
                else:
                    embed.description += "\n\n📊 Draw! No more moves possible."

            # Edit message
            await message.edit(embed=embed, view=self)

    async def forfeit_game(self, interaction: discord.Interaction):
        """
        Allow a player to forfeit the game.
        """
        try:
            # Validate user is in the game
            if not self.bot_mode and interaction.user not in [self.game.players[1], self.game.players[2]]:
                await interaction.response.send_message(
                    "You're not part of this game. Use `sl c4 @user` to start your own!", 
                    ephemeral=True
                )
                return

            # Determine the forfeit
            self.game.game_over = True
            self.game.winner = (
                self.game.players[2] if interaction.user == self.game.players[1] 
                else self.game.players[1]
            )
            
            # Disable all buttons
            self.disable_all_buttons()

            # Create final embed
            embed = self.parent.create_game_embed(self.game)
            embed.description += f"\n\n🏁 {interaction.user.mention} forfeited!\n🏆 Winner: {self.game.winner.mention}! 🎉"

            await interaction.response.edit_message(embed=embed, view=self)
        except Exception as e:
            print(f"Error in forfeit game: {e}")
            await interaction.response.send_message(
                "An error occurred during forfeit. Please try again.", 
                ephemeral=True
            )

    async def on_timeout(self):
        """
        Handle game timeout if no action is taken.
        """
        self.disable_all_buttons()

class ConnectFourCommand(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.active_games = {}

    def check_user_exists(self, connection, user_id: str) -> bool:
        """
        Check if a user exists in the database.
        """
        try:
            cursor = connection.cursor(dictionary=True)
            query = "SELECT user_id FROM user WHERE user_authorid = %s"
            cursor.execute(query, (user_id,))
            result = cursor.fetchone()
            cursor.close()
            return result is not None
        except Error as e:
            print(f"Database error: {e}")
            return False

    def create_game_embed(self, game: ConnectFourGame) -> discord.Embed:
        """
        Create an embed representing the current game state.
        """
        # Unicode block characters for visualization
        board_emojis = {
            0: "⚫", # Empty
            1: "🔴", # Player 1 (Challenger)
            2: "🟡"  # Player 2 (Challenged/Bot)
        }

        # Convert board to emojis
        board_str = "\n".join(
            "".join(board_emojis[cell] for cell in row) 
            for row in game.board
        )
        
        # Column numbers
        column_nums = "1️⃣2️⃣3️⃣4️⃣5️⃣6️⃣7️⃣8️⃣"

        # Create embed
        embed = discord.Embed(
            title="Connect 4 Game 🎲",
            description=f"{column_nums}\n{board_str}\n\n" +
                        f"Current Turn: {game.players[game.current_turn].mention}",
            color=discord.Color.blurple()
        )
        embed.add_field(
            name="Players", 
            value=f"🔴 {game.players[1].display_name}\n🟡 {game.players[2].display_name}"
        )
        return embed

    @commands.command(name="c4")
    async def start_connect_four(self, ctx, opponent: discord.Member = None):
        """
        Start a Connect 4 game.
        If no opponent is specified, play against the bot.
        """
        # Bot mode if no opponent specified
        bot_mode = opponent is None
        
        # If no opponent, set opponent to bot
        if bot_mode:
            opponent = self.bot.user

        # Prevent playing with self or bot when manually challenging
        if not bot_mode and (opponent == ctx.author or opponent == self.bot.user):
            await ctx.send("You can't play Connect 4 with yourself or the bot!")
            return

        # Create database connection
        connection = await create_db_connection()
        if not connection:
            await ctx.send("Database connection error. Please try again later.")
            return

        # Check if both users are registered
        not_registered_users = []
        
        # Check challenger
        if not self.check_user_exists(connection, str(ctx.author.id)):
            not_registered_users.append(ctx.author)
        
        # Check challenged opponent (skip for bot mode)
        if not bot_mode and not self.check_user_exists(connection, str(opponent.id)):
            not_registered_users.append(opponent)

        # Close the database connection
        connection.close()

        # If any users are not registered, create an embed
        if not_registered_users:
            unregistered_mentions = " ".join(user.mention for user in not_registered_users)
            embed = discord.Embed(
                title="Registration Required",
                description=f"{unregistered_mentions} needs to register first!",
                color=discord.Color.red()
            )
            embed.add_field(
                name="How to Register",
                value="Use the `sl register` command to create an account and join the game."
            )
            await ctx.send(embed=embed)
            return

        # Create invitation view
        view = ConnectFourInvitationView(
            ctx.author, 
            opponent if not bot_mode else self.bot.user, 
            self, 
            bot_mode=bot_mode
        )
        
        # Create invitation embed
        if bot_mode:
            embed = discord.Embed(
                title="Connect 4 Game 🎲",
                description=f"{ctx.author.mention} is starting a game against the bot!",
                color=discord.Color.blurple()
            )
        else:
            embed = discord.Embed(
                title="Connect 4 Game Invitation 🎲",
                description=f"{ctx.author.mention} has challenged {opponent.mention} to a game of Connect 4!",
                color=discord.Color.blurple()
            )
        
        # Send the invitation
        view.message = await ctx.send(embed=embed, view=view)

async def setup(bot):
    await bot.add_cog(ConnectFourCommand(bot))