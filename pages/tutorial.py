import discord
from discord.ext import commands
from discord.ui import Select, View, Button
import copy
from typing import List, Dict, Optional
from PIL import Image, ImageDraw, ImageFont
import os
import io
import asyncio
import random
import traceback
import logging
from datetime import datetime
import json
import time
from utils.database import execute_query_with_retry, get_metrics
from utils.db_atomic import db_advisory_lock, db_transaction

import functools

async def get_user_multilanguage(user_authorid: int) -> int:
    """
    Fetch multilanguage flag for a Discord user. Fallback to 0 (English).
    0 = English, 1 = Arabic, 3 = French
    """
    try:
        q = "SELECT multilanguage FROM user WHERE user_authorid = %s"
        res = await execute_query_with_retry(q, (str(user_authorid),))
        if res and 'multilanguage' in res[0] and res[0]['multilanguage'] is not None:
            return int(res[0]['multilanguage'])
    except Exception as e:
        pass
    return 0

def command_cooldown(rate=1, per=3.0, type=commands.BucketType.user):
    """
    A robust cooldown decorator that prevents command spam
    """
    cooldown = commands.Cooldown(rate, per)
    cooldowns = {}
    
    def decorator(func):
        @functools.wraps(func)
        async def wrapper(self, ctx, *args, **kwargs):
            if type == commands.BucketType.user:
                key = ctx.author.id
            elif type == commands.BucketType.channel:
                key = ctx.channel.id
            elif type == commands.BucketType.guild:
                key = ctx.guild.id if ctx.guild else ctx.author.id
            else:
                key = ctx.author.id
                
            bucket_key = f"{func.__name__}:{key}"
            if bucket_key in cooldowns:
                bucket = cooldowns[bucket_key]
                retry_after = bucket.update_rate_limit()
                if retry_after:
                    remaining = round(retry_after, 1)
                    await ctx.send(f"⏰ Command on cooldown. Please wait {remaining} seconds.")
                    return
            else:
                cooldowns[bucket_key] = commands.Cooldown(rate, per)
                
            return await func(self, ctx, *args, **kwargs)
        return wrapper
    return decorator

def _tr(lang: int, en: str, ar: str, fr: str) -> str:
    """Translate based on language code"""
    if lang == 1:
        return ar
    if lang == 3:
        return fr
    return en

async def _get_lang(discord_user_id: int) -> int:
    """Get user's language preference"""
    try:
        return await get_user_multilanguage(int(discord_user_id))
    except Exception:
        return 0

logger = logging.getLogger('tutorial_commands')
logger.setLevel(logging.DEBUG)

fh = logging.FileHandler('tutorial_commands.log')
fh.setLevel(logging.DEBUG)

ch = logging.StreamHandler()
ch.setLevel(logging.INFO)

formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
fh.setFormatter(formatter)
ch.setFormatter(formatter)

logger.addHandler(fh)
logger.addHandler(ch)


async def _add_inventory_quantity(table_name: str, id_column: str, inventory_id: int, item_id: int, quantity: int):
    rows = await execute_query_with_retry(
        f"""
        SELECT quantiter
        FROM {table_name}
        WHERE inventory_id = %s AND {id_column} = %s
        FOR UPDATE
        """,
        (inventory_id, item_id)
    )
    if rows:
        await execute_query_with_retry(
            f"""
            UPDATE {table_name}
            SET quantiter = quantiter + %s
            WHERE inventory_id = %s AND {id_column} = %s
            """,
            (quantity, inventory_id, item_id)
        )
    else:
        await execute_query_with_retry(
            f"""
            INSERT INTO {table_name} (inventory_id, {id_column}, quantiter)
            VALUES (%s, %s, %s)
            """,
            (inventory_id, item_id, quantity)
        )


async def _apply_tutorial_reward_rows(user_id: int, inventory_id: Optional[int], rewards: list[dict]):
    for reward in rewards:
        if reward.get('won_reward') and reward.get('won_reward') != 0:
            await execute_query_with_retry(
                "UPDATE user SET won_currency = won_currency + %s WHERE user_id = %s",
                (reward.get('won_quantity', 0), user_id)
            )

        if reward.get('gwon_reward') and reward.get('gwon_reward') != 0:
            await execute_query_with_retry(
                "UPDATE user SET gamble_currency = gamble_currency + %s WHERE user_id = %s",
                (reward.get('gwon_quantity', 0), user_id)
            )

        if inventory_id is None:
            continue

        if reward.get('product_id') and reward.get('product_id') != 0:
            await _add_inventory_quantity(
                "inventory_product", "product_id", inventory_id,
                reward.get('product_id'), reward.get('product_quantity', 0)
            )

        if reward.get('dungeonkey_id') and reward.get('dungeonkey_id') != 0:
            await _add_inventory_quantity(
                "inventory_dungeonkey", "dungeonkey_id", inventory_id,
                reward.get('dungeonkey_id'), reward.get('dungeonkey_quantity', 0)
            )

        if reward.get('chest_id') and reward.get('chest_id') != 0:
            await _add_inventory_quantity(
                "inventory_chest", "chest_id", inventory_id,
                reward.get('chest_id'), reward.get('chest_quantity', 0)
            )

        if reward.get('pickaxe_id') and reward.get('pickaxe_id') != 0:
            await _add_inventory_quantity(
                "inventory_pickaxe", "id_pickaxe", inventory_id,
                reward.get('pickaxe_id'), reward.get('pickaxe_quantity', 0)
            )

        if reward.get('orb_id') and reward.get('orb_id') != 0:
            await _add_inventory_quantity(
                "inventory_orbs", "orb_id", inventory_id,
                reward.get('orb_id'), reward.get('orb_quantity', 0)
            )

        if reward.get('skill_id') and reward.get('skill_id') != 0:
            await _add_inventory_quantity(
                "inventory_skills", "skill_id", inventory_id,
                reward.get('skill_id'), reward.get('skill_quantity', 0)
            )


