import discord
from discord.ext import commands
import aiomysql
import random
import asyncio
import math
import datetime
import traceback
import json
import io
import os
from PIL import Image, ImageDraw, ImageFont
from discord.ui import Button, View

class AutofightCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.image_path = os.path.join(os.path.dirname(__file__), '..', 'images')
        self.db_pool = None
        self.spawn_cooldowns = {}  # Track per-channel cooldowns
        self.message_counters = {}  # Track message activity per channel
        self.spawn_threshold = 50  # Number of messages needed to trigger a spawn chance
        self.spawn_chance = 0.55  # 25% chance to spawn when threshold reached
        self.disallowed_channels = set()  # Set to store channels where spawning is disabled

    async def create_db_pool(self):
        try:
            self.db_pool = await aiomysql.create_pool(
                host='localhost',
                user='root',
                password='',
                db='sololevelingboting',
                port= 3307,
                autocommit=True
            )
            print("Database pool created successfully")
        except Exception as e:
            print(f"Error creating database pool: {e}")
            self.db_pool = None

    async def execute_query(self, query, params=None):
        if not self.db_pool:
            await self.create_db_pool()
            if not self.db_pool:
                return None
                
        async with self.db_pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cursor:
                try:
                    await cursor.execute(query, params)
                    if query.strip().upper().startswith(('SELECT', 'SHOW')):
                        result = await cursor.fetchall()
                        return result
                    else:
                        return cursor.rowcount
                except Exception as e:
                    print(f"Database query error: {e}")
                    print(f"Query: {query}")
                    print(f"Params: {params}")
                    return None

    async def load_disallowed_channels(self):
        """Load disallowed channels from database"""
        query = "SELECT channel_id FROM disallowed_spawn_channels"
        result = await self.execute_query(query)
        
        if result:
            self.disallowed_channels = set(int(row['channel_id']) for row in result)
            print(f"Loaded {len(self.disallowed_channels)} disallowed spawn channels")

    @commands.Cog.listener()
    async def on_ready(self):
        """When bot is ready, load disallowed channels"""
        await self.load_disallowed_channels()

    @commands.Cog.listener()
    async def on_message(self, message):
        # Ignore bot messages
        if message.author.bot:
            return
            
        # Ignore DMs
        if not message.guild:
            return
            
        # Check if channel is in disallowed list
        if message.channel.id in self.disallowed_channels:
            return
            
        # Initialize counter for this channel if it doesn't exist
        channel_id = message.channel.id
        if channel_id not in self.message_counters:
            self.message_counters[channel_id] = 0
            
        # Check cooldown
        current_time = datetime.datetime.now()
        if channel_id in self.spawn_cooldowns:
            time_diff = (current_time - self.spawn_cooldowns[channel_id]).total_seconds()
            if time_diff < 20:  # 30-minute cooldown
                return
                
        # Increment message counter for this channel
        self.message_counters[channel_id] += 1
        
        # Check if threshold is reached
        if self.message_counters[channel_id] >= self.spawn_threshold:
            # Reset counter
            self.message_counters[channel_id] = 0
            
            # Roll for spawn chance
            if random.random() <= self.spawn_chance:
                await self.spawn_wild_card(message.channel)
                # Set cooldown
                self.spawn_cooldowns[channel_id] = current_time

    @commands.command(name="disallow_spawn")
    @commands.has_permissions(administrator=True)
    async def disallow_spawn(self, ctx, channel: discord.TextChannel = None):
        """Disallow card spawning in a specific channel"""
        if not channel:
            channel = ctx.channel
            
        # Check if channel is already disallowed
        if channel.id in self.disallowed_channels:
            await ctx.send(f"Spawning is already disabled in {channel.mention}!")
            return
            
        # Add to database
        query = "INSERT INTO disallowed_spawn_channels (channel_id, guild_id, added_by, added_at) VALUES (%s, %s, %s, %s)"
        params = (str(channel.id), str(ctx.guild.id), str(ctx.author.id), datetime.datetime.now())
        
        result = await self.execute_query(query, params)
        
        if result:
            # Add to memory set
            self.disallowed_channels.add(channel.id)
            await ctx.send(f"✅ Card spawning has been disabled in {channel.mention}")
        else:
            await ctx.send("❌ Failed to disable spawning. Please try again.")

    @commands.command(name="allow_spawn")
    @commands.has_permissions(administrator=True)
    async def allow_spawn(self, ctx, channel: discord.TextChannel = None):
        """Re-enable card spawning in a specific channel"""
        if not channel:
            channel = ctx.channel
            
        # Check if channel is actually disallowed
        if channel.id not in self.disallowed_channels:
            await ctx.send(f"Spawning is already enabled in {channel.mention}!")
            return
            
        # Remove from database
        query = "DELETE FROM disallowed_spawn_channels WHERE channel_id = %s"
        params = (str(channel.id),)
        
        result = await self.execute_query(query, params)
        
        if result:
            # Remove from memory set
            self.disallowed_channels.remove(channel.id)
            await ctx.send(f"✅ Card spawning has been re-enabled in {channel.mention}")
        else:
            await ctx.send("❌ Failed to enable spawning. Please try again.")

    @commands.command(name="list_disallowed")
    @commands.has_permissions(administrator=True)
    async def list_disallowed(self, ctx):
        """List all channels where spawning is disabled"""
        if not self.disallowed_channels:
            await ctx.send("No channels have spawning disabled.")
            return
            
        embed = discord.Embed(
            title="Channels with Spawning Disabled",
            color=discord.Color.blue(),
            description="The following channels have card spawning disabled:"
        )
        
        channel_list = []
        for channel_id in self.disallowed_channels:
            channel = ctx.guild.get_channel(channel_id)
            if channel and channel.guild.id == ctx.guild.id:
                channel_list.append(f"• {channel.mention}")
        
        if not channel_list:
            embed.description = "No channels in this server have spawning disabled."
        else:
            embed.description += "\n" + "\n".join(channel_list)
            
        await ctx.send(embed=embed)

    def get_image_path(self, image_name):
        """Get the full path for an image from the images directory"""
        if not image_name:
            return None
        return os.path.join(self.image_path, image_name)

    async def spawn_wild_card(self, channel):
        if not self.db_pool:
            await self.create_db_pool()
        if not self.db_pool:
            return

        try:
            # Get a random card
            card_query = """
            SELECT *
            FROM card
            WHERE spawn_status = 1 AND seeable = 1
            ORDER BY RAND()
            LIMIT 1
            """
            card_result = await self.execute_query(card_query)
            if not card_result:
                return

            card = card_result[0]
            base_stats = self.get_stats_by_rank(card['card_rank'])

            # Create embed
            embed = discord.Embed(
                title=f"A wild {card['card_name']} appeared!",
                description=f"{card['description']}\n\nRank: {self.get_rank_display(card['card_rank'])}\nType: {card['type_card']}\n\nBe the first to click the button to fight it!",
                color=self.get_rank_color(card['card_rank'])
            )

            embed.add_field(name="Attack", value=f"⚔️ {base_stats['attack']}", inline=True)
            embed.add_field(name="Defense", value=f"🛡️ {base_stats['defense']}", inline=True)
            embed.add_field(name="Speed", value=f"⚡ {base_stats['speed']}", inline=True)
            embed.add_field(name="Health", value=f"❤️ {base_stats['health']}", inline=True)

            files = []
            card_image_path = self.get_image_path(card['image'])
            if card_image_path and os.path.exists(card_image_path):
                card_file = discord.File(card_image_path, filename="card_image.png")
                files.append(card_file)
                embed.set_image(url="attachment://card_image.png")

            view = FightCardView(self, card, base_stats)

            # Send message with embed and image
            await channel.send(embed=embed, view=view, files=files if files else None)

        except Exception as e:
            print(f"Error spawning wild card: {e}")
            traceback.print_exc()

    def get_stats_by_rank(self, rank):
        # Set stats based on rank
        stats = {
            'attack': 0,
            'defense': 0,
            'speed': 0,
            'health': 0
        }
        
        if rank == 5:  # S Rank
            stats = {
                'attack': 3000,
                'defense': 3000,
                'speed': 3000,
                'health': 3000
            }
        elif rank == 4:  # A Rank
            stats = {
                'attack': 2000,
                'defense': 2000,
                'speed': 2000,
                'health': 2000
            }
        elif rank == 3:  # B Rank
            stats = {
                'attack': 1500,
                'defense': 1500,
                'speed': 1500,
                'health': 1500
            }
        elif rank == 2:  # C Rank
            stats = {
                'attack': 1000,
                'defense': 1000,
                'speed': 1000,
                'health': 1000
            }
        elif rank == 1:  # D Rank
            stats = {
                'attack': 750,
                'defense': 750,
                'speed': 750,
                'health': 750
            }
        else:  # E Rank or any other
            stats = {
                'attack': 500,
                'defense': 500,
                'speed': 500,
                'health': 500
            }
            
        # Add some randomness to make each spawn slightly different
        for stat in stats:
            variation = random.uniform(0.9, 1.1)  # ±10% variation
            stats[stat] = int(stats[stat] * variation)
            
        return stats
        
    def get_rank_display(self, rank):
        ranks = {
            5: "S",
            4: "A",
            3: "B", 
            2: "C",
            1: "D",
            0: "E"
        }
        return ranks.get(rank, "Unknown")
        
    def get_rank_color(self, rank):
        colors = {
            5: 0xFF0000,  # S - Red
            4: 0xFFA500,  # A - Orange
            3: 0xFFFF00,  # B - Yellow
            2: 0x00FF00,  # C - Green
            1: 0x0000FF,  # D - Blue
            0: 0x800080   # E - Purple
        }
        return colors.get(rank, 0xFFFFFF)
        
    async def get_user_fighters(self, user_id):
        # Check if user exists
        user_check_query = """
        SELECT user_id, user_authorid
        FROM user
        WHERE user_authorid = %s
        """
        user_result = await self.execute_query(user_check_query, (str(user_id),))
        
        if not user_result:
            return None
            
        user_data = {
            'user_id': user_result[0]['user_id'],
            'user_authorid': user_result[0]['user_authorid'],
            'fighters': []
        }
        
        # Get team ID
        team_query = """
        SELECT team_id
        FROM team
        WHERE user_id = %s
        """
        team_result = await self.execute_query(team_query, (user_data['user_id'],))
        
        if not team_result:
            return user_data
            
        team_id = team_result[0]['team_id']
        
        # Get fighters in team
        fighters_query = """
        SELECT c.card_id, c.card_name, c.image, c.card_rank, uc.power_stat, uc.health_stat, uc.speed_stat, 
            uc.attack_stat, uc.defence_stat, uc.current_level, tc.position,
            uc.current_xp, uc.id_enhancer, uc.fight_count, uc.charm_attack, uc.charm_defence, 
            uc.charm_speed, uc.charm_health,
            uc.attack_count, uc.defence_count, uc.speed_count, uc.health_count
        FROM team_cards tc
        JOIN card c ON tc.card_id = c.card_id
        JOIN user_cards uc ON tc.card_id = uc.card_id AND uc.user_id = %s
        WHERE tc.team_id = %s AND c.type_card = 'Fighter'
        ORDER BY tc.position
        """
        fighters = await self.execute_query(fighters_query, (user_data['user_id'], team_id))
        
        if fighters:
            user_data['fighters'] = fighters
            
        return user_data
        
    async def apply_charm_effects(self, fighters, user_id):
        # Placeholder for charm effect application - implement based on your game mechanics
        for fighter in fighters:
            # Apply charm bonuses to stats
            fighter['attack_stat'] += fighter.get('charm_attack', 0)
            fighter['defence_stat'] += fighter.get('charm_defence', 0)
            fighter['speed_stat'] += fighter.get('charm_speed', 0)
            fighter['health_stat'] += fighter.get('charm_health', 0)
        
        return fighters

    async def update_rewards(self, ctx, user_data, opponent_card, base_stats, user_won):
        try:
            # Base rewards based on card rank
            exp_reward = 50 * (opponent_card['card_rank'] + 1) if user_won else 10
            currency_reward = 100 * (opponent_card['card_rank'] + 1) if user_won else 20
            
            # Update user's currency
            currency_update_query = """
            UPDATE user
            SET won_currency = won_currency + %s
            WHERE user_id = %s
            """
            await self.execute_query(currency_update_query, (currency_reward, user_data['user_id']))
            
            # Update fighter cards XP
            for fighter in user_data['fighters']:
                # Only update if the fighter participated and is still on the team
                xp_update_query = """
                UPDATE user_cards
                SET current_xp = current_xp + %s, fight_count = fight_count + 1
                WHERE user_id = %s AND card_id = %s
                """
                await self.execute_query(xp_update_query, (exp_reward, user_data['user_id'], fighter['card_id']))
            
            # Prepare reward message
            if user_won:
                message = f"🎉 **Victory!** You earned {currency_reward} currency!"
            else:
                message = f"😢 **Defeat!**"
                
            return message
                
        except Exception as e:
            print(f"Error updating rewards: {e}")
            return "Error processing rewards."

    def calculate_effective_damage(self, base_damage, defense_stat):
        # Calculate damage reduction percentage based on defense
        # Higher defense means more damage reduction
        damage_reduction = min(75, defense_stat / 4000 * 100)  # Max 75% reduction
        
        # Apply damage reduction
        effective_damage = base_damage * (1 - damage_reduction / 100)
        
        # Ensure damage is at least 1
        return max(1, int(effective_damage))
        
    async def conduct_wild_fight(self, ctx, user_data, opponent_card, base_stats):
        try:
            # Sort user fighters by position
            user_fighters = sorted(user_data['fighters'], key=lambda x: x.get('position', float('inf')))
            
            if not user_fighters:
                return await ctx.send("You need at least one fighter card in your team to proceed with the fight.")
                
            # Create opponent fighter
            opponent_fighter = {
                'card_name': opponent_card['card_name'],
                'card_id': opponent_card['card_id'],
                'card_rank': opponent_card['card_rank'],
                'attack_stat': base_stats['attack'],
                'defence_stat': base_stats['defense'],
                'speed_stat': base_stats['speed'],
                'health_stat': base_stats['health'],
                'current_health': base_stats['health'],
                'image': opponent_card['image']
            }
            
            # Initialize current_health for user fighters
            for fighter in user_fighters:
                fighter['current_health'] = fighter['health_stat']
            
            # User name
            user = await self.bot.fetch_user(int(user_data['user_authorid']))
            user_name = user.name if user else "Unknown User"
            
            # Opponent name
            opponent_name = f"Wild {opponent_card['card_name']}"
            
            # Create initial embed
            embed = discord.Embed(
                title=f"🏆 Battle: {user_name} vs. {opponent_name}",
                description=f"The battle begins! {user_name}'s {user_fighters[0]['card_name']} against {opponent_name}!",
                color=0xFF9900
            )
            
            # Add stats to embed
            embed.add_field(name=f"{user_fighters[0]['card_name']} (HP)",
                          value=f"❤️ {user_fighters[0]['current_health']}/{user_fighters[0]['health_stat']}",
                          inline=True)
            embed.add_field(name=f"{opponent_fighter['card_name']} (HP)", 
                          value=f"❤️ {opponent_fighter['current_health']}/{opponent_fighter['health_stat']}",
                          inline=True)
            
            # Battle log
            battle_log = []
            
            # Send initial message
            battle_message = await ctx.send(embed=embed)
            
            # Set a consistent speed tie resolution for the entire fight
            user_goes_first_in_tie = random.choice([True, False])
            
            # Main battle loop
            current_user_fighter_index = 0
            
            while current_user_fighter_index < len(user_fighters) and opponent_fighter['current_health'] > 0:
                current_user_fighter = user_fighters[current_user_fighter_index]
                
                # Determine turn order based on speed
                if current_user_fighter['speed_stat'] > opponent_fighter['speed_stat']:
                    fighters = [(current_user_fighter, True), (opponent_fighter, False)]
                elif current_user_fighter['speed_stat'] < opponent_fighter['speed_stat']:
                    fighters = [(opponent_fighter, False), (current_user_fighter, True)]
                else:  # Speed tie
                    if user_goes_first_in_tie:
                        fighters = [(current_user_fighter, True), (opponent_fighter, False)]
                    else:
                        fighters = [(opponent_fighter, False), (current_user_fighter, True)]
                
                # Process turns
                for attacker, is_user_turn in fighters:
                    await asyncio.sleep(1)  # Pause for dramatic effect
                    
                    defender = opponent_fighter if is_user_turn else current_user_fighter
                    
                    # Calculate dodge chance
                    dodge_chance = self.calculate_dodge_chance(attacker['speed_stat'], defender['speed_stat'])
                    dodge_roll = random.random() * 100
                    
                    if dodge_roll < dodge_chance:
                        # Successful dodge
                        battle_log.append(f"🛡️ {defender['card_name']} dodged the attack!")
                    else:
                        # Attack calculations
                        base_damage = random.randint(
                            int(attacker['attack_stat'] * 0.45), 
                            int(attacker['attack_stat'] * 0.55)
                        )
                        
                        # Critical hit calculation
                        crit_chance = min(10, max(5, attacker['attack_stat'] / 100))
                        crit_roll = random.random() * 100
                        is_critical = crit_roll < crit_chance
                        
                        # Apply critical multiplier
                        crit_multiplier = 1.5 if is_critical else 1.0
                        
                        # Calculate final damage
                        effective_damage = self.calculate_effective_damage(
                            int(base_damage * crit_multiplier), 
                            defender['defence_stat']
                        )
                        
                        # Apply damage
                        defender['current_health'] -= effective_damage
                        defender['current_health'] = max(0, defender['current_health'])
                        
                        # Add attack message to log
                        if is_critical:
                            battle_log.append(f"💥 **CRITICAL HIT!** {'⚔️' if is_user_turn else '🛡️'} {attacker['card_name']} → {effective_damage} MASSIVE damage (Base: {base_damage})")
                        else:
                            battle_log.append(f"{'⚔️' if is_user_turn else '🛡️'} {attacker['card_name']} → {effective_damage} damage (Base: {base_damage})")
                    
                    # Update embed with new health values and battle log
                    embed.clear_fields()
                    embed.add_field(name=f"{current_user_fighter['card_name']} (HP)",
                                  value=f"❤️ {current_user_fighter['current_health']}/{current_user_fighter['health_stat']}",
                                  inline=True)
                    embed.add_field(name=f"{opponent_fighter['card_name']} (HP)", 
                                  value=f"❤️ {opponent_fighter['current_health']}/{opponent_fighter['health_stat']}",
                                  inline=True)
                    
                    # Add battle log to embed (keep only last 5 entries to avoid cluttering)
                    log_text = "\n".join(battle_log[-5:])
                    embed.description = f"Battle in progress!\n\n**Battle Log:**\n{log_text}"
                    
                    await battle_message.edit(embed=embed)
                    
                    # Check if defender is defeated
                    if defender['current_health'] <= 0:
                        if is_user_turn:
                            # Opponent defeated
                            battle_log.append(f"🏆 **{opponent_fighter['card_name']} has been defeated!**")
                            break
                        else:
                            # User fighter defeated
                            battle_log.append(f"💀 **{current_user_fighter['card_name']} has been defeated!**")
                            
                            # Move to next fighter if available
                            current_user_fighter_index += 1
                            
                            if current_user_fighter_index < len(user_fighters):
                                current_user_fighter = user_fighters[current_user_fighter_index]
                                battle_log.append(f"🔄 New user card: {current_user_fighter['card_name']}")
                                
                                embed.clear_fields()
                                embed.add_field(name=f"{current_user_fighter['card_name']} (HP)",
                                              value=f"❤️ {current_user_fighter['current_health']}/{current_user_fighter['health_stat']}",
                                              inline=True)
                                embed.add_field(name=f"{opponent_fighter['card_name']} (HP)", 
                                              value=f"❤️ {opponent_fighter['current_health']}/{opponent_fighter['health_stat']}",
                                              inline=True)
                                
                                await battle_message.edit(embed=embed)
                            break
                
                    # If either fighter is defeated, break the turn loop
                    if current_user_fighter['current_health'] <= 0 or opponent_fighter['current_health'] <= 0:
                        break
            
            # Determine winner
            user_won = opponent_fighter['current_health'] <= 0
            
            # Final embed update
            if user_won:
                embed.title = f"🎉 Victory! {user_name} defeated {opponent_name}!"
                embed.color = 0x00FF00  # Green for victory
            else:
                embed.title = f"💀 Defeat! {user_name} was defeated by {opponent_name}!"
                embed.color = 0xFF0000  # Red for defeat
            
            # Add battle log to embed (keep only last 5 entries to avoid cluttering)
            log_text = "\n".join(battle_log[-5:])
            embed.description = f"Battle completed!\n\n**Battle Log:**\n{log_text}"
            
            await battle_message.edit(embed=embed)
            
            # Process rewards
            reward_message = await self.update_rewards(ctx, user_data, opponent_card, base_stats, user_won)
            await ctx.send(reward_message)
            
            return user_won
            
        except Exception as e:
            error_message = f"Error in wild fight: {str(e)}\n{traceback.format_exc()}"
            print(error_message)
            await ctx.send("An error occurred during the battle. Please try again later.")
            return False
    
    def calculate_dodge_chance(self, attacker_speed, defender_speed):
        # Prevent division by zero and ensure positive speeds
        attacker_speed = max(1, attacker_speed)
        defender_speed = max(1, defender_speed)
        
        # Relative speed difference calculation
        speed_ratio = defender_speed / attacker_speed
        
        # Logarithmic scaling to create a balanced dodge chance
        # Ensures dodge chance is always non-negative and caps at 30%
        dodge_chance = min(30, max(0, 30 * (1 - math.log(speed_ratio + 1, 2))))
        
        return dodge_chance

