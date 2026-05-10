import discord
from discord.ext import commands
import random
from datetime import datetime
from utils.database import create_db_connection
from mysql.connector import Error
import asyncio
from typing import List, Dict, Union
from enum import Enum
import logging
import os
import traceback
intents = discord.Intents.default()
intents.message_content = True
intents.reactions = True

# Constants
DUNGEON_GATE_IMAGE = "https://staticg.sportskeeda.com/editor/2024/03/078be-17113304718546-1920.jpg"
# Constants
EASY_DUNGEON_IMAGE = "https://i.imgur.com/nLFDb29.jpeg"
MEDIUM_DUNGEON_IMAGE = "https://i.imgur.com/5WfEsHQ.png"
HARD_DUNGEON_IMAGE = "https://i.imgur.com/XiqKiv3.png"

class DungeonDifficulty(Enum):
    EASY = ("Easy", "🟢", EASY_DUNGEON_IMAGE, "E-D", "The Forest Goblin Gate", 0x4CAF50, "🗡️", "🏺", "💎", 1)
    MEDIUM = ("Medium", "🟡", MEDIUM_DUNGEON_IMAGE, "C-B", "The Giant Spider's Nest Gate", 0xFFC107, "⚔️", "🏺🏺", "💎💎", 2)
    HARD = ("Hard", "🔴", HARD_DUNGEON_IMAGE, "A-S", "The Hunters Guild Gate", 0xF44336, "🗡️⚔️", "🏺🏺🏺", "💎💎💎", 3)

class DungeonCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.green_square = "<:gre1295034850298040494__5_remove:1295043081774698538>"
        self.red_square = "<:redc:1295036894417125550>"
        self.gray_square = "<:gray:1295036907595759697>"
        self.empty_square = "⬜"
        self.dungeons_in_progress = set()  # Track active dungeons
        self.image_path = os.path.join(os.path.dirname(__file__), '..', 'images')
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

    def get_user_id(self, connection, discord_id):
        query = "SELECT user_id FROM user WHERE user_authorid = %s"
        result = self.execute_query(connection, query, (str(discord_id),))
        return result[0]['user_id'] if result else None

    def get_inventory_id(self, connection, user_id):
        query = "SELECT inventory_id FROM inventory WHERE user_id = %s"
        result = self.execute_query(connection, query, (user_id,))
        return result[0]['inventory_id'] if result else None

    def get_team_id(self, connection, user_id):
        query = "SELECT team_id FROM team WHERE user_id = %s"
        result = self.execute_query(connection, query, (user_id,))
        return result[0]['team_id'] if result else None

    def get_solo_dungeons(self, connection):
        query = "SELECT * FROM dungeon WHERE dungeon_rank = 1"
        return self.execute_query(connection, query)

    @commands.command(name="dungeon")
    async def dungeon(self, ctx):
        connection = create_db_connection()
        if not connection:
            await ctx.send("Unable to connect to the database. Please try again later.")
            return

        user_id = self.get_user_id(connection, ctx.author.id)
        if not user_id:
            await ctx.send("You need to be registered to use this command.")
            return

        inventory_id = self.get_inventory_id(connection, user_id)
        team_id = self.get_team_id(connection, user_id)

        if not inventory_id or not team_id:
            await ctx.send("You need to have an inventory and a team to enter a dungeon.")
            return

        dungeons = self.get_solo_dungeons(connection)
        if not dungeons:
            await ctx.send("No dungeons available at the moment.")
            return

        current_page = 0
        
        async def update_embed():
            difficulty = list(DungeonDifficulty)[current_page]
            embed = discord.Embed(
                title=f"{difficulty.value[4]}",
                description=self.format_dungeon_description(difficulty),
                color=discord.Color(difficulty.value[5])
            )
            embed.set_image(url=difficulty.value[2])
            
            # Add dungeon stats
            embed.add_field(name="📊 Rank", value=f"[{difficulty.value[3]}]", inline=True)
            embed.add_field(name="☠️ Danger", value=self.format_danger_level(difficulty), inline=True)
            embed.add_field(name="💰 Rewards", value=self.format_reward_level(difficulty), inline=True)
            
            # Add more detailed information
            embed.add_field(name="🗺️ Environment", value=self.generate_environment_description(difficulty), inline=False)
            embed.add_field(name="👹 Monsters", value=self.generate_monster_description(difficulty), inline=False)
            embed.add_field(name="💎 Treasures", value=self.generate_treasure_description(difficulty), inline=False)
            
            embed.set_footer(text=f"Gate {current_page + 1}/3 | ⬅️ Previous | ➡️ Next | 🚪 Enter")
            return embed

        message = await ctx.send(embed=await update_embed())
        await message.add_reaction("⬅️")
        await message.add_reaction("➡️")
        await message.add_reaction("🚪")

        def check(reaction, user):
            return user == ctx.author and str(reaction.emoji) in ["⬅️", "➡️", "🚪"]

        while True:
            try:
                reaction, user = await self.bot.wait_for('reaction_add', timeout=60.0, check=check)
            except asyncio.TimeoutError:
                await ctx.send("The gates have sealed shut. Your hesitation has cost you this opportunity, hunter.")
                return

            if str(reaction.emoji) == "⬅️":
                current_page = (current_page - 1) % 3
                await message.edit(embed=await update_embed())
            elif str(reaction.emoji) == "➡️":
                current_page = (current_page + 1) % 3
                await message.edit(embed=await update_embed())
            elif str(reaction.emoji) == "🚪":
                # Check dungeon timer when attempting to enter
                query = "SELECT dungeon_timer FROM user WHERE user_id = %s"
                result = self.execute_query(connection, query, (user_id,))
                current_time = datetime.now()
                
                if result[0]['dungeon_timer'] is not None:
                    last_dungeon_time = result[0]['dungeon_timer']
                    time_diff = current_time - last_dungeon_time
                    cooldown_minutes = 10
                    
                    if time_diff.total_seconds() < cooldown_minutes * 60:
                        remaining_seconds = (cooldown_minutes * 60) - time_diff.total_seconds()
                        remaining_minutes = int(remaining_seconds // 60)
                        remaining_secs = int(remaining_seconds % 60)
                        
                        if remaining_minutes > 0:
                            await ctx.send(f"⏳ You must wait {remaining_minutes}m {remaining_secs}s before entering another dungeon!")
                        else:
                            await ctx.send(f"⏳ You must wait {remaining_secs}s before entering another dungeon!")
                        return

                selected_dungeon = dungeons[current_page]
                dungeon_id = selected_dungeon['dungeon_id']
                
                # Check for dungeon key
                key_query = """
                SELECT idk.quantiter 
                FROM inventory_dungeonkey idk
                JOIN dungeon d ON idk.dungeonkey_id = d.dungeonkey_id
                WHERE idk.inventory_id = %s AND d.dungeon_id = %s
                """
                key_result = self.execute_query(connection, key_query, (inventory_id, dungeon_id))
                if not key_result:
                    await ctx.send("You don't have the required key for this dungeon.")
                    return

                key_quantity = key_result[0]['quantiter']

                # Check for fighter card
                fighter_query = """
                SELECT COUNT(*) as fighter_count
                FROM team_cards tc
                JOIN user_cards uc ON tc.card_id = uc.card_id
                JOIN card c ON uc.card_id = c.card_id
                WHERE tc.team_id = %s AND c.type_card = 'Fighter'
                """
                fighter_result = self.execute_query(connection, fighter_query, (team_id,))

                if fighter_result[0]['fighter_count'] == 0:
                    await ctx.send("Your team needs at least one Fighter card to enter the dungeon.")
                    return

                # Update dungeon timer since all checks passed
                update_timer_query = "UPDATE user SET dungeon_timer = %s WHERE user_id = %s"
                self.execute_query(connection, update_timer_query, (current_time, user_id))

                # Update or delete the dungeon key
                if key_quantity == 1:
                    delete_key_query = "DELETE FROM inventory_dungeonkey WHERE inventory_id = %s AND dungeonkey_id = %s"
                    self.execute_query(connection, delete_key_query, (inventory_id, selected_dungeon['dungeonkey_id']))
                else:
                    update_key_query = "UPDATE inventory_dungeonkey SET quantiter = quantiter - 1 WHERE inventory_id = %s AND dungeonkey_id = %s"
                    self.execute_query(connection, update_key_query, (inventory_id, selected_dungeon['dungeonkey_id']))

                # Get dungeon cards
                dungeon_cards_query = """
                SELECT dc.*, c.card_name
                FROM dungeon_cards dc
                JOIN card c ON dc.card_id = c.card_id
                WHERE dc.dungeon_id = %s
                ORDER BY dc.round
                """
                dungeon_cards = self.execute_query(connection, dungeon_cards_query, (dungeon_id,))

                # Get user's fighter cards
                user_cards_query = """
                SELECT c.card_id, c.card_name, c.image, c.card_rank, c.xp_cap, c.max_level,
                    uc.power_stat, uc.health_stat, uc.speed_stat, uc.attack_stat, uc.defence_stat, 
                    uc.current_level, uc.current_xp, uc.id_enhancer, uc.fight_count, 
                    uc.charm_attack, uc.charm_defence, uc.charm_speed, uc.charm_health,
                    uc.attack_count, uc.defence_count, uc.speed_count, uc.health_count, tc.position
                FROM team_cards tc
                JOIN card c ON tc.card_id = c.card_id
                JOIN user_cards uc ON tc.card_id = uc.card_id AND uc.user_id = %s
                WHERE tc.team_id = %s AND c.type_card = 'Fighter'
                ORDER BY tc.position
                """
                user_fighter_cards = self.execute_query(connection, user_cards_query, (user_id, team_id))
                # Apply charm effects
                user_fighter_cards = self.apply_charm_effects(connection, user_id, user_fighter_cards)

                selected_difficulty = list(DungeonDifficulty)[current_page]
                await self.start_dungeon(ctx, selected_difficulty, user_fighter_cards, dungeon_cards)
                return

            await message.remove_reaction(reaction, user)
    def format_dungeon_description(self, difficulty: DungeonDifficulty) -> str:
        descriptions = {
            DungeonDifficulty.EASY: "A shadowy forest teeming with mischievous goblins. The air is thick with an eerie calm, but danger lurks behind every tree.",
            DungeonDifficulty.MEDIUM: "A vast network of sticky webs, home to giant spiders. The chittering echoes in the darkness, testing the mettle of all who enter.",
            DungeonDifficulty.HARD: "The Hunter Guild's Gate, a massive portal exuding overwhelming mana. Behind it lies a battlefield of high-rank monsters and treasures unknown. Only the strongest hunters dare to challenge it."
        }
        return descriptions[difficulty]

    def format_danger_level(self, difficulty: DungeonDifficulty) -> str:
        danger = ':star:' * difficulty.value[9] + '⚪' * (3 - difficulty.value[9])
        return danger

    def format_reward_level(self, difficulty: DungeonDifficulty) -> str:
        rewards = '💎' * difficulty.value[9] + '⚪' * (3 - difficulty.value[9])
        return rewards

    def generate_environment_description(self, difficulty: DungeonDifficulty) -> str:
        environments = {
            DungeonDifficulty.EASY: "A misty forest with ancient trees and hidden paths. Goblin traps and crude fortifications dot the landscape.",
            DungeonDifficulty.MEDIUM: "A dark, cavernous space filled with giant webs. Abandoned equipment of unfortunate adventurers litters the ground.",
            DungeonDifficulty.HARD: "A colossal gate surrounded by glowing runes and pulsating with energy. The area hums with mana, and the faint roar of monsters echoes from within."
        }
        return environments[difficulty]
    def manage_combat_log(self, round_log, current_round):
        """
        Manages the combat log by splitting it into multiple fields if needed.
        Each field has a maximum of 1024 characters.
        """
        fields = []
        current_field = []
        current_length = 0
        
        for line in round_log:
            line_length = len(line) + 1  # +1 for newline
            
            if current_length + line_length > 1000:  # Using 1000 to be safe
                # Create a new field with accumulated lines
                field_value = "\n".join(current_field)
                fields.append((f"⚔️ Round {current_round}", field_value, False))
                
                # Start a new field
                current_field = [line]
                current_length = line_length
            else:
                current_field.append(line)
                current_length += line_length
        
        # Add the last field if it has content
        if current_field:
            field_value = "\n".join(current_field)
            fields.append((f"⚔️ Round {current_round}", field_value, False))
        
        return fields
    def get_image_path(self, image_name):
        """Get the full path for an image from the images directory"""
        if not image_name:
            return None
        return os.path.join(self.image_path, image_name)
    async def update_embed_fields(self, embed, dungeon_message, round_log, current_round):
        """
        Updates the embed with managed combat log fields
        """
        try:
            # Clear existing fields
            embed.clear_fields()
            
            # Get managed fields
            fields = self.manage_combat_log(round_log, current_round)
            
            # Add all fields to embed
            for name, value, inline in fields:
                embed.add_field(name=name, value=value, inline=inline)
            
            # Update the message
            await dungeon_message.edit(embed=embed)
        except Exception as e:
            logging.error(f"Error updating embed fields: {str(e)}")
            raise
    def generate_monster_description(self, difficulty: DungeonDifficulty) -> str:
        monsters = {
            DungeonDifficulty.EASY: "Goblins, Hobgoblin",
            DungeonDifficulty.MEDIUM: "Insects, Lizard Squad, Giant Spiders",
            DungeonDifficulty.HARD: "Jackals, Strike Team B, High-Orcss"
        }
        return monsters[difficulty]

    def generate_treasure_description(self, difficulty: DungeonDifficulty) -> str:
        treasures = {
            DungeonDifficulty.EASY: "Abundant Shadow Chests scattered throughout the dungeon.",
            DungeonDifficulty.MEDIUM: "A mix of Hunter Chests and Shadow Chests, offering greater rewards.",
            DungeonDifficulty.HARD: "An array of Monarch Chests, Hunter Chests, and Shadow Chests, brimming with rare treasures."
        }
        return treasures[difficulty]


    async def start_dungeon(self, ctx, difficulty: DungeonDifficulty,user_fighter_cards,dungeon_cards):
        dungeon_name = self.generate_dungeon_name(difficulty)
        embed = discord.Embed(
            title=f"🏰 {dungeon_name}",
            description=self.generate_dungeon_description(difficulty),
            color=discord.Color(difficulty.value[5])
        )

        embed.set_image(url=difficulty.value[2])

        embed.add_field(name="📊 Rank", value=f"\n[{difficulty.value[3]}]\n", inline=True)

        danger_level = '█' * (list(DungeonDifficulty).index(difficulty) + 1) + '░' * (3 - (list(DungeonDifficulty).index(difficulty) + 1))
        embed.add_field(name="☠️ Danger Level", value=f"\n{danger_level}\n", inline=True)

        rewards = '🌟' * (list(DungeonDifficulty).index(difficulty) + 1)
        embed.add_field(name="💰 Rewards", value=f"\n{rewards}\n", inline=True)

        embed.add_field(name="📜 Your Mission", value=self.generate_mission(), inline=False)
        embed.add_field(name="🕒 Status", value="```diff\n+ Dungeon exploration in progress...\n```", inline=False)

        embed.set_footer(text="'I alone level up.' - Sung Jin-Woo")

        dungeon_message = await ctx.send(embed=embed)
        await asyncio.sleep(3)
        await self.dungeon_exploration(ctx, dungeon_message, difficulty,user_fighter_cards,dungeon_cards)
    def truncate_log(self, log_entries, max_length=800):
        """Truncate log entries while keeping the most recent and important information."""
        total_length = sum(len(entry) for entry in log_entries)
        if total_length <= max_length:
            return log_entries

        # Always keep the first entry (round description) and last 4 entries (current fight status)
        essential_entries = [log_entries[0]] + log_entries[-4:]
        return essential_entries
    def calculate_effective_damage(self, attack_damage: int, defense_stat: int) -> int:
        k = 100
        effective_damage = attack_damage * (1 - (defense_stat / (defense_stat + k)))
        return round(effective_damage)
    def create_health_bar(self, entity, is_monster=False, damage_taken=None):
        health_percentage = max(0, min(100, (entity['current_health'] / entity['health_stat']) * 100))
        filled_blocks = min(10, max(0, int(health_percentage / 10)))
        empty_blocks = 10 - filled_blocks

        if is_monster:
            filled_square = self.red_square if health_percentage > 0 else self.gray_square
        else:
            filled_square = self.green_square

        health_bar = f"{filled_square * filled_blocks}{self.gray_square * empty_blocks}"
        name = entity.get('card_name', 'Unknown')
        
        # Add damage taken display if provided
        damage_text = ""
        return f"{name}: {health_bar} **({health_percentage:.1f}%){damage_text}** ({max(0, entity['current_health'])}/{entity['health_stat']} HP)"


    async def dungeon_exploration(self, ctx, dungeon_message, difficulty, user_fighter_cards, dungeon_cards):
        try:
            logging.info(f"Starting dungeon exploration for user {ctx.author.id} with difficulty {difficulty.value[0]}")
            
            # Add user to dungeons_in_progress at the start
            self.dungeons_in_progress.add(ctx.author.id)
            
            dungeon_rounds = {}
            for card in dungeon_cards:
                round_num = card['round']
                if round_num not in dungeon_rounds:
                    dungeon_rounds[round_num] = []
                dungeon_rounds[round_num].append(card)

            total_rounds = len(dungeon_rounds)
            embed = dungeon_message.embeds[0]
            embed.clear_fields()

            original_user_team = user_fighter_cards.copy()
            await dungeon_message.clear_reactions()
            await dungeon_message.add_reaction("🏃")
            embed.set_footer(text="React with 🏃 to run away")

            # Set up the reaction check outside the main loop
            def check(reaction, user):
                return (user == ctx.author and 
                    str(reaction.emoji) == "🏃" and 
                    reaction.message.id == dungeon_message.id)

            for current_round in range(1, total_rounds + 1):
                # Reset user team health at the start of each round
                user_team = []
                for card in original_user_team:
                    new_card = card.copy()
                    new_card['current_health'] = new_card['health_stat']
                    user_team.append(new_card)
                
                round_description = f"Round {current_round} of the {difficulty.value[4]}"
                round_log = [round_description]
                embed.add_field(name=f"⚔️ Round {current_round}", value=round_description, inline=False)
                await dungeon_message.edit(embed=embed)
                await asyncio.sleep(1)

                round_monsters = dungeon_rounds[current_round].copy()
                fight_number = 1

                while round_monsters and user_team:
                    monster = round_monsters[0]
                    user_card = user_team[0]

                    if 'current_health' not in monster:
                        monster['current_health'] = monster['health_stat']

                    fight_header = f"\n**Fight {fight_number}: {user_card['card_name']} vs {monster['card_name']}**"
                    player_health = self.create_health_bar(user_card, is_monster=False)
                    monster_health = self.create_health_bar(monster, is_monster=True)
                    damage_line = "Damage dealt: -"

                    fight_status = [fight_header, player_health, monster_health, damage_line]
                    round_log = self.truncate_log(round_log + fight_status)

                    embed.set_field_at(-1, name=f"⚔️ Round {current_round}", value="\n".join(round_log), inline=False)
                    
                    # Improved image loading
                    files = []
                    card_image_path = self.get_image_path(monster['image'])
                    if card_image_path and os.path.exists(card_image_path):
                        try:
                            # Use the base filename to create the attachment
                            card_filename = f"card_{os.path.basename(card_image_path)}"
                            card_file = discord.File(card_image_path, filename=card_filename)
                            files.append(card_file)
                            
                            # Update message edit to include files
                            embed.set_image(url=f"attachment://{card_filename}")
                            await dungeon_message.edit(embed=embed, attachments=files)
                        except Exception as e:
                            logging.error(f"Error loading card image: {e}")
                            # Fallback if image loading fails
                            await dungeon_message.edit(embed=embed)
                    else:
                        # If no image path exists, just edit the embed without files
                        await dungeon_message.edit(embed=embed)
                    
                    await asyncio.sleep(1)

                    while monster['current_health'] > 0 and user_card['current_health'] > 0:
                        # Create tasks for both the reaction check and the battle timer
                        reaction_task = asyncio.create_task(
                            self.bot.wait_for("reaction_add", check=check)
                        )
                        battle_timer = asyncio.create_task(asyncio.sleep(3))  # 3 second battle timer

                        # Wait for either a reaction or the timer to finish
                        done, pending = await asyncio.wait(
                            [reaction_task, battle_timer],
                            return_when=asyncio.FIRST_COMPLETED
                        )

                        # Cancel pending tasks
                        for task in pending:
                            task.cancel()

                        # Check if we got a reaction
                        if reaction_task in done:
                            try:
                                reaction, user = await reaction_task
                                await self.handle_run_away(ctx, dungeon_message)
                                if ctx.author.id in self.dungeons_in_progress:
                                    self.dungeons_in_progress.remove(ctx.author.id)
                                return
                            except asyncio.CancelledError:
                                pass
                        
                        # Continue with combat
                        # User attack
                        user_damage = random.randint(int(user_card['attack_stat'] * 0.45), int(user_card['attack_stat'] * 0.55))
                        effective_user_damage = self.calculate_effective_damage(user_damage, monster['defence_stat'])
                        monster['current_health'] = max(0, monster['current_health'] - effective_user_damage)

                        fight_status = [
                            fight_header,
                            self.create_health_bar(user_card, is_monster=False),
                            self.create_health_bar(monster, is_monster=True, damage_taken=effective_user_damage),
                            f"Damage dealt: {user_card['card_name']} → {effective_user_damage}"
                        ]
                        round_log = self.truncate_log(round_log[:-4] + fight_status)
                        embed.set_field_at(-1, name=f"⚔️ Round {current_round}", value="\n".join(round_log), inline=False)
                        await dungeon_message.edit(embed=embed)
                        await asyncio.sleep(1)

                        if monster['current_health'] > 0:
                            # Monster attack
                            monster_damage = random.randint(int(monster['attack_stat'] * 0.45), int(monster['attack_stat'] * 0.55))
                            effective_monster_damage = self.calculate_effective_damage(monster_damage, user_card['defence_stat'])
                            user_card['current_health'] = max(0, user_card['current_health'] - effective_monster_damage)

                            fight_status = [
                                fight_header,
                                self.create_health_bar(user_card, is_monster=False, damage_taken=effective_monster_damage),
                                self.create_health_bar(monster, is_monster=True),
                                f"Damage dealt: {monster['card_name']} → {effective_monster_damage}"
                            ]
                            round_log = self.truncate_log(round_log[:-4] + fight_status)
                            embed.set_field_at(-1, name=f"⚔️ Round {current_round}", value="\n".join(round_log), inline=False)
                            await dungeon_message.edit(embed=embed)
                            await asyncio.sleep(1)

                    # Defeat or victory logic for individual fights
                    if monster['current_health'] <= 0:
                        round_log.append(f"**{monster['card_name']} has been defeated!**")
                        round_monsters.pop(0)
                    else:
                        round_log.append(f"**{user_card['card_name']} has been defeated!**")
                        user_team.pop(0)

                    round_log = self.truncate_log(round_log)
                    embed.set_field_at(-1, name=f"⚔️ Round {current_round}", value="\n".join(round_log), inline=False)
                    await dungeon_message.edit(embed=embed)
                    await asyncio.sleep(2)

                    fight_number += 1

                    # Round completion or game end logic
                    if not round_monsters and current_round < total_rounds:
                        round_log.append("\n**Round Complete!** Moving to the next round...")
                        round_log = self.truncate_log(round_log)
                        
                        # Clear previous image completely
                        embed.set_image(url=difficulty.value[2])  # Round transition image
                        
                        # Update the field and edit the message
                        embed.set_field_at(-1, name=f"⚔️ Round {current_round}", value="\n".join(round_log), inline=False)
                        await dungeon_message.edit(embed=embed, attachments=[])  # Explicitly clear files
                        
                        await asyncio.sleep(2)
                    elif not round_monsters and current_round == total_rounds:
                        await self.handle_dungeon_victory(ctx, dungeon_message, embed, difficulty)
                        return
                    elif not user_team:
                        # When defeat occurs, clear image and ensure clean embed
                        embed.set_image(url=difficulty.value[2])  # Clear any existing image
                        
                        await self.handle_dungeon_defeat(ctx, dungeon_message, embed, difficulty)
                        
                        # Optional: If handle_dungeon_defeat method doesn't fully reset the message
                        await dungeon_message.edit(embed=embed, attachments=[])
                        return

            if ctx.author.id in self.dungeons_in_progress:
                self.dungeons_in_progress.remove(ctx.author.id)

        except Exception as e:
            logging.error(f"Critical error in dungeon exploration: {str(e)}")
            logging.error(traceback.format_exc())  # Added full traceback for more detailed error logging
            if ctx.author.id in self.dungeons_in_progress:
                self.dungeons_in_progress.remove(ctx.author.id)
            await ctx.send(f"An error occurred during dungeon exploration: {str(e)}")

    async def handle_dungeon_victory(self, ctx, dungeon_message, embed, difficulty):
        embed.add_field(name="🎉 Dungeon Cleared!", 
                       value="Congratulations! You've defeated the boss and cleared the dungeon!", 
                       inline=False)
        embed.set_image(url=difficulty.value[2])
        await dungeon_message.edit(embed=embed)
        
        try:
            connection = create_db_connection()
            if connection:
                user_id = self.get_user_id(connection, ctx.author.id)
                inventory_id = self.get_inventory_id(connection, user_id)
                quest_message = await self.check_and_update_quest(connection, user_id)
                if quest_message:
                    await ctx.send(quest_message)
                await self.treasure_room(ctx, connection, inventory_id, difficulty.value[9], f"{difficulty.value[0].lower()} gate")
            self.dungeons_in_progress.remove(ctx.author.id)
        except Exception as e:
            logging.error(f"Error handling dungeon rewards: {str(e)}")
            raise

    async def handle_dungeon_defeat(self, ctx, dungeon_message, embed, difficulty):
        # Clear any existing fields
        embed.clear_fields()
        
        # Add defeat field
        embed.add_field(name="❌ Dungeon Failed",
                        value=f"{ctx.author.mention} Your team has been defeated. Better luck next time!",
                        inline=False)
        
        # Clear the image 
        embed.set_image(url=difficulty.value[2])
        
        # Edit the message with cleared image and no files
        await dungeon_message.edit(embed=embed, attachments=[])
                    
        try:
            connection = create_db_connection()
            if connection:
                user_id = self.get_user_id(connection, ctx.author.id)
                money_loss = self.calculate_money_loss(difficulty)
                self.update_user_currency(connection, user_id, -money_loss, 1)
                embed.add_field(name=f"You lost {money_loss} currency for failing the dungeon.", value="", inline=False)
            
            self.dungeons_in_progress.remove(ctx.author.id)
        
        except Exception as e:
            logging.error(f"Error handling dungeon failure: {str(e)}")
            raise

    def calculate_money_loss(self, difficulty):
        if difficulty == DungeonDifficulty.EASY:
            return 5000
        elif difficulty == DungeonDifficulty.MEDIUM:
            return 10000
        elif difficulty == DungeonDifficulty.HARD:
            return 20000
        else:
            return 5000  # Default to easy dungeon loss if difficulty is unknown

    async def handle_run_away(self, ctx, dungeon_message):
        connection = create_db_connection()
        if not connection:
            await ctx.send("Unable to connect to the database. Please try again later.")
            return

        user_id = self.get_user_id(connection, ctx.author.id)
        inventory_id = self.get_inventory_id(connection, user_id)

        # Check if the user has a return crystal (product_id = 2)
        check_crystal_query = """
        SELECT quantiter FROM inventory_product
        WHERE inventory_id = %s AND product_id = 13
        """
        crystal_result = self.execute_query(connection, check_crystal_query, (inventory_id,))

        if not crystal_result:
            await ctx.send("You don't have a return crystal to escape the dungeon.")
            return

        crystal_quantity = crystal_result[0]['quantiter']

        if crystal_quantity == 1:
            delete_crystal_query = """
            DELETE FROM inventory_product
            WHERE inventory_id = %s AND product_id = 13
            """
            self.execute_query(connection, delete_crystal_query, (inventory_id,))
        else:
            update_crystal_query = """
            UPDATE inventory_product
            SET quantiter = quantiter - 1
            WHERE inventory_id = %s AND product_id = 13
            """
            self.execute_query(connection, update_crystal_query, (inventory_id,))

        embed = dungeon_message.embeds[0]
        embed.add_field(name="🏃‍♂️ Dungeon Cancelled", value="You used a return crystal to escape the dungeon!", inline=False)
        await dungeon_message.edit(embed=embed)
        await ctx.send("You have successfully fled the dungeon using a return crystal.")
    async def check_and_update_quest(self, connection, user_id):
        # Check if the user has the "Conquer 1 dungeon" quest
        check_quest_query = """
        SELECT uq.userquests_id, uq.current_count, uq.completion_status, q.required_count, q.quest_id
        FROM user_quests uq
        JOIN quest q ON uq.quest_id = q.quest_id
        WHERE uq.user_id = %s AND q.quest_name = 'Conquer 1 dungeon'
        """
        quest_result = self.execute_query(connection, check_quest_query, (user_id,))

        if quest_result:
            quest = quest_result[0]
            if quest['completion_status'] == 0:
                new_count = quest['current_count'] + 1
                update_query = """
                UPDATE user_quests
                SET current_count = %s, completion_status = %s
                WHERE userquests_id = %s
                """
                completion_status = 1 if new_count >= quest['required_count'] else 0
                self.execute_query(connection, update_query, (new_count, completion_status, quest['userquests_id']))

                if completion_status == 1:
                    # Quest completed, add rewards here
                    reward_query = """
                    SELECT product_id FROM quest
                    WHERE quest_id = %s
                    """
                    reward_result = self.execute_query(connection, reward_query, (quest['quest_id'],))
                    if reward_result:
                        product_id = reward_result[0]['product_id']
                        inventory_id = self.get_inventory_id(connection, user_id)
                        self.update_inventory(connection, inventory_id, product_id, 1, 'special_items')
                        return f"Quest 'Conquer 1 dungeon' completed! You have received a reward."
                else:
                    return f"Quest 'Conquer 1 dungeon' progress: {new_count}/{quest['required_count']}"

        return None
    def generate_round_description(self, round_number):
        descriptions = [
            "The air grows thicker as you delve deeper into the dungeon.",
            "Shadows dance on the walls, hinting at the dangers that lie ahead.",
            "The echoes of your footsteps are drowned out by distant, ominous sounds.",
            "You feel a chill run down your spine as you prepare for the next challenge.",
            "The very stones beneath your feet seem to pulse with an ancient, dark energy."
        ]
        return random.choice(descriptions)

    def generate_dungeon_name(self, difficulty: DungeonDifficulty) -> str:
        return f"{difficulty.value[0]} Dungeon of Shadows"

    def generate_dungeon_description(self, difficulty: DungeonDifficulty) -> str:
        return "The shadows dance around you as you step into the unknown. Every sound makes you tense, every step feels heavier than the last."

    def generate_mission(self) -> str:
        return "Your objective is to complete the mission and take down the boss. Only by succeeding will you claim the rewards awaiting you."

    def generate_stage_description(self, stage: str) -> str:
        return f"You've arrived at the {stage}. Prepare yourself for what's to come."
    async def treasure_room(self, ctx, connection, inventory_id, dungeon_id, dungeon_name):
        # Get chest rewards based on dungeon difficulty
        rewards = {}
        
        # Get user_id from inventory_id
        user_query = """
        SELECT user_id 
        FROM inventory 
        WHERE inventory_id = %s
        """
        user_result = self.execute_query(connection, user_query, (inventory_id,))
        user_id = user_result[0]['user_id']
        
        if dungeon_name.lower() == "easy gate":
            # Shadow chest (chest_id 1)
            shadow_chest_count = random.randint(1, 3)
            rewards[1] = {
                'chest_id': 1,
                'quantity': shadow_chest_count
            }
            # Update low dungeon points
            update_points_query = """
            UPDATE user 
            SET low_dungeon_points = low_dungeon_points + 1 
            WHERE user_id = %s
            """
            self.execute_query(connection, update_points_query, (user_id,))
        
        elif dungeon_name.lower() == "medium gate":
            # Hunter chest (chest_id 2)
            chest_type_roll = random.random()
            
            if chest_type_roll < 0.9:
                # 90% chance: 1-2 hunter chests
                hunter_chest_count = random.randint(1, 2)
                rewards[2] = {
                    'chest_id': 2,
                    'quantity': hunter_chest_count
                }
            else:
                # 10% chance: 1 hunter chest and 1-3 shadow chests
                rewards[2] = {
                    'chest_id': 2,
                    'quantity': 1
                }
                shadow_chest_count = random.randint(1, 3)
                rewards[1] = {
                    'chest_id': 1,
                    'quantity': shadow_chest_count
                }
            # Update medium dungeon points
            update_points_query = """
            UPDATE user 
            SET mid_dungeon_points = mid_dungeon_points + 1 
            WHERE user_id = %s
            """
            self.execute_query(connection, update_points_query, (user_id,))
        
        elif dungeon_name.lower() == "hard gate":
            # Monarch chest (chest_id 3)
            chest_type_roll = random.random()
            
            # Always get monarch chest
            rewards[3] = {
                'chest_id': 3,
                'quantity': 1
            }
            
            # Additional chest possibilities
            if chest_type_roll < 0.7:  # High probability of only monarch chest
                pass
            elif chest_type_roll < 0.8:  # Monarch + Hunter
                hunter_chest_count = random.randint(1, 2)
                rewards[2] = {
                    'chest_id': 2,
                    'quantity': hunter_chest_count
                }
            elif chest_type_roll < 0.9:  # Monarch + Shadow
                shadow_chest_count = random.randint(1, 4)
                rewards[1] = {
                    'chest_id': 1,
                    'quantity': shadow_chest_count
                }
            else:  # Monarch + Hunter + Shadow
                hunter_chest_count = random.randint(1, 2)
                shadow_chest_count = random.randint(1, 3)
                rewards[2] = {
                    'chest_id': 2,
                    'quantity': hunter_chest_count
                }
                rewards[1] = {
                    'chest_id': 1,
                    'quantity': shadow_chest_count
                }
            # Update high dungeon points
            update_points_query = """
            UPDATE user 
            SET high_dungeon_points = high_dungeon_points + 1 
            WHERE user_id = %s
            """
            self.execute_query(connection, update_points_query, (user_id,))
        
        # Update inventory_chest
        for chest_entry in rewards.values():
            # Check if chest already exists in inventory
            check_query = "SELECT * FROM inventory_chest WHERE inventory_id = %s AND chest_id = %s"
            existing_chest = self.execute_query(connection, check_query, (inventory_id, chest_entry['chest_id']))
            
            if existing_chest:
                # Update quantity if chest exists
                update_query = "UPDATE inventory_chest SET quantiter = quantiter + %s WHERE inventory_id = %s AND chest_id = %s"
                self.execute_query(connection, update_query, (chest_entry['quantity'], inventory_id, chest_entry['chest_id']))
            else:
                # Insert new chest entry
                insert_query = "INSERT INTO inventory_chest (inventory_id, chest_id, quantity) VALUES (%s, %s, %s)"
                self.execute_query(connection, insert_query, (inventory_id, chest_entry['chest_id'], chest_entry['quantity']))
        
        # Calculate money reward
        money_query = "SELECT dungeon_moneycap FROM dungeon WHERE dungeon_id = %s"
        money_result = self.execute_query(connection, money_query, (dungeon_id,))
        money_cap = money_result[0]["dungeon_moneycap"]
        money_reward = self.calculate_money_reward(ctx, connection, money_cap)
        
        # Handle guild contribution and update user currency
        final_money_reward = await self.handle_guild_contribution(ctx, connection, user_id, money_reward)
        
        # Handle XP rewards
        team_id = self.get_team_id(connection, user_id)
        await self.handle_xp_rewards(ctx, connection, user_id, team_id, dungeon_id, final_money_reward, rewards)
        
        return rewards

    async def handle_xp_rewards(self, ctx, connection, user_id, team_id, dungeon_id, money_reward, rewards):
        # Get the user's team cards
        team_cards_query = """
        SELECT tc.*, c.card_name, c.type_card, c.card_icon, c.card_rank, c.xp_cap, c.max_level, uc.current_level, uc.current_xp,
            uc.attack_stat, uc.health_stat, uc.defence_stat, uc.speed_stat, uc.id_enhancer, uc.fight_count,
            uc.charm_attack, uc.charm_defence, uc.charm_speed, uc.charm_health,
            uc.attack_count, uc.defence_count, uc.speed_count, uc.health_count
        FROM team_cards tc
        JOIN user_cards uc ON tc.card_id = uc.card_id AND uc.user_id = %s
        JOIN card c ON tc.card_id = c.card_id
        WHERE tc.team_id = %s AND c.type_card='Fighter'
        """
        team_cards = self.execute_query(connection, team_cards_query, (user_id, team_id))

        xp_rewards = []
        total_xp_gained = 0
        total_cards_leveled = 0
        for card in team_cards:
            xp_reward = random.randint(20, 100)
            
            # Adjust XP based on dungeon and card rank
            if dungeon_id == 1:  # Easy dungeon
                if card['card_rank'] in [5, 6]:
                    xp_reward = xp_reward
                else:
                    xp_reward = xp_reward // 2
            elif dungeon_id == 2:  # Medium dungeon
                if card['card_rank'] in [3, 4]:
                    xp_reward = xp_reward
                elif card['card_rank'] in [1, 2]:
                    xp_reward = xp_reward // 2
                else:  # Rank 5 or 6
                    xp_reward = xp_reward * 2
            elif dungeon_id == 3:  # Hard dungeon
                if card['card_rank'] in [1, 2]:
                    xp_reward = xp_reward
                else:  # Rank 3, 4, 5, or 6
                    xp_reward = xp_reward * 2

            if card['id_enhancer'] is not None and card['id_enhancer'] != 0:
                xp_reward = int(xp_reward * 1.2)  # 20% XP boost
                new_fight_count = card['fight_count'] - 1
                if new_fight_count <= 0:
                    update_enhancer_query = """
                    UPDATE user_cards
                    SET fight_count = 0, id_enhancer = NULL
                    WHERE user_id = %s AND card_id = %s
                    """
                else:
                    update_enhancer_query = """
                    UPDATE user_cards
                    SET fight_count = %s
                    WHERE user_id = %s AND card_id = %s
                    """
                self.execute_query(connection, update_enhancer_query, (new_fight_count, user_id, card['card_id']))
            total_xp_gained += xp_reward
            new_xp = card['current_xp'] + xp_reward
            new_level = card['current_level']
            
            # Handle level up
            level_up = False
            if new_xp >= card['xp_cap']:
                if new_level < card['max_level']:
                    new_level += 1
                    new_xp = new_xp - card['xp_cap']
                    level_up = True
                    total_cards_leveled += 1
                else:
                    new_xp = 0  # XP stays at 0 if at level cap

            # Generate stat increases if leveled up
            attack_increase = random.randint(10, 15) if level_up else 0
            health_increase = random.randint(10, 15) if level_up else 0
            defence_increase = random.randint(10, 15) if level_up else 0
            speed_increase = random.randint(10, 15) if level_up else 0

            new_attack = card['attack_stat'] + attack_increase
            new_health = card['health_stat'] + health_increase
            new_defence = card['defence_stat'] + defence_increase
            new_speed = card['speed_stat'] + speed_increase

            # Update user_cards table
            update_query = """
            UPDATE user_cards
            SET current_level = %s, current_xp = %s,
                attack_stat = %s, health_stat = %s, defence_stat = %s, speed_stat = %s,current_statpoints = current_statpoints + 5,power_stat = attack_stat+health_stat+defence_stat+speed_stat
            WHERE user_id = %s AND card_id = %s
            """
            self.execute_query(connection, update_query, (new_level, new_xp, new_attack, new_health, 
                                                        new_defence, new_speed, user_id, card['card_id']))

            xp_rewards.append({
                'card_name': card['card_name'],
                'xp_gained': xp_reward,
                'new_level': new_level,
                'new_xp': new_xp,
                'level_up': level_up,
                'old_level': card['current_level'],
                'xp_cap': card['xp_cap'],
                'attack_increase': attack_increase,
                'health_increase': health_increase,
                'defence_increase': defence_increase,
                'speed_increase': speed_increase,
                'card_icon':card['card_icon'],
                'new_attack': new_attack,
                'new_defence': new_defence,
                'new_health': new_health,
                'new_speed': new_speed,
            })
        
        await self.send_rewards_embed(ctx, xp_rewards, total_xp_gained, total_cards_leveled, money_reward, rewards)

    def calculate_money_reward(self, ctx, connection, money_cap):
        user_id = self.get_user_id(connection, ctx.author.id)
        # Check if user has card_id 108
        check_card_query = """
        SELECT card_id FROM user_cards 
        WHERE user_id = %s AND card_id = 106
        """
        card_result = self.execute_query(connection, check_card_query, (user_id,))
        
        # Calculate base money prize
        money_prize = random.randint(100, money_cap)
        
        # Add bonus if user has card 108
        if card_result:
            print("lol")
            money_prize += 200
        else:
            print("no")
        
        return money_prize
    async def send_rewards_embed(self, ctx, xp_rewards, total_xp_gained, total_cards_leveled, money_reward, rewards):
        connection = create_db_connection()
        if not connection:
            await ctx.send("Unable to connect to the database. Please try again later.")
            return
        reward_message = "You've obtained the following chests: "
        for chest_info in rewards.values():
            chest_name_query = "SELECT chest_icon FROM chest WHERE chest_id = %s"
            chest_name_result = self.execute_query(connection, chest_name_query, (chest_info['chest_id'],))
            chest_name = chest_name_result[0]["chest_icon"]
            reward_message += f"{chest_info['quantity']}x {chest_name}"
        embed = discord.Embed(
            title="🏆 Battle Rewards Summary 🏆",
            color=discord.Color.gold(),
            description=f"{reward_message}\nWon Currency Received: **{money_reward}**\nTotal XP Gained: **{total_xp_gained}**\nCards Leveled Up: **{total_cards_leveled}**"
        )

        # Add a field for each card's rewards
        for reward in xp_rewards:
            # Create the XP progress bar
            xp_percentage = (reward['new_xp'] / reward['xp_cap']) * 100
            progress_bar = self.create_xp_progress_bar(xp_percentage)
            
            # Format the card's rewards information
            card_info = (

                f"{progress_bar} **{reward['new_xp']}/{reward['xp_cap']}**\n"
            )

            if reward['level_up']:
                level_up_info = (
                    f"✨ **LEVEL UP!** {reward['old_level']} → {reward['new_level']}\n"
                    f"📈 Stats Increased:\n"
                    f"```diff\n"
                    f"+ ATK: +{reward['attack_increase']}\n"
                    f"+ HP:  +{reward['health_increase']}\n"
                    f"+ DEF: +{reward['defence_increase']}\n"
                    f"+ SPD: +{reward['speed_increase']}\n"
                    f"```"
                )
                card_info += level_up_info

            embed.add_field(
                name=f"{reward['card_icon']} {reward['card_name']}: **+{reward['xp_gained']} XP**",
                value=card_info,
                inline=False
            )

        embed.set_footer(text="Keep fighting to grow stronger! | Solo Leveling PVP")
        await ctx.send(embed=embed)

    def create_xp_progress_bar(self, percentage, length=10):
        filled = int((percentage / 100.0) * length)
        empty = length - filled
        
        # Use custom emotes for the progress bar
        bar = (self.green_square * filled) + (self.gray_square * empty)
        return f"{bar} **{percentage:.1f}%**"
    async def handle_guild_contribution(self, ctx, connection, user_id, money_reward):
        # Check if user is in a guild
        guild_query = "SELECT guild_id FROM user WHERE user_id = %s"
        guild_result = self.execute_query(connection, guild_query, (user_id,))

        if guild_result and guild_result[0]['guild_id']:
            guild_id = guild_result[0]['guild_id']
            guild_contribution = int(money_reward * 0.05)  # 5% guild contribution
            final_reward = money_reward - guild_contribution

            # Update guild fund
            guild_update_query = "UPDATE guild SET fund_cash = fund_cash + %s WHERE guild_id = %s"
            self.execute_query(connection, guild_update_query, (guild_contribution, guild_id))

            await ctx.send(f"Your guild received a contribution of {guild_contribution} currency.")
        else:
            final_reward = money_reward

        # Update user currency
        self.update_user_currency(connection, user_id, final_reward,0)

        return final_reward

    def update_user_currency(self, connection, user_id, money_reward, losswin):
        if losswin == 0:
            update_query = "UPDATE user SET won_currency = won_currency + %s WHERE user_id = %s"
            self.execute_query(connection, update_query, (money_reward, user_id))
        else:
            # First get current won_currency value
            check_query = "SELECT won_currency FROM user WHERE user_id = %s"
            current_balance = self.execute_query(connection, check_query, (user_id,))[0]['won_currency']
            
            # If current balance is 0, no update needed
            if current_balance == 0:
                return
            
            # Calculate potential new balance
            # Note: money_reward is already negative in the elif case
            new_balance = current_balance + money_reward
            
            if new_balance < 0:
                # If would go negative, set to 0
                update_query = "UPDATE user SET won_currency = 0 WHERE user_id = %s"
                self.execute_query(connection, update_query, (user_id,))
            else:
                # Safe to subtract
                update_query = "UPDATE user SET won_currency = won_currency + %s WHERE user_id = %s"
                self.execute_query(connection, update_query, (money_reward, user_id))
    def get_dungeon_items(self, connection, dungeon_id):
        dungeon_items = {
            'orbs': [],
            'fragments': [],
            'weapons': [],
            'xp_boost': [],
            'stat_boosts': [],
            'special_items': []
        }

        # Fetch orbs
        orb_query = """
        SELECT o.orb_id as id, o.orb_name as name
        FROM dungeon_orb do
        JOIN leveling_orbs o ON do.orb_id = o.orb_id
        WHERE do.dungeon_id = %s
        """
        dungeon_items['orbs'] = self.execute_query(connection, orb_query, (dungeon_id,))

        # Fetch fragments
        fragment_query = """
        SELECT f.id_fragment as id, f.name_fragment as name
        FROM dungeon_fragments df
        JOIN fragments f ON df.frag_id = f.id_fragment
        WHERE df.dungeon_id = %s
        """
        dungeon_items['fragments'] = self.execute_query(connection, fragment_query, (dungeon_id,))

        # Fetch weapons
        weapon_query = """
        SELECT w.weapon_id as id, w.weapon_name as name
        FROM dungeon_weapon dw
        JOIN weapon w ON dw.weapon_id = w.weapon_id
        WHERE dw.dungeon_id = %s
        """
        dungeon_items['weapons'] = self.execute_query(connection, weapon_query, (dungeon_id,))

        # Fetch products (xp_boost, stat_boosts, special_items)
        product_query = """
        SELECT p.product_id as id, p.product_name as name, 
               p.xp_boost, p.attack_boost, p.health_boost, p.speed_boost
        FROM dungeon_product dp
        JOIN product p ON dp.product_id = p.product_id
        WHERE dp.dungeon_id = %s
        """
        products = self.execute_query(connection, product_query, (dungeon_id,))

        for product in products:
            if product['xp_boost'] != 0 and product['attack_boost'] == 0 and product['health_boost'] == 0 and product['speed_boost'] == 0:
                dungeon_items['xp_boost'].append(product)
            elif product['xp_boost'] == 0 and (product['attack_boost'] != 0 or product['health_boost'] != 0 or product['speed_boost'] != 0):
                dungeon_items['stat_boosts'].append(product)
            else:
                dungeon_items['special_items'].append(product)

        return dungeon_items

    def update_inventory(self, connection, inventory_id, item_id, quantity, item_type):
        if item_type in ['xp_boost', 'stat_boosts', 'special_items']:
            table = 'inventory_product'
            id_column = 'product_id'
        elif item_type == 'orbs':
            table = 'inventory_orbs'
            id_column = 'orb_id'
        elif item_type == 'weapons':
            table = 'inventory_weapon'
            id_column = 'weapon_id'
        elif item_type == 'fragments':
            table = 'inventory_frags'
            id_column = 'frag_id'
        else:
            print(f"Unknown item type: {item_type}")
            return

        # Check if item exists in inventory
        check_query = f"""
        SELECT quantiter FROM {table}
        WHERE inventory_id = %s AND {id_column} = %s
        """
        result = self.execute_query(connection, check_query, (inventory_id, item_id))

        if result:
            # Update existing item
            update_query = f"""
            UPDATE {table}
            SET quantiter = quantiter + %s
            WHERE inventory_id = %s AND {id_column} = %s
            """
            self.execute_query(connection, update_query, (quantity, inventory_id, item_id))
        else:
            # Insert new item
            insert_query = f"""
            INSERT INTO {table} (inventory_id, {id_column}, quantiter)
            VALUES (%s, %s, %s)
            """
            self.execute_query(connection, insert_query, (inventory_id, item_id, quantity))
    def apply_charm_effects(self, connection, user_id, team_cards):
        for card in team_cards:
            charm_updates = []
            stat_updates = []

            if card['charm_attack'] == 1:
                charm_updates.append("attack_count = CASE WHEN attack_count > 1 THEN attack_count - 1 ELSE 0 END")
                charm_updates.append("charm_attack = CASE WHEN attack_count > 1 THEN 1 ELSE 0 END")
                stat_updates.append("attack_stat = ROUND(attack_stat * 1.2)")
                card['attack_stat'] = round(card['attack_stat'] * 1.2)

            if card['charm_defence'] == 1:
                charm_updates.append("defence_count = CASE WHEN defence_count > 1 THEN defence_count - 1 ELSE 0 END")
                charm_updates.append("charm_defence = CASE WHEN defence_count > 1 THEN 1 ELSE 0 END")
                stat_updates.append("defence_stat = ROUND(defence_stat * 1.2)")
                card['defence_stat'] = round(card['defence_stat'] * 1.2)

            if card['charm_speed'] == 1:
                charm_updates.append("speed_count = CASE WHEN speed_count > 1 THEN speed_count - 1 ELSE 0 END")
                charm_updates.append("charm_speed = CASE WHEN speed_count > 1 THEN 1 ELSE 0 END")
                stat_updates.append("speed_stat = ROUND(speed_stat * 1.2)")
                card['speed_stat'] = round(card['speed_stat'] * 1.2)

            if card['charm_health'] == 1:
                charm_updates.append("health_count = CASE WHEN health_count > 1 THEN health_count - 1 ELSE 0 END")
                charm_updates.append("charm_health = CASE WHEN health_count > 1 THEN 1 ELSE 0 END")
                stat_updates.append("health_stat = ROUND(health_stat * 1.2)")
                card['health_stat'] = round(card['health_stat'] * 1.2)

            if charm_updates or stat_updates:
                update_query = "UPDATE user_cards SET "
                update_query += ", ".join(charm_updates + stat_updates)
                update_query += " WHERE user_id = %s AND card_id = %s"

                self.execute_query(connection, update_query, (user_id, card['card_id']))

        return team_cards
async def setup(bot):
    await bot.add_cog(DungeonCommands(bot))