async def complete_tutorial_rewards_atomic(user_id: int, subtutorial_id: int):
    async with db_advisory_lock(f"slbot:tutorial:{user_id}:{subtutorial_id}", timeout=5):
        async with db_transaction():
            completed = await execute_query_with_retry(
                """
                SELECT 1
                FROM user_tutorials
                WHERE user_id = %s AND subtutorial_id = %s
                FOR UPDATE
                """,
                (user_id, subtutorial_id)
            )
            if completed:
                await execute_query_with_retry(
                    "DELETE FROM quiz_status WHERE user_id = %s AND subtutorial_id = %s",
                    (user_id, subtutorial_id)
                )
                return {"status": "already_completed"}

            if subtutorial_id == 14:
                skills = await execute_query_with_retry(
                    """
                    SELECT DISTINCT tr.skill_id, s.skill_name
                    FROM tutorial_rewards tr
                    JOIN skill s ON tr.skill_id = s.skill_id
                    WHERE tr.subtutorial_id = %s AND tr.skill_id != 0
                    """,
                    (subtutorial_id,)
                )
                if skills and len(skills) > 1:
                    return {"status": "skill_selection", "skills": skills}

            await execute_query_with_retry(
                "INSERT INTO user_tutorials (user_id, subtutorial_id) VALUES (%s, %s)",
                (user_id, subtutorial_id)
            )

            rewards = await execute_query_with_retry(
                "SELECT * FROM tutorial_rewards WHERE subtutorial_id = %s",
                (subtutorial_id,)
            )
            if rewards:
                inventory_rows = await execute_query_with_retry(
                    "SELECT inventory_id FROM inventory WHERE user_id = %s FOR UPDATE",
                    (user_id,)
                )
                inventory_id = inventory_rows[0]['inventory_id'] if inventory_rows else None
                await _apply_tutorial_reward_rows(user_id, inventory_id, rewards)

            await execute_query_with_retry(
                "DELETE FROM quiz_status WHERE user_id = %s AND subtutorial_id = %s",
                (user_id, subtutorial_id)
            )
            return {"status": "completed"}


async def select_tutorial_skill_atomic(user_id: int, subtutorial_id: int, skill_id: int):
    async with db_advisory_lock(f"slbot:tutorial:{user_id}:{subtutorial_id}", timeout=5):
        async with db_transaction():
            completed = await execute_query_with_retry(
                """
                SELECT 1
                FROM user_tutorials
                WHERE user_id = %s AND subtutorial_id = %s
                FOR UPDATE
                """,
                (user_id, subtutorial_id)
            )
            if completed:
                await execute_query_with_retry(
                    "DELETE FROM quiz_status WHERE user_id = %s AND subtutorial_id = %s",
                    (user_id, subtutorial_id)
                )
                return {"status": "already_completed"}

            inventory_rows = await execute_query_with_retry(
                "SELECT inventory_id FROM inventory WHERE user_id = %s FOR UPDATE",
                (user_id,)
            )
            if not inventory_rows:
                return {"status": "inventory_missing"}

            reward_rows = await execute_query_with_retry(
                """
                SELECT skill_quantity
                FROM tutorial_rewards
                WHERE subtutorial_id = %s AND skill_id = %s
                """,
                (subtutorial_id, skill_id)
            )
            skill_quantity = reward_rows[0]['skill_quantity'] if reward_rows else 1

            await execute_query_with_retry(
                "INSERT INTO user_tutorials (user_id, subtutorial_id) VALUES (%s, %s)",
                (user_id, subtutorial_id)
            )
            await _add_inventory_quantity(
                "inventory_skills", "skill_id", inventory_rows[0]['inventory_id'], skill_id, skill_quantity
            )
            await execute_query_with_retry(
                "DELETE FROM quiz_status WHERE user_id = %s AND subtutorial_id = %s",
                (user_id, subtutorial_id)
            )
            return {"status": "completed", "quantity": skill_quantity}

