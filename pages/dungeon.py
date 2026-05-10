import discord
from discord.ext import commands
import random
from datetime import datetime
from utils.database import create_db_connection
from mysql.connector import Error
import asyncio
from typing import List, Dict, Union
from enum import Enum

intents = discord.Intents.default()
intents.message_content = True
intents.reactions = True

# Constants
DUNGEON_GATE_IMAGE = "https://staticg.sportskeeda.com/editor/2024/03/078be-17113304718546-1920.jpg"
# Constants
EASY_DUNGEON_IMAGE = "https://i.imgur.com/nLFDb29.jpeg"
MEDIUM_DUNGEON_IMAGE = "https://static.wikia.nocookie.net/solo-leveling/images/9/95/Anime_Episode_5_Jinwoo_and_Jinho_vs_spider_dungeon_boss.png/revision/latest?cb=20240203211830"
HARD_DUNGEON_IMAGE = "https://i.imgur.com/9qOrkry.jpeg"

class DungeonDifficulty(Enum):
    EASY = ("Easy", "🟢", EASY_DUNGEON_IMAGE, "E-D", "The Forest Goblin Gate", 0x4CAF50, "🗡️", "🏺", "💎", 1)
    MEDIUM = ("Medium", "🟡", MEDIUM_DUNGEON_IMAGE, "C-B", "The Giant Spider's Nest Gate", 0xFFC107, "⚔️", "🏺🏺", "💎💎", 2)
    HARD = ("Hard", "🔴", HARD_DUNGEON_IMAGE, "A-S", "The Busan Gate", 0xF44336, "🗡️⚔️", "🏺🏺🏺", "💎💎💎", 3)

