import discord
from discord.ext import commands
from utils.database import create_db_connection
from mysql.connector import Error
from asyncio import TimeoutError

class TeamCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    def execute_query(self, connection, query, params=None):
        cursor = connection.cursor(dictionary=True)
        try:
            if params:
                cursor.execute(query, params)
            else:
                cursor.execute(query)
            result = cursor.fetchall()
            connection.commit()
            return result
        except Error as e:
            print(f"Error executing query: {e}")
            return None
        finally:
            cursor.close()

    @commands.command(name='teamview')
    async def teamview(self, ctx):
        connection = create_db_connection()
        if not connection:
            await ctx.send("Unable to connect to the database. Please try again later.")
            return

        user_authorid = str(ctx.author.id)
        
        # Get user_id
        query = "SELECT user_id FROM user WHERE user_authorid = %s"
        user_result = self.execute_query(connection, query, (user_authorid,))
        
        if not user_result:
            await ctx.send("Error: You need to register to the bot first.")
            connection.close()
            return
        
        user_id = user_result[0]['user_id']
        
        # Get team_id
        query = "SELECT team_id FROM team WHERE user_id = %s"
        team_result = self.execute_query(connection, query, (user_id,))
        
        if not team_result:
            await ctx.send("You don't have a team yet.")
            connection.close()
            return
        
        team_id = team_result[0]['team_id']
        
        # Get card_ids for the team
        query = """
            SELECT uc.*, c.image, c.card_name, c.xp_cap, w.weapon_name
            FROM team_cards tc
            JOIN user_cards uc ON tc.card_id = uc.card_id AND uc.user_id = %s
            JOIN card c ON uc.card_id = c.card_id
            LEFT JOIN weapon w ON uc.weapon_id = w.weapon_id
            WHERE tc.team_id = %s
        """
        team_cards = self.execute_query(connection, query, (user_id, team_id))
        connection.close()
        
        if not team_cards:
            await ctx.send("Your team is empty.")
            return
        
        # Create embeds for each card
        embeds = []
        for card in team_cards:
            embed = discord.Embed(title=f"{card['card_name']}", color=0x00ff00)
            embed.add_field(name="Level", value=card['current_level'], inline=True)
            embed.add_field(name="XP", value=f"{card['current_xp']}/{card['xp_cap']}", inline=False)
            embed.add_field(name="Weapon", value=card['weapon_name'] or "None", inline=False)
            embed.add_field(name="Power", value=card['power_stat'], inline=False)
            embed.add_field(name="Attack", value=card['attack_stat'], inline=False)
            embed.add_field(name="Health", value=card['health_stat'], inline=False)
            embed.add_field(name="Speed", value=card['speed_stat'], inline=False)
            embed.set_image(url=card['image'])
            embed.set_thumbnail(url=ctx.author.avatar.url)
            embeds.append(embed)
        
        # Send embeds
        if not embeds:
            await ctx.send("Your team is empty.")
            return
        
        current_page = 0
        message = await ctx.send(embed=embeds[current_page])
        
        # Add reaction controls
        await message.add_reaction("⬅️")
        await message.add_reaction("➡️")
        
        def check(reaction, user):
            return user == ctx.author and str(reaction.emoji) in ["⬅️", "➡️"]
        
        while True:
            try:
                reaction, user = await self.bot.wait_for("reaction_add", timeout=60, check=check)
                
                if str(reaction.emoji) == "➡️" and current_page < len(embeds) - 1:
                    current_page += 1
                    await message.edit(embed=embeds[current_page])
                elif str(reaction.emoji) == "⬅️" and current_page > 0:
                    current_page -= 1
                    await message.edit(embed=embeds[current_page])
                
                await message.remove_reaction(reaction, user)
            
            except TimeoutError:
                await message.clear_reactions()
                break

    @commands.command(name='teamadd')
    async def teamadd(self, ctx, *, character_name):
        connection = create_db_connection()
        if not connection:
            await ctx.send("Unable to connect to the database. Please try again later.")
            return

        user_authorid = str(ctx.author.id)
        
        # Get user_id
        query = "SELECT user_id FROM user WHERE user_authorid = %s"
        user_result = self.execute_query(connection, query, (user_authorid,))
        
        if not user_result:
            await ctx.send("Error: You need to register to the bot first.")
            connection.close()
            return
        
        user_id = user_result[0]['user_id']
        
        # Get team_id
        query = "SELECT team_id FROM team WHERE user_id = %s"
        team_result = self.execute_query(connection, query, (user_id,))
        
        if not team_result:
            await ctx.send("You don't have a team yet.")
            connection.close()
            return
        
        team_id = team_result[0]['team_id']
        
        # Look up the card in the card table
        query = "SELECT card_id, type_card FROM card WHERE card_name = %s"
        card_result = self.execute_query(connection, query, (character_name,))
        
        if not card_result:
            await ctx.send(f"The card '{character_name}' doesn't exist.")
            connection.close()
            return
        
        card_id = card_result[0]['card_id']
        card_type = card_result[0]['type_card']
        
        if card_type == "Booster":
            await ctx.send(f"'{character_name}' is a Booster card and cannot be added to your team.")
            connection.close()
            return

        # Check if the user owns the card
        query = "SELECT * FROM user_cards WHERE user_id = %s AND card_id = %s"
        user_card_result = self.execute_query(connection, query, (user_id, card_id))
        
        if not user_card_result:
            await ctx.send(f"You don't own the card '{character_name}'.")
            connection.close()
            return
        
        # Check if the team already has 4 cards
        query = "SELECT COUNT(*) as card_count FROM team_cards WHERE team_id = %s"
        count_result = self.execute_query(connection, query, (team_id,))
        
        if count_result[0]['card_count'] >= 4:
            await ctx.send("Your team already has the maximum of 4 cards.")
            connection.close()
            return

        # Check if the card is in the team
        query = "SELECT * FROM team_cards WHERE team_id = %s AND card_id = %s"
        team_card_result = self.execute_query(connection, query, (team_id, card_id))
        
        if team_card_result:
            await ctx.send(f"'{character_name}' is already in your team.")
            connection.close()
            return

        # Add the card to the team
        query = "INSERT INTO team_cards (team_id, card_id) VALUES (%s, %s)"
        self.execute_query(connection, query, (team_id, card_id))
        
        if card_type == "Support":
            # Get the stats of the support card
            query = """
            SELECT power_stat, attack_stat, health_stat, speed_stat 
            FROM user_cards 
            WHERE user_id = %s AND card_id = %s
            """
            support_stats = self.execute_query(connection, query, (user_id, card_id))[0]
            
            # Get all fighter cards in the team
            query = """
            SELECT uc.card_id, uc.power_stat, uc.attack_stat, uc.health_stat, uc.speed_stat
            FROM team_cards tc
            JOIN user_cards uc ON tc.card_id = uc.card_id
            JOIN card c ON uc.card_id = c.card_id
            WHERE tc.team_id = %s AND uc.user_id = %s AND c.type_card = 'Fighter'
            """
            fighter_cards = self.execute_query(connection, query, (team_id, user_id))
            
            # Update stats of fighter cards
            for fighter in fighter_cards:
                query = """
                UPDATE user_cards
                SET power_stat = power_stat + %s,
                    attack_stat = attack_stat + %s,
                    health_stat = health_stat + %s,
                    speed_stat = speed_stat + %s
                WHERE user_id = %s AND card_id = %s
                """
                self.execute_query(connection, query, (
                    support_stats['power_stat'],
                    support_stats['attack_stat'],
                    support_stats['health_stat'],
                    support_stats['speed_stat'],
                    user_id,
                    fighter['card_id']
                ))
        
        await ctx.send(f"Successfully added '{character_name}' to your team!")
        connection.close()

    @commands.command(name='teamremove')
    async def teamremove(self, ctx, *, character_name):
        connection = create_db_connection()
        if not connection:
            await ctx.send("Unable to connect to the database. Please try again later.")
            return

        user_authorid = str(ctx.author.id)
        
        # Get user_id
        query = "SELECT user_id FROM user WHERE user_authorid = %s"
        user_result = self.execute_query(connection, query, (user_authorid,))
        
        if not user_result:
            await ctx.send("Error: You need to register to the bot first.")
            connection.close()
            return
        
        user_id = user_result[0]['user_id']
        
        # Get team_id
        query = "SELECT team_id FROM team WHERE user_id = %s"
        team_result = self.execute_query(connection, query, (user_id,))
        
        if not team_result:
            await ctx.send("You don't have a team yet.")
            connection.close()
            return
        
        team_id = team_result[0]['team_id']
        
        # Look up the card in the card table
        query = "SELECT card_id, type_card FROM card WHERE card_name = %s"
        card_result = self.execute_query(connection, query, (character_name,))
        
        if not card_result:
            await ctx.send(f"The card '{character_name}' doesn't exist.")
            connection.close()
            return
        
        card_id = card_result[0]['card_id']
        card_type = card_result[0]['type_card']
        
        # Check if the user owns the card
        query = "SELECT * FROM user_cards WHERE user_id = %s AND card_id = %s"
        user_card_result = self.execute_query(connection, query, (user_id, card_id))
        
        if not user_card_result:
            await ctx.send(f"You don't own the card '{character_name}'.")
            connection.close()
            return
        
        # Check if the card is in the team
        query = "SELECT * FROM team_cards WHERE team_id = %s AND card_id = %s"
        team_card_result = self.execute_query(connection, query, (team_id, card_id))
        
        if not team_card_result:
            await ctx.send(f"'{character_name}' is not in your team.")
            connection.close()
            return
        
        # Remove the card from the team
        query = "DELETE FROM team_cards WHERE team_id = %s AND card_id = %s"
        self.execute_query(connection, query, (team_id, card_id))
        
        if card_type == "Support":
            # Get the stats of the support card
            query = """
            SELECT power_stat, attack_stat, health_stat, speed_stat 
            FROM user_cards 
            WHERE user_id = %s AND card_id = %s
            """
            support_stats = self.execute_query(connection, query, (user_id, card_id))[0]
            
            # Get all remaining fighter cards in the team
            query = """
            SELECT uc.card_id, uc.power_stat, uc.attack_stat, uc.health_stat, uc.speed_stat
            FROM team_cards tc
            JOIN user_cards uc ON tc.card_id = uc.card_id
            JOIN card c ON uc.card_id = c.card_id
            WHERE tc.team_id = %s AND uc.user_id = %s AND c.type_card = 'Fighter'
            """
            fighter_cards = self.execute_query(connection, query, (team_id, user_id))
            
            # Update stats of remaining fighter cards
            for fighter in fighter_cards:
                query = """
                UPDATE user_cards
                SET power_stat = power_stat - %s,
                    attack_stat = attack_stat - %s,
                    health_stat = health_stat - %s,
                    speed_stat = speed_stat - %s
                WHERE user_id = %s AND card_id = %s
                """
                self.execute_query(connection, query, (
                    support_stats['power_stat'],
                    support_stats['attack_stat'],
                    support_stats['health_stat'],
                    support_stats['speed_stat'],
                    user_id,
                    fighter['card_id']
                ))
        
        await ctx.send(f"Successfully removed '{character_name}' from your team!")
        connection.close()

async def setup(bot):
    await bot.add_cog(TeamCommands(bot))