class TutorialNavigationView(View):
    def __init__(self, tutorials: List[Dict], current_index: int, lang: int, user_id: int, user_authorid: int):
        super().__init__(timeout=300)
        self.tutorials = tutorials
        self.current_index = current_index
        self.lang = lang
        self.user_id = user_id
        self.user_authorid = user_authorid
        self.update_buttons()
    
    def update_buttons(self):
        self.prev_button.disabled = self.current_index == 0
        self.next_button.disabled = self.current_index == len(self.tutorials) - 1
    
    @discord.ui.button(label="◀", style=discord.ButtonStyle.primary)
    async def prev_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_authorid:
            await interaction.response.send_message("You cannot use this button.", ephemeral=True)
            return
        self.current_index = max(0, self.current_index - 1)
        self.update_buttons()
        await self.update_embed(interaction)
    
    @discord.ui.button(label="▶", style=discord.ButtonStyle.primary)
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_authorid:
            await interaction.response.send_message("You cannot use this button.", ephemeral=True)
            return
        self.current_index = min(len(self.tutorials) - 1, self.current_index + 1)
        self.update_buttons()
        await self.update_embed(interaction)
    
    async def update_embed(self, interaction: discord.Interaction):
        embed = await self.create_tutorial_embed()
        await interaction.response.edit_message(embed=embed, view=self)
    
    async def create_tutorial_embed(self) -> discord.Embed:
        tutorial = self.tutorials[self.current_index]
        tutorial_id = tutorial['id_tutorial']
        
        title = _tr(self.lang,
                   tutorial.get('title_eng', ''),
                   tutorial.get('title_ar', ''),
                   tutorial.get('title_fr', ''))
        
        description = _tr(self.lang, 
                         tutorial.get('tutorial_descritpion', ''),
                         tutorial.get('tutorial_ardescription', ''),
                         tutorial.get('tutorial_frdescription', ''))
        
        # Get status
        status = await self.get_tutorial_status(tutorial_id)
        
        embed = discord.Embed(
            title=f"📚 {title}",
            description=description,
            color=discord.Color.from_rgb(47, 49, 54)
        )
        embed.add_field(name="Status", value=status, inline=False)
        embed.set_footer(text=f"Tutorial {self.current_index + 1}/{len(self.tutorials)}")
        
        # Get subtutorials
        subtutorials = await self.get_subtutorials(tutorial_id)
        if subtutorials:
            subtut_text = ""
            for sub in subtutorials:
                sub_title = _tr(self.lang,
                               sub.get('title_eng', ''),
                               sub.get('title_ar', ''),
                               sub.get('title_fr', ''))
                sub_status = await self.get_subtutorial_status(sub['subtutorial_id'])
                
                rewards_text = await self.get_subtutorial_rewards_text(sub['subtutorial_id'])
                
                subtut_text += f"**Subtutorial {sub['subtutorial_id']}: {sub_title}**\n"
                subtut_text += f"Status: {sub_status}\n"
                if rewards_text:
                    subtut_text += f"Rewards: {rewards_text}\n"
                subtut_text += f"How to proceed: `sl tutostart {sub['subtutorial_id']}`\n\n"
            
            embed.add_field(name="🎯 Subtutorial", value=subtut_text, inline=False)
        
        return embed
    
    async def get_subtutorial_rewards_text(self, subtutorial_id: int) -> str:
        try:
            q = """SELECT * FROM tutorial_rewards 
                   WHERE subtutorial_id = %s"""
            res = await execute_query_with_retry(q, (subtutorial_id,))
            
            if not res:
                return ""
            
            reward_items = []
            
            for reward in res:
                # Check won_reward (currency)
                if reward.get('won_reward') == 1:
                    reward_items.append(f"💰 x{reward.get('won_quantity', 0)}")
                
                # Check gwon_reward (gamble currency)
                if reward.get('gwon_reward') == 1:
                    reward_items.append(f"🎲 x{reward.get('gwon_quantity', 0)}")
                
                # Check product_id
                if reward.get('product_id') and reward.get('product_id') != 0:
                    q = """SELECT product_name, product_icon FROM product 
                           WHERE product_id = %s"""
                    prod_res = await execute_query_with_retry(q, (reward.get('product_id'),))
                    if prod_res:
                        name = prod_res[0].get('product_name', 'Product')
                        icon = prod_res[0].get('product_icon', '📦')
                        reward_items.append(f"{icon} {name} x{reward.get('product_quantity', 0)}")
                
                # Check dungeonkey_id
                if reward.get('dungeonkey_id') and reward.get('dungeonkey_id') != 0:
                    q = """SELECT dungeonkey_name, dungeonkey_icon FROM dungeon_key 
                           WHERE dungeonkey_id = %s"""
                    key_res = await execute_query_with_retry(q, (reward.get('dungeonkey_id'),))
                    if key_res:
                        name = key_res[0].get('dungeonkey_name', 'Key')
                        icon = key_res[0].get('dungeonkey_icon', '🔑')
                        reward_items.append(f"{icon} {name} x{reward.get('dungeonkey_quantity', 0)}")
                
                # Check chest_id
                if reward.get('chest_id') and reward.get('chest_id') != 0:
                    q = """SELECT chest_name, chest_icon FROM chest 
                           WHERE chest_id = %s"""
                    chest_res = await execute_query_with_retry(q, (reward.get('chest_id'),))
                    if chest_res:
                        name = chest_res[0].get('chest_name', 'Chest')
                        icon = chest_res[0].get('chest_icon', '📦')
                        reward_items.append(f"{icon} {name} x{reward.get('chest_quantity', 0)}")
                
                # Check pickaxe_id
                if reward.get('pickaxe_id') and reward.get('pickaxe_id') != 0:
                    q = """SELECT pickaxe_name, pickaxe_icon FROM pickaxe 
                           WHERE id_pickaxe = %s"""
                    pick_res = await execute_query_with_retry(q, (reward.get('pickaxe_id'),))
                    if pick_res:
                        name = pick_res[0].get('pickaxe_name', 'Pickaxe')
                        icon = pick_res[0].get('pickaxe_icon', '⛏️')
                        reward_items.append(f"{icon} {name} x{reward.get('pickaxe_quantity', 0)}")
                
                # Check orb_id
                if reward.get('orb_id') and reward.get('orb_id') != 0:
                    q = """SELECT orb_name, orb_icon FROM leveling_orbs 
                           WHERE orb_id = %s"""
                    orb_res = await execute_query_with_retry(q, (reward.get('orb_id'),))
                    if orb_res:
                        name = orb_res[0].get('orb_name', 'Orb')
                        icon = orb_res[0].get('orb_icon', '🔮')
                        reward_items.append(f"{icon} {name} x{reward.get('orb_quantity', 0)}")
                
                # Check skill_id
                if reward.get('skill_id') and reward.get('skill_id') != 0:
                    q = """SELECT skill_name FROM skill 
                           WHERE skill_id = %s"""
                    skill_res = await execute_query_with_retry(q, (reward.get('skill_id'),))
                    if skill_res:
                        name = skill_res[0].get('skill_name', 'Skill')
                        reward_items.append(f"✨ {name} x{reward.get('skill_quantity', 0)}")
            
            return ", ".join(reward_items) if reward_items else ""
        except Exception as e:
            logger.error(f"Error getting subtutorial rewards: {e}")
            return ""
    
    async def get_tutorial_status(self, tutorial_id: int) -> str:
        try:
            # Count total subtutorials for this tutorial
            q = "SELECT COUNT(*) as count FROM subtutorial WHERE tutorial_id = %s"
            res = await execute_query_with_retry(q, (tutorial_id,))
            total_subtutorials = res[0]['count'] if res else 0
            
            if total_subtutorials == 0:
                return "Not Started"
            
            q = """SELECT COUNT(DISTINCT subtutorial_id) as completed 
                   FROM user_tutorials 
                   WHERE user_id = %s AND subtutorial_id IN (SELECT subtutorial_id FROM subtutorial WHERE tutorial_id = %s)"""
            res = await execute_query_with_retry(q, (self.user_id, tutorial_id))
            completed = res[0]['completed'] if res else 0
            
            if completed == 0:
                return "❌ Not Started"
            elif completed == total_subtutorials:
                return "✅ Completed"
            else:
                return "⏳ In Progress"
        except Exception as e:
            logger.error(f"Error getting tutorial status: {e}")
            return "Unknown"
    
    async def get_subtutorial_status(self, subtutorial_id: int) -> str:
        try:
            q = """SELECT COUNT(*) as count FROM user_tutorials 
                   WHERE user_id = %s AND subtutorial_id = %s"""
            res = await execute_query_with_retry(q, (self.user_id, subtutorial_id))
            count = res[0]['count'] if res else 0
            return "✅ Completed" if count > 0 else "❌ Not Started"
        except Exception as e:
            logger.error(f"Error getting subtutorial status: {e}")
            return "❓ Unknown"
    
    async def get_subtutorials(self, tutorial_id: int) -> List[Dict]:
        try:
            q = "SELECT * FROM subtutorial WHERE tutorial_id = %s ORDER BY subtutorial_id"
            res = await execute_query_with_retry(q, (tutorial_id,))
            return res if res else []
        except Exception as e:
            logger.error(f"Error getting subtutorials: {e}")
            return []