class DungeonCommands(commands.Cog):
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
                title=f"🏰 {difficulty.value[4]}",
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
            elif str(reaction.emoji) == "➡️":
                current_page = (current_page + 1) % 3
            elif str(reaction.emoji) == "🚪":
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

                # Update or delete the dungeon key
                if key_quantity == 1:
                    delete_key_query = "DELETE FROM inventory_dungeonkey WHERE inventory_id = %s AND dungeonkey_id = %s"
                    self.execute_query(connection, delete_key_query, (inventory_id, selected_dungeon['dungeonkey_id']))
                else:
                    update_key_query = "UPDATE inventory_dungeonkey SET quantiter = quantiter - 1 WHERE inventory_id = %s AND dungeonkey_id = %s"
                    self.execute_query(connection, update_key_query, (inventory_id, selected_dungeon['dungeonkey_id']))

                # Get dungeon cards
                dungeon_cards_query = """
                SELECT dc.*, c.card_name, c.image
                FROM dungeon_cards dc
                JOIN card c ON dc.card_id = c.card_id
                WHERE dc.dungeon_id = %s
                ORDER BY dc.round
                """
                dungeon_cards = self.execute_query(connection, dungeon_cards_query, (dungeon_id,))

                # Get user's fighter cards
                user_cards_query = """
                SELECT uc.*, c.card_name, c.image
                FROM team_cards tc
                JOIN user_cards uc ON tc.card_id = uc.card_id
                JOIN card c ON uc.card_id = c.card_id
                WHERE tc.team_id = %s AND c.type_card = 'Fighter' AND uc.user_id = %s
                """
                user_fighter_cards = self.execute_query(connection, user_cards_query, (team_id, user_id))

                selected_difficulty = list(DungeonDifficulty)[current_page]
                await self.start_dungeon(ctx, selected_difficulty, user_fighter_cards, dungeon_cards)
                return

            await message.edit(embed=await update_embed())
            await message.remove_reaction(reaction, user)

    def format_dungeon_description(self, difficulty: DungeonDifficulty) -> str:
        descriptions = {
            DungeonDifficulty.EASY: "A shadowy forest teeming with mischievous goblins. The air is thick with an eerie calm, but danger lurks behind every tree.",
            DungeonDifficulty.MEDIUM: "A vast network of sticky webs, home to giant spiders. The chittering echoes in the darkness, testing the mettle of all who enter.",
            DungeonDifficulty.HARD: "The ruins of Busan, a once-great city now overrun by high-rank monsters. Only the bravest dare to tread here, where legends are forged."
        }
        return descriptions[difficulty]

    def format_danger_level(self, difficulty: DungeonDifficulty) -> str:
        danger = '🔥' * difficulty.value[9] + '⚪' * (3 - difficulty.value[9])
        return danger

    def format_reward_level(self, difficulty: DungeonDifficulty) -> str:
        rewards = '💎' * difficulty.value[9] + '⚪' * (3 - difficulty.value[9])
        return rewards

    def generate_environment_description(self, difficulty: DungeonDifficulty) -> str:
        environments = {
            DungeonDifficulty.EASY: "A misty forest with ancient trees and hidden paths. Goblin traps and crude fortifications dot the landscape.",
            DungeonDifficulty.MEDIUM: "A dark, cavernous space filled with giant webs. Abandoned equipment of unfortunate adventurers litters the ground.",
            DungeonDifficulty.HARD: "Crumbling skyscrapers and eerie silence. The cityscape is a maze of danger and forgotten treasures."
        }
        return environments[difficulty]

    def generate_monster_description(self, difficulty: DungeonDifficulty) -> str:
        monsters = {
            DungeonDifficulty.EASY: "Goblins, Dire Wolves, Mischievous Sprites",
            DungeonDifficulty.MEDIUM: "Giant Spiders, Venomous Crawlers, Web Stalkers",
            DungeonDifficulty.HARD: "High-rank Beasts, Mutated Humans, Ancient Guardians"
        }
        return monsters[difficulty]

    def generate_treasure_description(self, difficulty: DungeonDifficulty) -> str:
        treasures = {
            DungeonDifficulty.EASY: "Minor artifacts, small caches of gold, low-rank magical items",
            DungeonDifficulty.MEDIUM: "Valuable spider silk, potent venom sacs, mid-tier enchanted gear",
            DungeonDifficulty.HARD: "Ancient technologies, powerful relics, high-grade magical equipment"
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
        embed.add_field(name="💰 Potential Rewards", value=f"\n{rewards}\n", inline=True)

        embed.add_field(name="📜 Your Mission", value=self.generate_mission(), inline=False)
        embed.add_field(name="🕒 Status", value="diff\n+ Dungeon exploration in progress...\n", inline=False)

        embed.set_footer(text="'I alone level up.' - Sung Jin-Woo")

        dungeon_message = await ctx.send(embed=embed)
        await asyncio.sleep(3)
        await self.dungeon_exploration(ctx, dungeon_message, difficulty,user_fighter_cards,dungeon_cards)

    def calculate_effective_damage(self, attack_damage: int, defense_stat: int) -> int:
        k = 100
        effective_damage = attack_damage * (1 - (defense_stat / (defense_stat + k)))
        return round(effective_damage)

    async def dungeon_exploration(self, ctx, dungeon_message, difficulty, user_fighter_cards, dungeon_cards):
        dungeon_rounds = {}
        for card in dungeon_cards:
            round_num = card['round']
            if round_num not in dungeon_rounds:
                dungeon_rounds[round_num] = []
            dungeon_rounds[round_num].append(card)

        total_rounds = len(dungeon_rounds)

        embed = dungeon_message.embeds[0]
        embed.clear_fields()  # Clear existing fields to start fresh

        original_user_team = user_fighter_cards.copy()  # Store the original team

        for current_round in range(1, total_rounds + 1):
            round_description = self.generate_round_description(current_round)
            embed.add_field(name=f"⚔️ Round {current_round}", value=round_description, inline=False)
            await dungeon_message.edit(embed=embed)
            await asyncio.sleep(2)

            round_monsters = dungeon_rounds[current_round]
            user_team = original_user_team.copy()  # Reset user team for each round
            
            # Heal all user cards to full health at the start of each round
            for card in user_team:
                card['current_health'] = card['health_stat']

            fight_number = 1
            round_log = [round_description]

            while round_monsters and user_team:
                monster = round_monsters[0]
                user_card = user_team[0]

                # Initialize monster health only if it's a new monster
                if 'current_health' not in monster:
                    monster['current_health'] = monster['health_stat']

                monster_max_health = monster['health_stat']
                monster_current_health = monster['current_health']
                user_max_health = user_card['health_stat']
                user_current_health = user_card['current_health']

                fight_log = [
                    f"\n**Fight {fight_number}: {user_card['card_name']} vs {monster['card_name']}**",
                    f"{user_card['card_name']}: {user_current_health}/{user_max_health} HP",
                    f"{monster['card_name']}: {monster_current_health}/{monster_max_health} HP",
                    "Damage dealt: -"
                ]

                round_log.extend(fight_log)
                embed.set_field_at(-1, name=f"⚔️ Round {current_round}", value="\n".join(round_log), inline=False)
                
                # Update the embed image to show the current monster's card image
                embed.set_image(url=monster['image'])
                
                await dungeon_message.edit(embed=embed)
                await asyncio.sleep(2)

                while monster_current_health > 0 and user_current_health > 0:
                    # User attacks first
                    user_damage = random.randint(10, user_card['attack_stat'])
                    effective_user_damage = self.calculate_effective_damage(user_damage, monster['defence_stat'])
                    monster_current_health -= effective_user_damage
                    monster_current_health = max(0, monster_current_health)  # Ensure health doesn't go below 0
                    
                    # Update the fight log for user's attack
                    fight_log[-3] = f"{user_card['card_name']}: {user_current_health}/{user_max_health} HP"
                    fight_log[-2] = f"{monster['card_name']}: {monster_current_health}/{monster_max_health} HP"
                    fight_log[-1] = f"Damage dealt: {user_card['card_name']} → {effective_user_damage} (Raw: {user_damage}, Def: {monster['defence_stat']})"

                    round_log[-4:] = fight_log
                    embed.set_field_at(-1, name=f"⚔️ Round {current_round}", value="\n".join(round_log), inline=False)
                    await dungeon_message.edit(embed=embed)
                    await asyncio.sleep(2)

                    # Monster attacks if still alive
                    if monster_current_health > 0:
                        monster_damage = random.randint(10, monster['attack_stat'])
                        effective_monster_damage = self.calculate_effective_damage(monster_damage, user_card['defence_stat'])
                        user_current_health -= effective_monster_damage
                        user_current_health = max(0, user_current_health)  # Ensure health doesn't go below 0

                        # Update the fight log for monster's attack
                        fight_log[-3] = f"{user_card['card_name']}: {user_current_health}/{user_max_health} HP"
                        fight_log[-2] = f"{monster['card_name']}: {monster_current_health}/{monster_max_health} HP"
                        fight_log[-1] = f"Damage dealt: {monster['card_name']} → {effective_monster_damage} (Raw: {monster_damage}, Def: {user_card['defence_stat']})"

                        round_log[-4:] = fight_log
                        embed.set_field_at(-1, name=f"⚔️ Round {current_round}", value="\n".join(round_log), inline=False)
                        await dungeon_message.edit(embed=embed)
                        await asyncio.sleep(2)

                # Determine the outcome of the fight
                if monster_current_health <= 0:
                    round_log.append(f"**{monster['card_name']} has been defeated!**")
                    round_monsters.pop(0)
                    # Update the user card's health for the next fight
                    user_card['current_health'] = user_current_health
                else:
                    round_log.append(f"**{user_card['card_name']} has been defeated!**")
                    user_team.pop(0)
                    # Update the monster's current health for the next fight
                    monster['current_health'] = monster_current_health

                embed.set_field_at(-1, name=f"⚔️ Round {current_round}", value="\n".join(round_log), inline=False)
                await dungeon_message.edit(embed=embed)
                await asyncio.sleep(3)

                fight_number += 1

                # Check if the round or dungeon is complete
                if not round_monsters and current_round < total_rounds:
                    round_log.append("\n**Round Complete!** Moving to the next round...")
                    embed.set_field_at(-1, name=f"⚔️ Round {current_round}", value="\n".join(round_log), inline=False)
                    
                    # Reset the embed image to the original dungeon image
                    embed.set_image(url=difficulty.value[2])
                    
                    await dungeon_message.edit(embed=embed)
                    await asyncio.sleep(3)
                elif not round_monsters and current_round == total_rounds:
                    embed.add_field(name="🎉 Dungeon Cleared!", value="Congratulations! You've defeated the boss and cleared the dungeon!", inline=False)
                    
                    # Reset the embed image to the original dungeon image
                    embed.set_image(url=difficulty.value[2])
                    
                    await dungeon_message.edit(embed=embed)
                    
                    # Get the user's inventory ID
                    connection = create_db_connection()
                    if not connection:
                        await ctx.send("Unable to connect to the database. Please try again later.")
                        return
                    
                    user_id = self.get_user_id(connection, ctx.author.id)
                    inventory_id = self.get_inventory_id(connection, user_id)
                    quest_message = await self.check_and_update_quest(connection, user_id)
                    if quest_message:
                        await ctx.send(quest_message)
                    # Call the treasure room method
                    await self.treasure_room(ctx, connection, inventory_id, difficulty.value[9], f"{difficulty.value[0].lower()} gate")
                    return
                elif not user_team:
                    embed.add_field(name="❌ Dungeon Failed", value=f"{ctx.author.mention} Your team has been defeated. Better luck next time!", inline=False)
                    
                    # Reset the embed image to the original dungeon image
                    embed.set_image(url=difficulty.value[2])
                    
                    await dungeon_message.edit(embed=embed)
                    return

        embed.add_field(name="🏁 Dungeon Complete", value="The dungeon challenge has ended.", inline=False)
        
        # Reset the embed image to the original dungeon image
        embed.set_image(url=difficulty.value[2])
        
        await dungeon_message.edit(embed=embed)
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
        return "Your task is to clear the dungeon and defeat the boss. Only then will you earn the rewards hidden within."

    def generate_stage_description(self, stage: str) -> str:
        return f"You've arrived at the {stage}. Prepare yourself for what's to come."
    async def treasure_room(self, ctx, connection, inventory_id, dungeon_id, dungeon_name):
        # Get dungeon items
        dungeon_items = self.get_dungeon_items(connection, dungeon_id)

        # Determine the gate and its corresponding caps
        gate_caps = {
            "easy gate": {'xp_boost': (5, 15), 'others': (1, 2), 'item_limit': 4},
            "medium gate": {'xp_boost': (10, 20), 'others': (1, 3), 'item_limit': 5},
            "hard gate": {'xp_boost': (10, 30), 'others': (1, 5), 'item_limit': 7}
        }

        caps = gate_caps.get(dungeon_name.lower(), gate_caps["easy gate"])

        # Choose random lists to keep (minimum 3)
        kept_lists = random.sample(list(dungeon_items.keys()), max(3, random.randint(3, len(dungeon_items))))
        rewards = {}
        for list_name in kept_lists:
            items = dungeon_items[list_name]
            num_items = min(len(items), random.randint(1, 3))
            chosen_items = random.sample(items, num_items)

            for item in chosen_items:
                if list_name == 'xp_boost':
                    quantity = random.randint(*caps['xp_boost'])
                else:
                    quantity = random.randint(*caps['others'])
                
                rewards[item['id']] = {
                    'name': item['name'],
                    'quantity': quantity,
                    'type': list_name
                }

        # Limit the number of items based on the gate's item_limit
        if len(rewards) > caps['item_limit']:
            rewards = dict(random.sample(rewards.items(), caps['item_limit']))

        # Update inventory
        for item_id, item_info in rewards.items():
            self.update_inventory(connection, inventory_id, item_id, item_info['quantity'], item_info['type'])

        # Calculate money reward
        money_query = "SELECT dungeon_moneycap FROM dungeon WHERE dungeon_id = %s"
        money_result = self.execute_query(connection, money_query, (dungeon_id,))
        money_cap = money_result[0]["dungeon_moneycap"]
        money_reward = self.calculate_money_reward(money_cap)

        # Get user_id
        user_id = self.get_user_id(connection, ctx.author.id)

        # Handle guild contribution and update user currency
        final_money_reward = await self.handle_guild_contribution(ctx, connection, user_id, money_reward)

        # Display rewards to the user
        reward_message = "You've obtained the following rewards:\n"
        for item_info in rewards.values():
            reward_message += f"- {item_info['quantity']}x {item_info['name']} ({item_info['type']})\n"
        reward_message += f"\nYou also received {final_money_reward} currency!"
        await ctx.send(reward_message)

        # Handle XP rewards
        team_id = self.get_team_id(connection, user_id)
        await self.handle_xp_rewards(ctx, connection, user_id, team_id, dungeon_id)

        return rewards

    async def handle_xp_rewards(self, ctx, connection, user_id, team_id, dungeon_id):
        # Get the user's team cards
        team_cards_query = """
        SELECT tc.*, c.card_name, c.card_rank, c.xp_cap, c.max_level, uc.current_level, uc.current_xp,
            uc.attack_stat, uc.health_stat, uc.defence_stat, uc.speed_stat
        FROM team_cards tc
        JOIN user_cards uc ON tc.card_id = uc.card_id AND uc.user_id = %s
        JOIN card c ON tc.card_id = c.card_id
        WHERE tc.team_id = %s
        """
        team_cards = self.execute_query(connection, team_cards_query, (user_id, team_id))

        xp_rewards = []

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

            new_xp = card['current_xp'] + xp_reward
            new_level = card['current_level']
            
            # Handle level up
            level_up = False
            if new_xp >= card['xp_cap']:
                if new_level < card['max_level']:
                    new_level += 1
                    new_xp = new_xp - card['xp_cap']
                    level_up = True
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
                attack_stat = %s, health_stat = %s, defence_stat = %s, speed_stat = %s,current_statpoints = current_statpoints + 5
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
                'attack_increase': attack_increase,
                'health_increase': health_increase,
                'defence_increase': defence_increase,
                'speed_increase': speed_increase
            })

        # Display XP rewards to the user
        xp_message = "XP Rewards:\n"
        for reward in xp_rewards:
            xp_message += f"- {reward['card_name']}: +{reward['xp_gained']} XP (Now Level {reward['new_level']} with {reward['new_xp']} XP)\n"
            if reward['level_up']:
                xp_message += f"  Level Up! Stats increased: ATK +{reward['attack_increase']}, HP +{reward['health_increase']}, DEF +{reward['defence_increase']}, SPD +{reward['speed_increase']}\n"
        
        await ctx.send(xp_message)
    def calculate_money_reward(self, money_cap):
        return random.randint(100, money_cap)

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
        self.update_user_currency(connection, user_id, final_reward)

        return final_reward

    def update_user_currency(self, connection, user_id, money_reward):
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

async def setup(bot):
    await bot.add_cog(DungeonCommands(bot))