class FightCardView(discord.ui.View):
    def __init__(self, cog, card, base_stats):
        super().__init__(timeout=60)  # Button expires after 60 seconds
        self.cog = cog
        self.card = card
        self.base_stats = base_stats
        self.has_been_used = False
        
    @discord.ui.button(label="Fight!", style=discord.ButtonStyle.danger)
    async def fight_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Prevent multiple people from using the same button
        if self.has_been_used:
            await interaction.response.send_message("Someone else is already fighting this card!", ephemeral=True)
            return
            
        self.has_been_used = True
        
        # Disable the button after it's been used
        button.disabled = True
        await interaction.message.edit(view=self)
        
        # Check if user exists in database
        user_data = await self.cog.get_user_fighters(interaction.user.id)
        
        if not user_data:
            await interaction.response.send_message("You need to be registered before you can fight! Use the registration command first.", ephemeral=True)
            return
            
        if not user_data['fighters']:
            await interaction.response.send_message("You need to have fighter cards in your team before you can fight!", ephemeral=True)
            return
            
        # Apply charm effects to user fighters
        user_data['fighters'] = await self.cog.apply_charm_effects(user_data['fighters'], user_data['user_id'])
        
        # Respond to the interaction to avoid timeout
        await interaction.response.send_message(f"You challenged the wild {self.card['card_name']}! Prepare for battle!")
        
        # Start the fight
        await self.cog.conduct_wild_fight(interaction.channel, user_data, self.card, self.base_stats)
        
    async def on_timeout(self):
        # Disable the button when it times out
        for item in self.children:
            item.disabled = True
            
        # Try to edit the message
        message = self.message
        if message:
            try:
                await message.edit(view=self)
            except:
                pass

async def setup(bot):
   await bot.add_cog(AutofightCommands(bot))