class QuizNavigationView(View):
    def __init__(self, subtutorial_id: int, lang: int, user_id: int, user_authorid: int, ctx):
        super().__init__(timeout=600)
        self.subtutorial_id = subtutorial_id
        self.lang = lang
        self.user_id = user_id
        self.user_authorid = user_authorid
        self.ctx = ctx
        self.current_page = 0  # 0 = description, 1 = tips, 2+ = questions
        self.questions = []
        self.current_question_index = 0
        self.correct_answers = 0
        self.quiz_started = False
        self.already_completed = False  # Track if user already completed this quest
        self.update_buttons()
    
    def update_buttons(self):
        self.prev_button.disabled = self.current_page == 0 or self.quiz_started
        self.next_button.disabled = self.current_page == 1 or self.quiz_started
        self.start_quiz_button.disabled = self.quiz_started
    
    @discord.ui.button(label="◀", style=discord.ButtonStyle.primary)
    async def prev_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_authorid:
            await interaction.response.send_message("You cannot use this button.", ephemeral=True)
            return
        
        self.current_page = max(0, self.current_page - 1)
        self.update_buttons()
        await self.update_embed(interaction)
    
    @discord.ui.button(label="▶", style=discord.ButtonStyle.primary)
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_authorid:
            await interaction.response.send_message("You cannot use this button.", ephemeral=True)
            return
        
        self.current_page = min(1, self.current_page + 1)
        self.update_buttons()
        await self.update_embed(interaction)
    
    @discord.ui.button(label="Start Quiz", style=discord.ButtonStyle.success)
    async def start_quiz_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_authorid:
            await interaction.response.send_message("You cannot use this button.", ephemeral=True)
            return
        
        # Check if quiz already in progress
        try:
            q = "SELECT * FROM quiz_status WHERE user_id = %s AND subtutorial_id = %s"
            res = await execute_query_with_retry(q, (self.user_id, self.subtutorial_id))
            if res:
                await interaction.response.send_message(
                    "You already have this quiz in progress!",
                    ephemeral=True
                )
                return
            
            # Insert quiz status
            q = """INSERT INTO quiz_status (user_id, subtutorial_id) 
                   VALUES (%s, %s)"""
            await execute_query_with_retry(q, (self.user_id, self.subtutorial_id))
            
            # Load questions
            await self.load_questions()
            self.quiz_started = True
            self.current_question_index = 0
            self.update_buttons()
            
            embed = await self.create_question_embed()
            answer_view = QuestionAnswerView(self, self.user_id, self.user_authorid)
            await answer_view.load_options()
            await interaction.response.edit_message(embed=embed, view=answer_view)
        except Exception as e:
            logger.error(f"Error starting quiz: {e}")
            await interaction.response.send_message("Error starting quiz.", ephemeral=True)
    
    async def load_questions(self):
        try:
            q = "SELECT subtutorialquest_id FROM subtutorial WHERE subtutorial_id = %s"
            res = await execute_query_with_retry(q, (self.subtutorial_id,))
            if not res:
                return
            
            quest_id = res[0]['subtutorialquest_id']
            
            q = """SELECT * FROM quest_questions 
                   WHERE subtutorialquest_id = %s 
                   ORDER BY id_questquestions"""
            res = await execute_query_with_retry(q, (quest_id,))
            self.questions = res if res else []
        except Exception as e:
            logger.error(f"Error loading questions: {e}")
            self.questions = []
    
    async def update_embed(self, interaction: discord.Interaction):
        if self.current_page == 0:
            embed = await self.create_description_embed()
        elif self.current_page == 1:
            embed = await self.create_tips_embed()
        else:
            embed = await self.create_question_embed()
        
        await interaction.response.edit_message(embed=embed, view=self)
    
    async def create_description_embed(self) -> discord.Embed:
        try:
            q = "SELECT * FROM subtutorial WHERE subtutorial_id = %s"
            res = await execute_query_with_retry(q, (self.subtutorial_id,))
            if not res:
                return discord.Embed(title="Error", description="Subtutorial not found")
            
            sub = res[0]
            description = _tr(self.lang,
                            sub.get('subtutorial_description', ''),
                            sub.get('subtutorial_ardescription', ''),
                            sub.get('subtutorial_frdescription', ''))
            
            title = _tr(self.lang,
                       sub.get('title_eng', ''),
                       sub.get('title_ar', ''),
                       sub.get('title_fr', ''))
            
            embed = discord.Embed(
                title=title,
                description=description,
                color=discord.Color.blue()
            )
            return embed
        except Exception as e:
            logger.error(f"Error creating description embed: {e}")
            return discord.Embed(title="Error", description="Failed to load subtutorial")
    
    async def create_tips_embed(self) -> discord.Embed:
        try:
            q = "SELECT * FROM subtutorial WHERE subtutorial_id = %s"
            res = await execute_query_with_retry(q, (self.subtutorial_id,))
            if not res:
                return discord.Embed(title="Error", description="Subtutorial not found")
            
            sub = res[0]
            
            tips_text = _tr(self.lang,
                           sub.get('tip_description', ''),
                           sub.get('tip_ardescription', ''),
                           sub.get('tip_frdescription', ''))
            
            # Split tips by semicolon
            tips_list = [tip.strip() for tip in tips_text.split(';') if tip.strip()]
            tips_formatted = "\n".join([f"- {tip}" for tip in tips_list])
            
            embed = discord.Embed(
                title=_tr(self.lang, "Tips", "نصائح", "Conseils"),
                description=tips_formatted if tips_formatted else "No tips available",
                color=discord.Color.green()
            )
            embed.set_footer(text="Complete the quiz to claim rewards")
            return embed
        except Exception as e:
            logger.error(f"Error creating tips embed: {e}")
            return discord.Embed(title="Error", description="Failed to load tips")
    
    async def create_question_embed(self) -> discord.Embed:
        if self.current_question_index >= len(self.questions):
            return await self.create_rewards_embed()
        
        question = self.questions[self.current_question_index]
        
        desc_text = _tr(self.lang,
                       question.get('question_description', ''),
                       question.get('question_ardescription', ''),
                       question.get('question_frdescription', ''))
        
        embed = discord.Embed(
            title=f"Question {self.current_question_index + 1}/{len(self.questions)}",
            description=desc_text,
            color=discord.Color.orange()
        )
        
        return embed
    
    async def create_rewards_embed(self) -> discord.Embed:
        try:
            q = """SELECT * FROM tutorial_rewards 
                   WHERE subtutorial_id = %s"""
            res = await execute_query_with_retry(q, (self.subtutorial_id,))
            
            rewards_text = ""
            
            if res:
                for reward in res:
                    reward_items = []
                    
                    # Check won_reward (currency)
                    if reward.get('won_reward') and reward.get('won_reward') != 0:
                        reward_items.append(f"💰 Won Currency x{reward.get('won_quantity', 0)}")
                    
                    # Check gwon_reward (gamble currency)
                    if reward.get('gwon_reward') and reward.get('gwon_reward') != 0:
                        reward_items.append(f"🎲 Gamble Currency x{reward.get('gwon_quantity', 0)}")
                    
                    # Check product_id
                    if reward.get('product_id') and reward.get('product_id') != 0:
                        q = """SELECT product_name, product_icon FROM product 
                               WHERE product_id = %s"""
                        prod_res = await execute_query_with_retry(q, (reward.get('product_id'),))
                        if prod_res:
                            name = prod_res[0].get('product_name', 'Product')
                            icon = prod_res[0].get('product_icon', '📦')
                            reward_items.append(f"{icon} {name} x{reward.get('product_quantity', 0)}")
                    
                    # Check dungeonkey_id
                    if reward.get('dungeonkey_id') and reward.get('dungeonkey_id') != 0:
                        q = """SELECT dungeonkey_name, dungeonkey_icon FROM dungeon_key 
                               WHERE dungeonkey_id = %s"""
                        key_res = await execute_query_with_retry(q, (reward.get('dungeonkey_id'),))
                        if key_res:
                            name = key_res[0].get('dungeonkey_name', 'Key')
                            icon = key_res[0].get('dungeonkey_icon', '🔑')
                            reward_items.append(f"{icon} {name} x{reward.get('dungeonkey_quantity', 0)}")
                    
                    # Check chest_id
                    if reward.get('chest_id') and reward.get('chest_id') != 0:
                        q = """SELECT chest_name, chest_icon FROM chest 
                               WHERE chest_id = %s"""
                        chest_res = await execute_query_with_retry(q, (reward.get('chest_id'),))
                        if chest_res:
                            name = chest_res[0].get('chest_name', 'Chest')
                            icon = chest_res[0].get('chest_icon', '📦')
                            reward_items.append(f"{icon} {name} x{reward.get('chest_quantity', 0)}")
                    
                    # Check pickaxe_id
                    if reward.get('pickaxe_id') and reward.get('pickaxe_id') != 0:
                        q = """SELECT pickaxe_name, pickaxe_icon FROM pickaxe 
                               WHERE id_pickaxe = %s"""
                        pick_res = await execute_query_with_retry(q, (reward.get('pickaxe_id'),))
                        if pick_res:
                            name = pick_res[0].get('pickaxe_name', 'Pickaxe')
                            icon = pick_res[0].get('pickaxe_icon', '⛏️')
                            reward_items.append(f"{icon} {name} x{reward.get('pickaxe_quantity', 0)}")
                    
                    # Check orb_id
                    if reward.get('orb_id') and reward.get('orb_id') != 0:
                        q = """SELECT orb_name, orb_icon FROM leveling_orbs 
                               WHERE orb_id = %s"""
                        orb_res = await execute_query_with_retry(q, (reward.get('orb_id'),))
                        if orb_res:
                            name = orb_res[0].get('orb_name', 'Orb')
                            icon = orb_res[0].get('orb_icon', '🔮')
                            reward_items.append(f"{icon} {name} x{reward.get('orb_quantity', 0)}")
                    
                    # Check skill_id
                    if reward.get('skill_id') and reward.get('skill_id') != 0:
                        q = """SELECT skill_name, skill_icon FROM skill
                               WHERE skill_id = %s"""
                        skill_res = await execute_query_with_retry(q, (reward.get('skill_id'),))
                        if skill_res:
                            name = skill_res[0].get('skill_name', 'Skill')
                            icon = skill_res[0].get('skill_icon', '✨')
                            reward_items.append(f"{icon} {name} x{reward.get('skill_quantity', 0)}")
                    
                    rewards_text += ", ".join(reward_items) + "\n"
            
            if self.already_completed:
                crossed_rewards = "\n".join([f"~~{line}~~" for line in rewards_text.split("\n") if line])
                embed = discord.Embed(
                    title=_tr(self.lang, "Already Completed", "تم إكماله بالفعل", "Déjà Complété"),
                    description=_tr(self.lang, "You already completed this quest before, hence you can't get reward twice", "لقد أكملت هذا الاختبار من قبل، لذا لا يمكنك الحصول على المكافأة مرتين", "Vous avez déjà complété ce quiz, donc vous ne pouvez pas obtenir la récompense deux fois"),
                    color=discord.Color.greyple()
                )
                if crossed_rewards:
                    embed.add_field(name="Rewards", value=crossed_rewards, inline=False)
            else:
                embed = discord.Embed(
                    title=_tr(self.lang, "Quiz Completed!", "تم إكمال الاختبار!", "Quiz Complété!"),
                    description=_tr(self.lang, "You earned:", "لقد حصلت على:", "Vous avez gagné:"),
                    color=discord.Color.gold()
                )
                
                if rewards_text:
                    embed.add_field(name="Rewards", value=rewards_text, inline=False)
            
            return embed
        except Exception as e:
            logger.error(f"Error creating rewards embed: {e}")
            return discord.Embed(title="Rewards", description="Quiz completed!")
    async def on_timeout(self):
        """Clean up quiz_status when view times out"""
        try:
            if self.quiz_started:
                q = """DELETE FROM quiz_status 
                       WHERE user_id = %s AND subtutorial_id = %s"""
                await execute_query_with_retry(q, (self.user_id, self.subtutorial_id))
                logger.info(f"Cleaned up quiz_status for user {self.user_id}, subtutorial {self.subtutorial_id} due to timeout")
        except Exception as e:
            logger.error(f"Error cleaning up quiz_status on timeout: {e}")
class SkillSelectionView(View):
    """Special view for subtutorial 14 to let users choose which skill they want"""
    def __init__(self, quiz_view: QuizNavigationView, user_id: int, user_authorid: int, skills: List[Dict]):
        super().__init__(timeout=600)
        self.quiz_view = quiz_view
        self.user_id = user_id
        self.user_authorid = user_authorid
        self.skills = skills
        self.add_skill_buttons()
    
    def add_skill_buttons(self):
        """Add a button for each skill"""
        for skill in self.skills:
            skill_id = skill.get('skill_id')
            skill_name = skill.get('skill_name', 'Unknown Skill')
            
            async def skill_callback(interaction: discord.Interaction, sid=skill_id, sname=skill_name):
                if interaction.user.id != self.user_authorid:
                    await interaction.response.send_message("You cannot use this button.", ephemeral=True)
                    return

                await self.select_skill(interaction, sid, sname)
            
            button = discord.ui.Button(label=skill_name[:80], style=discord.ButtonStyle.primary)
            button.callback = skill_callback
            self.add_item(button)
    
    async def select_skill(self, interaction: discord.Interaction, skill_id: int, skill_name: str):
        """Handle skill selection and distribute only that skill"""
        try:
            result = await select_tutorial_skill_atomic(self.user_id, self.quiz_view.subtutorial_id, skill_id)
            if result["status"] == "inventory_missing":
                await interaction.response.send_message("Error: Inventory not found.", ephemeral=True)
                return

            if result["status"] == "already_completed":
                self.quiz_view.already_completed = True
                await interaction.response.send_message("You already completed this quest before.", ephemeral=True)
                return

            skill_quantity = result["quantity"]
            
            # Show success message
            embed = discord.Embed(
                title=_tr(self.quiz_view.lang, "Skill Selected!", "تم اختيار المهارة!", "Compétence Sélectionnée!"),
                description=_tr(self.quiz_view.lang, "You earned:", "لقد حصلت على:", "Vous avez gagné:"),
                color=discord.Color.gold()
            )
            embed.add_field(name="Reward", value=f"✨ {skill_name} x{skill_quantity}", inline=False)
            
            back_view = discord.ui.View()
            back_btn = discord.ui.Button(label="◀ Back to Tutorials", style=discord.ButtonStyle.secondary)
            
            async def back_callback(inter: discord.Interaction):
                if inter.user.id != self.user_authorid:
                    await inter.response.send_message("You cannot use this button.", ephemeral=True)
                    return
                self.quiz_view.current_page = 0
                self.quiz_view.quiz_started = False
                self.quiz_view.update_buttons()
                embed = await self.quiz_view.create_description_embed()
                await inter.response.edit_message(embed=embed, view=self.quiz_view)
            
            back_btn.callback = back_callback
            back_view.add_item(back_btn)
            
            await interaction.response.edit_message(embed=embed, view=back_view)
        
        except Exception as e:
            logger.error(f"Error selecting skill: {e}")
            await interaction.response.send_message("Error processing skill selection.", ephemeral=True)

    async def on_timeout(self):
        try:
            await execute_query_with_retry(
                "DELETE FROM quiz_status WHERE user_id = %s AND subtutorial_id = %s",
                (self.user_id, self.quiz_view.subtutorial_id)
            )
        except Exception as e:
            logger.error(f"Error cleaning up skill selection quiz_status on timeout: {e}")

class QuestionAnswerView(View):
    def __init__(self, quiz_view: QuizNavigationView, user_id: int, user_authorid: int):
        super().__init__(timeout=600)
        self.quiz_view = quiz_view
        self.user_id = user_id
        self.user_authorid = user_authorid
        self.option_values = {}
        self.setup_answer_buttons()
    
    def setup_answer_buttons(self):
        if self.quiz_view.current_question_index >= len(self.quiz_view.questions):
            return
        
        question = self.quiz_view.questions[self.quiz_view.current_question_index]
        q_id = question['id_questquestions']
        
        # This will be populated asynchronously
        self.q_id = q_id
    
    async def load_options(self):
        """Load language-specific options from database"""
        try:
            q = """SELECT first_option, second_option, third_option,
                          first_froption, second_froption, third_froption,
                          first_aroption, second_aroption, third_aroption
                   FROM question_options 
                   WHERE id_questquestions = %s"""
            res = await execute_query_with_retry(q, (self.q_id,))
            
            if res:
                if self.quiz_view.lang == 1:  # Arabic
                    self.option_values = {
                        'first_option': res[0].get('first_aroption', ''),
                        'second_option': res[0].get('second_aroption', ''),
                        'third_option': res[0].get('third_aroption', '')
                    }
                elif self.quiz_view.lang == 3:  # French
                    self.option_values = {
                        'first_option': res[0].get('first_froption', ''),
                        'second_option': res[0].get('second_froption', ''),
                        'third_option': res[0].get('third_froption', '')
                    }
                else:  # English (default)
                    self.option_values = {
                        'first_option': res[0].get('first_option', ''),
                        'second_option': res[0].get('second_option', ''),
                        'third_option': res[0].get('third_option', '')
                    }
                
                # Update button labels with actual option text
                if len(self.children) >= 3:
                    self.children[0].label = self.option_values['first_option'][:80]
                    self.children[1].label = self.option_values['second_option'][:80]
                    self.children[2].label = self.option_values['third_option'][:80]
        except Exception as e:
            logger.error(f"Error loading options: {e}")
    
    @discord.ui.button(label="Option 1", style=discord.ButtonStyle.primary)
    async def option1_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_authorid:
            await interaction.response.send_message("You cannot use this button.", ephemeral=True)
            return
        
        await self.check_answer(interaction, "first_option")
    
    @discord.ui.button(label="Option 2", style=discord.ButtonStyle.primary)
    async def option2_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_authorid:
            await interaction.response.send_message("You cannot use this button.", ephemeral=True)
            return
        
        await self.check_answer(interaction, "second_option")
    
    @discord.ui.button(label="Option 3", style=discord.ButtonStyle.primary)
    async def option3_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_authorid:
            await interaction.response.send_message("You cannot use this button.", ephemeral=True)
            return
        
        await self.check_answer(interaction, "third_option")
    
    @discord.ui.button(label="◀ Back", style=discord.ButtonStyle.secondary)
    async def back_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_authorid:
            await interaction.response.send_message("You cannot use this button.", ephemeral=True)
            return
        
        try:
            q = """DELETE FROM quiz_status 
                   WHERE user_id = %s AND subtutorial_id = %s"""
            await execute_query_with_retry(q, (self.user_id, self.quiz_view.subtutorial_id))
        except Exception as e:
            logger.error(f"Error deleting quiz_status on back: {e}")
        
        self.quiz_view.quiz_started = False
        self.quiz_view.current_page = 1
        self.quiz_view.update_buttons()
        embed = await self.quiz_view.create_tips_embed()
        await interaction.response.edit_message(embed=embed, view=self.quiz_view)
    
    async def check_answer(self, interaction: discord.Interaction, selected_option: str):
        try:
            question = self.quiz_view.questions[self.quiz_view.current_question_index]
            q_id = question['id_questquestions']
            
            q = """SELECT correct_response, correct_frresponse, correct_arresponse 
                   FROM quest_questions 
                   WHERE id_questquestions = %s"""
            res = await execute_query_with_retry(q, (q_id,))
            
            if not res:
                await interaction.response.send_message("Error loading question.", ephemeral=True)
                return
            
            if self.quiz_view.lang == 1:  # Arabic
                correct_response = res[0].get('correct_arresponse', '')
            elif self.quiz_view.lang == 3:  # French
                correct_response = res[0].get('correct_frresponse', '')
            else:  # English (default)
                correct_response = res[0].get('correct_response', '')
            
            q = """SELECT first_option, second_option, third_option,
                          first_froption, second_froption, third_froption,
                          first_aroption, second_aroption, third_aroption
                   FROM question_options 
                   WHERE id_questquestions = %s"""
            options_res = await execute_query_with_retry(q, (q_id,))
            
            if not options_res:
                await interaction.response.send_message("Error loading options.", ephemeral=True)
                return
            
            options = options_res[0]
            
            if self.quiz_view.lang == 1:  # Arabic
                selected_value = options.get(f"{selected_option.replace('_option', '_aroption')}", '')
            elif self.quiz_view.lang == 3:  # French
                selected_value = options.get(f"{selected_option.replace('_option', '_froption')}", '')
            else:  # English (default)
                selected_value = options.get(selected_option, '')

            if selected_value == correct_response:
                self.quiz_view.correct_answers += 1
                self.quiz_view.current_question_index += 1
                
                if self.quiz_view.current_question_index >= len(self.quiz_view.questions):
                    result = await self.distribute_rewards()
                    
                    # Check if result is a tuple (embed, view) for skill selection
                    if isinstance(result, tuple) and len(result) == 2:
                        embed, skill_view = result
                        await interaction.response.edit_message(embed=embed, view=skill_view)
                    else:
                        # Normal rewards display
                        embed = await self.quiz_view.create_rewards_embed()
                        rewards_view = discord.ui.View()
                        back_btn = discord.ui.Button(label="◀ Back to Tutorials", style=discord.ButtonStyle.secondary)
                        
                        async def back_to_tutorials(inter: discord.Interaction):
                            if inter.user.id != self.user_authorid:
                                await inter.response.send_message("You cannot use this button.", ephemeral=True)
                                return
                            self.quiz_view.current_page = 0
                            self.quiz_view.quiz_started = False
                            self.quiz_view.update_buttons()
                            embed = await self.quiz_view.create_description_embed()
                            await inter.response.edit_message(embed=embed, view=self.quiz_view)
                        
                        back_btn.callback = back_to_tutorials
                        rewards_view.add_item(back_btn)
                        await interaction.response.edit_message(embed=embed, view=rewards_view)
                else:
                    embed = await self.quiz_view.create_question_embed()
                    new_answer_view = QuestionAnswerView(self.quiz_view, self.user_id, self.user_authorid)
                    await new_answer_view.load_options()
                    await interaction.response.edit_message(embed=embed, view=new_answer_view)
            else:
                await interaction.response.send_message(
                    f"❌ Wrong answer!",
                    ephemeral=True
                )
        except Exception as e:
            logger.error(f"Error checking answer: {e}")
            await interaction.response.send_message("Error processing answer.", ephemeral=True)
    async def on_timeout(self):
        """Clean up quiz_status when view times out"""
        try:
            if self.quiz_started:
                q = """DELETE FROM quiz_status 
                       WHERE user_id = %s AND subtutorial_id = %s"""
                await execute_query_with_retry(q, (self.user_id, self.subtutorial_id))
                logger.info(f"Cleaned up quiz_status for user {self.user_id}, subtutorial {self.subtutorial_id} due to timeout")
        except Exception as e:
            logger.error(f"Error cleaning up quiz_status on timeout: {e}")                
    async def distribute_rewards(self):
        try:
            result = await complete_tutorial_rewards_atomic(self.user_id, self.quiz_view.subtutorial_id)

            if result["status"] == "already_completed":
                self.quiz_view.already_completed = True
                return None

            if result["status"] == "skill_selection":
                skill_selection_view = SkillSelectionView(self.quiz_view, self.user_id, self.user_authorid, result["skills"])
                embed = discord.Embed(
                    title=_tr(self.quiz_view.lang, "Choose Your Skill", "اختر مهارتك", "Choisissez Votre Compétence"),
                    description=_tr(self.quiz_view.lang, "Select which skill you want to receive:", "اختر المهارة التي تريد الحصول عليها:", "Sélectionnez la compétence que vous souhaitez recevoir:"),
                    color=discord.Color.blue()
                )
                return (embed, skill_selection_view)

            return None

        except Exception as e:
            logger.error(f"Error distributing rewards: {e}")

class TutorialCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.connection_retry_attempts = 3
        self.connection_retry_delay = 2
    
    @commands.command(name="tutorial", aliases=["tt","ttl"])
    @command_cooldown(rate=1, per=5.0)
    async def tutorial_command(self, ctx):
        """Display tutorials with progress tracking"""
        try:
            # Get user language
            lang = await _get_lang(ctx.author.id)
            
            # Get user_id from Discord ID
            q = "SELECT user_id FROM user WHERE user_authorid = %s"
            res = await execute_query_with_retry(q, (str(ctx.author.id),))
            
            if not res:
                await ctx.send("User not found in database.")
                return
            
            user_id = res[0]['user_id']
            
            q = "SELECT * FROM tutorials ORDER BY id_tutorial"
            tutorials = await execute_query_with_retry(q, ())
            
            if not tutorials:
                await ctx.send("No tutorials available.")
                return
            
            view = TutorialNavigationView(tutorials, 0, lang, user_id, ctx.author.id)
            embed = await view.create_tutorial_embed()
            
            await ctx.send(embed=embed, view=view)
        
        except Exception as e:
            logger.error(f"Error in tutorial command: {e}")
            await ctx.send("An error occurred while loading tutorials.")
    
    @commands.command(name="tutostart",aliases=["tst","tts"])
    @command_cooldown(rate=1, per=5.0)
    async def tutostart_command(self, ctx, subtutorial_id: int):
        """Start a subtutorial quiz"""
        try:
            # Get user language
            lang = await _get_lang(ctx.author.id)
            
            # Get user_id from Discord ID
            q = "SELECT user_id FROM user WHERE user_authorid = %s"
            res = await execute_query_with_retry(q, (str(ctx.author.id),))
            
            if not res:
                await ctx.send("User not found in database.")
                return
            
            user_id = res[0]['user_id']
            
            q = "SELECT * FROM subtutorial WHERE subtutorial_id = %s"
            res = await execute_query_with_retry(q, (subtutorial_id,))
            
            if not res:
                await ctx.send("Subtutorial not found.")
                return
            
            quiz_view = QuizNavigationView(subtutorial_id, lang, user_id, ctx.author.id, ctx)
            embed = await quiz_view.create_description_embed()
            
            await ctx.send(embed=embed, view=quiz_view)
        
        except Exception as e:
            logger.error(f"Error in tutostart command: {e}")
            await ctx.send("An error occurred while starting the tutorial.")

async def setup(bot):
    await bot.add_cog(TutorialCommands(bot))
