import discord
from discord.ext import commands
import random
from datetime import datetime
import time
import asyncio
import math
from typing import List, Dict, Union
import json
from enum import Enum
import logging
import os
import traceback
from utils.database import execute_query_with_retry, get_metrics
from utils.activity_state import claim_user_activity, cleanup_stale_user_activity, release_user_activity
from utils.db_atomic import advisory_lock_for_context, db_transaction
from utils.discord_safe import safe_defer, safe_edit, safe_response_edit, safe_response_send, safe_send
from utils.error_reporting import compact_card_rows, compact_opponent_rows, send_support_error
from utils.inventory_ops import add_inventory_quantity
import functools
import secrets
# --- multilanguage helpers ---
def _tr(multilanguage: int, en: str, ar: str, fr: str) -> str:
    if multilanguage == 1:
        return ar
    if multilanguage == 3:
        return fr
    return en

async def get_user_multilanguage(discord_id: int) -> int:
    try:
        rows = await execute_query_with_retry("SELECT multilanguage FROM user WHERE user_authorid = %s", (str(discord_id),))
        if rows and 'multilanguage' in rows[0] and rows[0]['multilanguage'] is not None:
            return int(rows[0]['multilanguage'])
    except Exception:
        pass
    return 0  # default EN
# --------------------------------

class CombatCalculator:
    LOG_SCALE = 1000.0      # Every 1,000 DEF = +1 log unit (slow, epic scaling)
    MAX_REDUCTION = 0.9     # Max damage reduction = 90%

    @classmethod
    def calculate_effective_damage(cls, attack_damage: int, defense_stat: int) -> int:
        """New logarithmic defense scaling system."""
        if attack_damage <= 0:
            return 1

        log_val = math.log10(1 + defense_stat / cls.LOG_SCALE)
        reduction = log_val / (1 + log_val)
        reduction = min(reduction, cls.MAX_REDUCTION)

        damage_taken = attack_damage * (1 - reduction)
        return max(1, round(damage_taken))

# Create a command cooldown decorator
def command_cooldown(rate=1, per=3.0, type=commands.BucketType.user):
    """
    A robust cooldown decorator that prevents command spam
    """
    cooldown = commands.Cooldown(rate, per)
    cooldowns = {}
   
    def decorator(func):
        @functools.wraps(func)
        async def wrapper(self, ctx, *args, **kwargs):
            # Get the bucket key based on the cooldown type
            if type == commands.BucketType.user:
                key = ctx.author.id
            elif type == commands.BucketType.channel:
                key = ctx.channel.id
            elif type == commands.BucketType.guild:
                key = ctx.guild.id if ctx.guild else ctx.author.id
            else:
                key = ctx.author.id
               
            # Check if the command is on cooldown
            bucket_key = f"{func.__name__}:{key}"
            if bucket_key in cooldowns:
                bucket = cooldowns[bucket_key]
                retry_after = bucket.update_rate_limit()
                if retry_after:
                    # Command is on cooldown
                    remaining = round(retry_after, 1)
                    ml = await get_user_multilanguage(ctx.author.id)
                    await safe_send(ctx, _tr(ml,
                        f"⏰ Command on cooldown. Please wait {remaining} seconds.",
                        f"⏰ الأمر قيد الانتظار. يرجى الانتظار {remaining} ثانية.",
                        f"⏰ Commande en attente. Veuillez patienter {remaining} secondes."
                    ))
                    return
            else:
                # Create a new cooldown bucket
                cooldowns[bucket_key] = commands.Cooldown(rate, per)
               
            # Execute the command
            return await func(self, ctx, *args, **kwargs)
        return wrapper
    return decorator

intents = discord.Intents.default()
intents.message_content = True
intents.reactions = True

# Constants
DUNGEON_GATE_IMAGE = "https://staticg.sportskeeda.com/editor/2024/03/078be-17113304718546-1920.jpg"
# Constants
E_DUNGEON_IMAGE = "https://i.imgur.com/nLFDb29.jpeg"
C_DUNGEON_IMAGE = "https://i.imgur.com/5WfEsHQ.png"
S_DUNGEON_IMAGE = "https://i.imgur.com/XiqKiv3.png"
A_DUNGEON_IMAGE = "https://i.imgur.com/MFNSo3U.jpeg"
B_DUNGEON_IMAGE = "https://i.imgur.com/xG9ChWZ.jpeg"
D_DUNGEON_IMAGE = "https://i.imgur.com/yUhVAOt.jpeg"

class DungeonRank(Enum):
    E_RANK = ("E-Rank", "🟢", E_DUNGEON_IMAGE, "E", "E-Rank Dungeon", 0x4CAF50, "🗡️", "🏺", "💎", 1, "Beginner hunters start here. Goblins and weak monsters.")
    D_RANK = ("D-Rank", "🟡", D_DUNGEON_IMAGE, "D", "D-Rank Dungeon", 0xFFC107, "⚔️", "🏺🏺", "💎💎", 4, "Slightly stronger foes await the determined.")
    C_RANK = ("C-Rank", "🟠", C_DUNGEON_IMAGE, "C", "C-Rank Dungeon", 0xFF9800, "⚔️", "🏺🏺", "💎💎", 2, "Mid-tier challenges for experienced hunters.")
    B_RANK = ("B-Rank", "🔴", B_DUNGEON_IMAGE, "B", "B-Rank Dungeon", 0xF44336, "🗡️⚔️", "🏺🏺🏺", "💎💎💎", 5, "High-level threats require skill and strategy.")
    A_RANK = ("A-Rank", "🟣", A_DUNGEON_IMAGE, "A", "A-Rank Dungeon", 0x9C27B0, "🗡️⚔️", "🏺🏺🏺", "💎💎💎", 6, "Elite dungeons for the most capable hunters.")
    S_RANK = ("S-Rank", "⚫", S_DUNGEON_IMAGE, "S", "S-Rank Dungeon", 0x000000, "🗡️⚔️", "🏺🏺🏺", "💎💎💎", 3, "Legendary challenges that test the limits of power.")

# Create a Run Away button class
class RunAwayButton(discord.ui.Button):
    def __init__(self, dungeon_commands, ctx, multilanguage: int):
        super().__init__(
            style=discord.ButtonStyle.danger,
            label=_tr(multilanguage, "Run Away", "اهرب", "Fuir"),
            emoji="🏃"
        )
        self.dungeon_commands = dungeon_commands
        self.ctx = ctx
        self.ml = multilanguage
       
    async def callback(self, interaction: discord.Interaction):
        # Only allow the original user to use the button
        if interaction.user.id != self.ctx.author.id:
            await safe_response_send(interaction.response, _tr(self.ml,
                "You cannot use this button.",
                "لا يمكنك استخدام هذا الزر.",
                "Vous ne pouvez pas utiliser ce bouton."
            ), ephemeral=True)
            return
   
        # First acknowledge the interaction to prevent timeout
        await safe_defer(interaction.response)
   
        # Set a flag on the dungeon_commands to indicate this dungeon has been fled
        self.dungeon_commands.fled_dungeons.add(self.ctx.author.id)
   
        # Disable the button to prevent multiple clicks
        self.disabled = True
        try:
            await safe_edit(interaction.message, view=self.view)
        except:
            pass
   
        # Send a new message about fleeing
        await safe_send(self.ctx, _tr(self.ml,
            "🏃‍♂️ **You have fled from the dungeon!**",
            "🏃‍♂️ **لقد هربت من الزنزانة!**",
            "🏃‍♂️ **Vous avez fui le donjon !**"
        ))
   
        # Handle the run away logic
        await self.dungeon_commands.handle_run_away(self.ctx, interaction.message)
        self.dungeon_commands.dungeons_in_progress.discard(self.ctx.author.id)
        # Delete the original dungeon message completely
        try:
            await interaction.message.delete()
        except:
            # If we can't delete, try to at least remove all content
            try:
                await safe_edit(interaction.message, content="", embed=None, view=None, attachments=[])
            except:
                pass

# Create a view that contains the Run Away button
class DungeonView(discord.ui.View):
    def __init__(self, dungeon_commands, ctx, multilanguage: int):
        super().__init__(timeout=None)  # No timeout for dungeon buttons
        self.add_item(RunAwayButton(dungeon_commands, ctx, multilanguage))

class DungeonCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.connection_retry_attempts = 3
        self.connection_retry_delay = 2  # seconds
        self.green_square = "<:gre1295034850298040494__5_remove:1295043081774698538>"
        self.blue_square = "<:blueshield:1385260867280703499>"
        self.red_square = "<:redc:1295036894417125550>"
        self.gray_square = "<:gray:1295036907595759697>"
        self.empty_square = "⬜"
        self.dungeons_in_progress = set()  # Track active dungeons
        self.fled_dungeons = set()  # Track dungeons that have been fled
        self.image_path = os.path.join(os.path.dirname(__file__), '..', 'images')

    async def reset_dungeon_state(self, ctx, user_id=None, *, clear_timer: bool = False):
        """Release dungeon activity flags for the current command without touching rewards."""
        self.dungeons_in_progress.discard(ctx.author.id)
        self.fled_dungeons.discard(ctx.author.id)

        if user_id is None:
            user_id = await self.get_user_id(ctx.author.id)

        if user_id:
            await release_user_activity(user_id, "dungeon_current")
            if clear_timer:
                await execute_query_with_retry(
                    "UPDATE user SET dungeon_timer = NULL WHERE user_id = %s",
                    (user_id,),
                )

    async def consume_dungeon_entry(self, user_id, inventory_id, dungeonkey_id, current_time):
        """Atomically consume the entry key and start cooldown after the battle message exists."""
        async with db_transaction():
            key_rows = await execute_query_with_retry(
                """
                SELECT quantiter
                FROM inventory_dungeonkey
                WHERE inventory_id = %s AND dungeonkey_id = %s
                FOR UPDATE
                """,
                (inventory_id, dungeonkey_id),
            )
            if not key_rows or key_rows[0]["quantiter"] <= 0:
                return False

            await execute_query_with_retry(
                "UPDATE user SET dungeon_timer = %s WHERE user_id = %s",
                (current_time, user_id),
            )
            if key_rows[0]["quantiter"] == 1:
                await execute_query_with_retry(
                    "DELETE FROM inventory_dungeonkey WHERE inventory_id = %s AND dungeonkey_id = %s",
                    (inventory_id, dungeonkey_id),
                )
            else:
                await execute_query_with_retry(
                    """
                    UPDATE inventory_dungeonkey
                    SET quantiter = quantiter - 1
                    WHERE inventory_id = %s AND dungeonkey_id = %s
                    """,
                    (inventory_id, dungeonkey_id),
                )
            return True

    def calculate_dodge_chance(self, attacker_speed, defender_speed):
        """
        Calculate dodge chance based on relative speed difference.
       
        Args:
            attacker_speed (int): Speed stat of the attacker
            defender_speed (int): Speed stat of the defender
       
        Returns:
            float: Percentage chance of dodging between 0-30%
        """
        # Prevent division by zero and ensure positive speeds
        attacker_speed = max(1, attacker_speed)
        defender_speed = max(1, defender_speed)
       
        # Relative speed difference calculation
        speed_ratio = defender_speed / attacker_speed
       
        # Logarithmic scaling to create a balanced dodge chance
        # Ensures dodge chance is always non-negative and caps at 30%
        dodge_chance = min(30, max(0, 30 * (1 - math.log(speed_ratio + 1, 2))))
       
        return dodge_chance

    def calculate_critical_chance(self, attack_stat):
        """
        Calculate critical hit chance based on attack stat.
       
        Args:
            attack_stat (int): Attack statistic of the attacker
       
        Returns:
            float: Percentage chance of critical hit between 5-10%
        """
        return min(10, max(5, attack_stat / 100))

    def handle_dodge_attempt(self, attacker, defender, multilanguage: int = 0):
        """
        Determine if an attack is successfully dodged.
        """
        dodge_chance = self.calculate_dodge_chance(attacker['speed_stat'], defender['speed_stat'])
        dodge_roll = random.random() * 100
       
        if dodge_roll < dodge_chance:
            dodge_message = _tr(multilanguage,
                f"🛡️ {defender['card_name']} swiftly dodged the attack!",
                f"🛡️ {defender['card_name']} تفادى الهجوم بسرعة!",
                f"🛡️ {defender['card_name']} a esquivé l’attaque avec agilité !"
            )
            return True, dodge_message
       
        return False, ""

    def handle_critical_attack(self, base_damage, attack_stat, multilanguage: int = 0):
        """
        Determine if an attack is a critical hit.
        """
        crit_chance = self.calculate_critical_chance(attack_stat)
        crit_roll = random.random() * 100
       
        if crit_roll < crit_chance:
            crit_multiplier = 1.5
            crit_message = _tr(multilanguage,
                f"💥 **CRITICAL HIT!** Damage amplified by {int((crit_multiplier - 1) * 100)}%!",
                f"💥 **ضربة حرجة!** الضرر زاد بنسبة {int((crit_multiplier - 1) * 100)}%!",
                f"💥 **Coup critique !** Dégâts augmentés de {int((crit_multiplier - 1) * 100)}% !"
            )
            return True, int(base_damage * crit_multiplier), crit_message
       
        return False, base_damage, ""

    # Modified perform_combat_round to localize messages
    async def perform_combat_round(self, attacker, defender, is_user_turn, damage_multiplier=1.0, bonus_damage=0, multilanguage: int = 0):
        """
        Comprehensive combat round handling with dodge, critical mechanics, boss special effects, and skills.
        """
        # Handle defensive skills before normal dodge check
        if 'skill_state' in defender:
            # Assuming these return EN strings; you can thread multilanguage into them if needed
            skill_messages, skill_dodge_success, paralysis_success = await self.handle_defensive_skills(defender, attacker, [])
           
            # If paralysis activated, attacker loses turn
            if paralysis_success:
                return skill_messages + [_tr(multilanguage,
                    f"⚡ {attacker['card_name'][:15]} is paralyzed and cannot attack!",
                    f"⚡ {attacker['card_name'][:15]} مُصاب بالشلل ولا يستطيع الهجوم!",
                    f"⚡ {attacker['card_name'][:15]} est paralysé et ne peut pas attaquer !"
                )]
           
            # If skill dodge activated, return dodge message
            if skill_dodge_success:
                return skill_messages
       
        # Handle C-Rank boss enhanced dodge chance
        if hasattr(defender, 'dodge_bonus') and not is_user_turn:
            base_dodge_chance = self.calculate_dodge_chance(attacker['speed_stat'], defender['speed_stat'])
            enhanced_dodge_chance = base_dodge_chance + defender['dodge_bonus']
            dodge_roll = random.random() * 100
           
            if dodge_roll < enhanced_dodge_chance:
                dodge_message = _tr(multilanguage,
                    f"🌪️ {defender['card_name']} swiftly dodged the attack with enhanced agility!",
                    f"🌪️ {defender['card_name']} تفادى الهجوم بخفة مُعزَّزة!",
                    f"🌪️ {defender['card_name']} a esquivé l’attaque avec une agilité accrue !"
                )
                return [dodge_message]
        else:
            # Normal dodge check
            is_dodged, dodge_message = self.handle_dodge_attempt(attacker, defender, multilanguage)
            if is_dodged:
                return [dodge_message]
       
        # Base damage calculation with variance
        base_damage = random.randint(
            int(attacker['attack_stat'] * 0.45),
            int(attacker['attack_stat'] * 0.55)
        )
       
        # Add skill bonus damage
        base_damage += bonus_damage
       
        # Apply damage multiplier for special attacks
        base_damage = int(base_damage * damage_multiplier)
       
        # Critical hit check
        is_critical, final_damage, crit_message = self.handle_critical_attack(base_damage, attacker['attack_stat'], multilanguage)
       
        # Effective damage calculation
        effective_damage = self.calculate_effective_damage(final_damage, defender['defence_stat'])
       
        # Handle shield (both boss shields and skill shields)
        shield_message = ""
        actual_hp_damage = 0

        # Check for skill-based shield first
        if 'shield_health' in defender and defender.get('shield_health', 0) > 0:
            shield_damage = min(effective_damage, defender['shield_health'])
            defender['shield_health'] -= shield_damage
            remaining_damage = effective_damage - shield_damage
           
            if defender['shield_health'] <= 0:
                defender['current_health'] = max(0, defender['current_health'] - remaining_damage)
                actual_hp_damage = remaining_damage
                shield_message = _tr(multilanguage,
                    f"🛡️ Skill shield broken! ({shield_damage} shield + {remaining_damage} HP damage)",
                    f"🛡️ تحطم الدرع المهاري! ({shield_damage} درع + {remaining_damage} ضرر نقاط حياة)",
                    f"🛡️ Bouclier de compétence brisé ! ({shield_damage} bouclier + {remaining_damage} dégâts PV)"
                )
            else:
                actual_hp_damage = 0
                shield_message = _tr(multilanguage,
                    f"🛡️ Skill shield absorbed {shield_damage} damage!",
                    f"🛡️ درع المهارة امتص {shield_damage} ضررًا!",
                    f"🛡️ Le bouclier de compétence a absorbé {shield_damage} dégâts !"
                )
        # Check if this is user attacking S-Rank boss with shield
        elif is_user_turn and 'shield_health' in defender and 'max_shield' in defender:
            if defender['shield_health'] > 0:
                shield_damage = min(effective_damage, defender['shield_health'])
                defender['shield_health'] -= shield_damage
                remaining_damage = effective_damage - shield_damage
               
                if defender['shield_health'] <= 0:
                    defender['current_health'] = max(0, defender['current_health'] - remaining_damage)
                    actual_hp_damage = remaining_damage
                    shield_message = _tr(multilanguage,
                        f"🛡️ Shield broken! ({shield_damage} shield + {remaining_damage} HP damage)",
                        f"🛡️ تحطم الدرع! ({shield_damage} درع + {remaining_damage} ضرر نقاط حياة)",
                        f"🛡️ Bouclier brisé ! ({shield_damage} bouclier + {remaining_damage} dégâts PV)"
                    )
                else:
                    actual_hp_damage = 0
                    shield_message = _tr(multilanguage,
                        f"🛡️ Shield absorbed {shield_damage} damage!",
                        f"🛡️ الدرع امتص {shield_damage} ضررًا!",
                        f"🛡️ Le bouclier a absorbé {shield_damage} dégâts !"
                    )
            else:
                defender['current_health'] = max(0, defender['current_health'] - effective_damage)
                actual_hp_damage = effective_damage
                shield_message = _tr(multilanguage,
                    "🛡️ Shield already broken - direct HP damage!",
                    "🛡️ الدرع مكسور بالفعل - ضرر مباشر لنقاط الحياة!",
                    "🛡️ Bouclier déjà brisé – dégâts directs aux PV !"
                )
        else:
            # Normal damage (no shield or monster attacking user)
            defender['current_health'] = max(0, defender['current_health'] - effective_damage)
            actual_hp_damage = effective_damage
       
        # Prepare attack messages
        messages = []
        if is_critical and crit_message:
            messages.append(crit_message)
       
        if shield_message:
            messages.append(shield_message)
       
        attack_message = self.get_attack_message(
            attacker['card_name'],
            effective_damage if not shield_message else actual_hp_damage,
            base_damage,
            is_user_turn,
            is_critical,
            multilanguage
        )
        messages.append(attack_message)
       
        return messages

    async def check_and_update_referral_status(self, user_id):
        """
        Helper function to check if user is a referee and update their referral status if requirements are met.
       
        Args:
            user_id: The user_id to check
       
        Returns:
            bool: True if status was updated, False otherwise
        """
        try:
            # Check if user is a referee with pending status
            referee_check_query = """
            SELECT r.referrer_id, r.referee_id, r.status, u.roll_total, u.c_dungeon_points
            FROM referrals r
            JOIN user u ON r.referee_id = u.user_id
            WHERE r.referee_id = %s AND r.status = 'pending'
            """
            referee_result = await execute_query_with_retry(referee_check_query, (user_id,))
           
            if not referee_result:
                # User is not a pending referee
                return False
           
            referee_data = referee_result[0]
            roll_total = referee_data['roll_total'] or 0
            mid_dungeon_points = referee_data['c_dungeon_points'] or 0
           
            # Check if requirements are met (500 rolls + 1 mid dungeon)
            if roll_total >= 500 and mid_dungeon_points > 0:
                # Update status to completed
                update_query = """
                UPDATE referrals
                SET status = 'completed', completed_at = %s
                WHERE referee_id = %s AND status = 'pending'
                """
                await execute_query_with_retry(update_query, (datetime.now(), user_id))
               
                print(f"Updated referral status to completed for user_id: {user_id}")
                return True
           
            return False
           
        except Exception as e:
            print(f"Error in check_and_update_referral_status: {e}")
            return False

    async def calculate_referral_bonus(self, user_id, base_amount):
        """
        Calculate referral bonus based on user's referrals
        Returns the modified amount with referral bonus applied
        """
        try:
            # Check if user is in activerefs table
            check_active_query = "SELECT user_id FROM activerefs WHERE user_id = %s"
            is_active_referrer = await execute_query_with_retry(check_active_query, (user_id,))
           
            if not is_active_referrer:
                # User is not an active referrer, return original amount
                return base_amount
               
            # Count total referrals
            count_refs_query = "SELECT COUNT(*) as total FROM referrals WHERE referrer_id = %s"
            refs_result = await execute_query_with_retry(count_refs_query, (user_id,))
            total_refs = refs_result[0]['total'] if refs_result else 0
           
            # Calculate percentage bonus based on referral count
            perc = 0
            if total_refs <= 3:
                # 5% per referral for first 3
                perc = total_refs * 5
            elif total_refs <= 9:
                # 15% base (for first 3) + 2.5% per additional referral
                additional_refs = total_refs - 3
                perc = 15 + (additional_refs * 2.5)
            else:
                # Cap at 30% (9 referrals)
                perc = 30
               
            # Apply percentage bonus
            bonus_amount = int(base_amount * (perc / 100))
            return base_amount + bonus_amount
           
        except Exception as e:
            logging.error(f"Error calculating referral bonus: {e}")
            # In case of error, return original amount
            return base_amount

    def get_attack_message(self, attacker_name, effective_damage, base_damage, is_user_turn, is_critical, multilanguage: int = 0):
        # Limit name length to prevent overflow
        attacker_name = attacker_name[:20] + '...' if len(attacker_name) > 20 else attacker_name
       
        # Emoji and base formatting
        emoji = "⚔️" if is_user_turn else "🛡️"
       
        # Critical hit formatting
        if is_critical:
            return _tr(multilanguage,
                f"💥 **CRITICAL HIT!** {emoji} {attacker_name} → {effective_damage} MASSIVE damage (Base: {base_damage})",
                f"💥 **ضربة حرجة!** {emoji} {attacker_name} → {effective_damage} ضرر هائل (الأساس: {base_damage})",
                f"💥 **Coup critique !** {emoji} {attacker_name} → {effective_damage} dégâts MASSIFS (Base : {base_damage})"
            )
        else:
            return _tr(multilanguage,
                f"{emoji} {attacker_name} → {effective_damage} damage (Base: {base_damage})",
                f"{emoji} {attacker_name} → {effective_damage} ضرر (الأساس: {base_damage})",
                f"{emoji} {attacker_name} → {effective_damage} dégâts (Base : {base_damage})"
            )

    async def get_user_id(self, discord_id):
        query = "SELECT user_id FROM user WHERE user_authorid = %s"
        result = await execute_query_with_retry(query, (str(discord_id),))
        return result[0]['user_id'] if result else None

    async def get_inventory_id(self, user_id):
        query = "SELECT inventory_id FROM inventory WHERE user_id = %s"
        result = await execute_query_with_retry(query, (user_id,))
        return result[0]['inventory_id'] if result else None

    async def get_team_id(self, user_id):
        query = "SELECT team_id FROM team WHERE user_id = %s"
        result = await execute_query_with_retry(query, (user_id,))
        return result[0]['team_id'] if result else None

    async def get_solo_dungeons(self):
        query = "SELECT * FROM dungeon"
        return await execute_query_with_retry(query)

# In DungeonCommands class, inside the dungeon command method

    @commands.command(name="dungeon", aliases=["dg", "dgn"])
    @advisory_lock_for_context("dungeon_start", timeout=2)
    async def dungeon(self, ctx, rank: str = None):
        print("passed through")
        multilanguage = await get_user_multilanguage(ctx.author.id)

        selected_rank_enum = None
        if rank:
            rank = rank.upper().strip()
            if rank in ['A', 'B', 'C', 'D', 'E', 'S']:
                # Find the matching DungeonRank enum
                for dr in DungeonRank:
                    if dr.value[3] == rank:  # value[3] is the rank letter
                        selected_rank_enum = dr
                        break
                if not selected_rank_enum:
                    await safe_send(ctx, _tr(multilanguage,
                        f"❌ Invalid dungeon rank: {rank}. Valid ranks are: E, D, C, B, A, S",
                        f"❌ رتبة زنزانة غير صالحة: {rank}. الرتب الصالحة هي: E, D, C, B, A, S",
                        f"❌ Rang de donjon invalide : {rank}. Rangs valides : E, D, C, B, A, S"
                    ))
                    return
            else:
                await safe_send(ctx, _tr(multilanguage,
                    f"❌ Invalid dungeon rank: {rank}. Valid ranks are: E, D, C, B, A, S",
                    f"❌ رتبة زنزانة غير صالحة: {rank}. الرتب الصالحة هي: E, D, C, B, A, S",
                    f"❌ Rang de donjon invalide : {rank}. Rangs valides : E, D, C, B, A, S"
                ))
                return

        user_id = await self.get_user_id(ctx.author.id)
        if not user_id:
            await safe_send(ctx, _tr(multilanguage,
                "You need to be registered to use this command.",
                "يجب أن تكون مسجلاً لاستخدام هذا الأمر.",
                "Vous devez être inscrit pour utiliser cette commande."
            ))
            return

        stale_dungeon_released = await cleanup_stale_user_activity(user_id, "dungeon_current", "dungeon_timer", 1800)
        if stale_dungeon_released:
            self.dungeons_in_progress.discard(ctx.author.id)

        # First check if user is in the in-memory tracking
        if ctx.author.id in self.dungeons_in_progress:
            await safe_send(ctx, _tr(multilanguage,
                "Please finish your current dungeon exploration before starting a new one.",
                "يرجى إنهاء استكشافك الحالي للزنزانة قبل بدء واحد جديد.",
                "Veuillez terminer votre exploration actuelle du donjon avant d'en commencer une nouvelle."
            ))
            return

        # Then check database state
        check_dungeon_status_query = "SELECT dungeon_current FROM user WHERE user_id = %s"
        dungeon_status_result = await execute_query_with_retry(check_dungeon_status_query, (user_id,))

        if dungeon_status_result[0]['dungeon_current'] == 1:
            await safe_send(ctx, _tr(multilanguage,
                "Please finish your current dungeon exploration before starting a new one.",
                "يرجى إنهاء استكشافك الحالي للزنزانة قبل بدء واحد جديد.",
                "Veuillez terminer votre exploration actuelle du donjon avant d'en commencer une nouvelle."
            ))
            return

        # Add user to in-progress set after the database says the activity is free.
        self.dungeons_in_progress.add(ctx.author.id)

        # Check cooldown
        query = "SELECT dungeon_timer FROM user WHERE user_id = %s"
        result = await execute_query_with_retry(query, (user_id,))
        current_time = datetime.now()
        if result[0]['dungeon_timer'] is not None:
            last_dungeon_time = result[0]['dungeon_timer']
            time_diff = current_time - last_dungeon_time
            cooldown_minutes = 10

            if time_diff.total_seconds() < cooldown_minutes * 60:
                remaining_seconds = (cooldown_minutes * 60) - time_diff.total_seconds()
                remaining_minutes = int(remaining_seconds // 60)
                remaining_secs = int(remaining_seconds % 60)

                # Remove from in-progress before returning
                self.dungeons_in_progress.discard(ctx.author.id)

                if remaining_minutes > 0:
                    await safe_send(ctx, _tr(multilanguage,
                        f"⏳ You must wait {remaining_minutes}m {remaining_secs}s before entering another dungeon!",
                        f"⏳ يجب عليك الانتظار {remaining_minutes}د {remaining_secs}ث قبل دخول زنزانة أخرى!",
                        f"⏳ Vous devez attendre {remaining_minutes}m {remaining_secs}s avant d'entrer dans un autre donjon !"
                    ))
                else:
                    await safe_send(ctx, _tr(multilanguage,
                        f"⏳ You must wait {remaining_secs}s before entering another dungeon!",
                        f"⏳ يجب عليك الانتظار {remaining_secs}ث قبل دخول زنزانة أخرى!",
                        f"⏳ Vous devez attendre {remaining_secs}s avant d'entrer dans un autre donjon !"
                    ))
                return

        dungeon_claimed = await claim_user_activity(user_id, "dungeon_current")
        if not dungeon_claimed:
            self.dungeons_in_progress.discard(ctx.author.id)
            await safe_send(ctx, _tr(multilanguage,
                "Please finish your current dungeon exploration before starting a new one.",
                "يرجى إنهاء استكشافك الحالي للزنزانة قبل بدء واحد جديد.",
                "Veuillez terminer votre exploration actuelle du donjon avant d'en commencer une nouvelle."
            ))
            return

        inventory_id = await self.get_inventory_id(user_id)
        team_id = await self.get_team_id(user_id)

        dungeon_cards_query = """
        SELECT dc.*, c.card_name
        FROM dungeon_cards dc
        JOIN card c ON dc.card_id = c.card_id
        WHERE dc.dungeon_id = %s
        ORDER BY dc.round
        """

        user_cards_query = """
        SELECT c.card_id, c.card_name, c.image, c.card_rank, c.xp_cap, uc.max_leveled,
            uc.power_stat, uc.health_stat, uc.speed_stat, uc.attack_stat, uc.defence_stat,
            uc.current_level, uc.current_xp, uc.id_enhancer, uc.fight_count,
            uc.charm_attack, uc.charm_defence, uc.charm_speed, uc.charm_health,
            uc.attack_count, uc.defence_count, uc.speed_count, uc.health_count,
            uc.skill_id, tc.position
        FROM team_cards tc
        JOIN user_cards uc ON tc.card_id = uc.card_id AND uc.user_id = %s
        JOIN card c ON uc.card_id = c.card_id
        WHERE tc.team_id = %s AND c.type_card = 'Fighter'
        ORDER BY tc.position
        """

        if not inventory_id or not team_id:
            # Remove from in-progress before returning
            self.dungeons_in_progress.discard(ctx.author.id)
            await release_user_activity(user_id, "dungeon_current")
            await safe_send(ctx, _tr(multilanguage,
                "You need to have an inventory and a team to enter a dungeon.",
                "تحتاج إلى امتلاك مخزون وفريق لدخول زنزانة.",
                "Vous devez avoir un inventaire et une équipe pour entrer dans un donjon."
            ))
            return

        all_dungeons_from_db = await self.get_solo_dungeons()
        if not all_dungeons_from_db:
            self.dungeons_in_progress.discard(ctx.author.id)
            await release_user_activity(user_id, "dungeon_current")
            await safe_send(ctx, _tr(multilanguage,
                "No dungeons available at the moment.",
                "لا توجد زنزانات متاحة حاليًا.",
                "Aucun donjon disponible pour le moment."
            ))
            return

        # Reorder dungeons based on DungeonRank enum's desired sequence
        ordered_dungeons = []
        dungeon_map = {d['dungeon_id']: d for d in all_dungeons_from_db}

        for rank_enum_member in DungeonRank:
            dungeon_id_for_rank = rank_enum_member.value[9]
            if dungeon_id_for_rank in dungeon_map:
                ordered_dungeons.append(dungeon_map[dungeon_id_for_rank])
            else:
                logging.warning(f"Dungeon with ID {dungeon_id_for_rank} (for {rank_enum_member.value[0]}) not found in database.")

        dungeons_to_display = ordered_dungeons
        if not dungeons_to_display:
            self.dungeons_in_progress.discard(ctx.author.id)
            await release_user_activity(user_id, "dungeon_current")
            await safe_send(ctx, _tr(multilanguage,
                "No dungeons available after ordering. Please check dungeon configurations.",
                "لا توجد زنزانات متاحة بعد الترتيب. يرجى التحقق من إعدادات الزنزانات.",
                "Aucun donjon disponible après tri. Veuillez vérifier la configuration des donjons."
            ))
            return

        if selected_rank_enum:
            # Find the dungeon data for the selected rank
            selected_dungeon = None
            for dungeon in dungeons_to_display:
                if dungeon['dungeon_id'] == selected_rank_enum.value[9]:
                    selected_dungeon = dungeon
                    break

            if not selected_dungeon:
                self.dungeons_in_progress.discard(ctx.author.id)
                await release_user_activity(user_id, "dungeon_current")
                await safe_send(ctx, _tr(multilanguage,
                    f"❌ {selected_rank_enum.value[0]} dungeon is not available.",
                    f"❌ زنزانة {selected_rank_enum.value[0]} غير متاحة.",
                    f"❌ Le donjon {selected_rank_enum.value[0]} n'est pas disponible."
                ))
                return

            # Directly enter the dungeon (same logic as clicking 🚪)
            try:
                await self.enter_dungeon(ctx, selected_dungeon, selected_rank_enum, user_id, inventory_id, team_id,
                                         dungeon_cards_query, user_cards_query, multilanguage, current_time)
            except Exception as error:
                await self.reset_dungeon_state(ctx, user_id)
                logging.error(f"Error entering selected dungeon: {error}")
                await safe_send(ctx, _tr(multilanguage,
                    "An unexpected error occurred while entering the dungeon. Please try again later.",
                    "حدث خطأ غير متوقع أثناء دخول الزنزانة. يرجى المحاولة لاحقًا.",
                    "Une erreur inattendue s’est produite lors de l’entrée dans le donjon. Veuillez réessayer plus tard."
                ))
            return

        current_page = 0

        async def update_embed():
            dungeon_data_from_db = dungeons_to_display[current_page]

            rank_enum = None
            for dr in DungeonRank:
                if dr.value[9] == dungeon_data_from_db['dungeon_id']:
                    rank_enum = dr
                    break
            if not rank_enum:
                logging.error(f"Could not find DungeonRank enum for dungeon_id: {dungeon_data_from_db['dungeon_id']}")
                return None

            embed = discord.Embed(
                title=_tr(multilanguage,
                    f"🏰 {dungeon_data_from_db['dungeon_name']}",
                    f"🏰 {dungeon_data_from_db['dungeon_name']}",
                    f"🏰 {dungeon_data_from_db['dungeon_name']}"
                ),
                color=discord.Color(rank_enum.value[5])
            )
            embed.set_image(url=rank_enum.value[2])

            embed.add_field(name=_tr(multilanguage, "📊 Rank", "📊 الرتبة", "📊 Rang"), value=f"[{rank_enum.value[3]}]", inline=True)
            embed.add_field(name=_tr(multilanguage, "☠️ Danger", "☠️ الخطر", "☠️ Danger"), value=self.format_danger_level(rank_enum), inline=True)
            embed.add_field(name=_tr(multilanguage, "💰 Rewards", "💰 المكافآت", "💰 Récompenses"), value=self.format_reward_level(rank_enum), inline=True)

            embed.add_field(name=_tr(multilanguage, "🗺️ Environment", "🗺️ البيئة", "🗺️ Environnement"), value=self.generate_environment_description(rank_enum, multilanguage), inline=False)
            embed.add_field(name=_tr(multilanguage, "👹 Monsters", "👹 الوحوش", "👹 Monstres"), value=self.generate_monster_description(rank_enum, multilanguage), inline=False)
            embed.add_field(name=_tr(multilanguage, "💎 Treasures", "💎 الكنوز", "💎 Trésors"), value=self.generate_treasure_description(rank_enum, multilanguage), inline=False)

            embed.set_footer(text=_tr(multilanguage,
                f"Gate {current_page + 1}/{len(dungeons_to_display)}",
                f"البوابة {current_page + 1}/{len(dungeons_to_display)}",
                f"Porte {current_page + 1}/{len(dungeons_to_display)}"
            ))
            return embed

        class DungeonView(discord.ui.View):
            def __init__(self, timeout=60):
                super().__init__(timeout=timeout)
                self.value = None

            @discord.ui.button(label="<", style=discord.ButtonStyle.secondary)
            async def previous_button(self, interaction: discord.Interaction, button: discord.ui.Button):
                if interaction.user.id != ctx.author.id:
                    await safe_response_send(interaction.response, _tr(multilanguage,
                        "This is not your dungeon selection!",
                        "هذا ليس اختيار الزنزانة الخاص بك!",
                        "Ce n'est pas votre sélection de donjon !"
                    ), ephemeral=True)
                    return

                nonlocal current_page
                current_page = (current_page - 1) % len(dungeons_to_display)
                await safe_response_edit(interaction.response, embed=await update_embed(), view=self)

            @discord.ui.button(label=">", style=discord.ButtonStyle.secondary)
            async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
                if interaction.user.id != ctx.author.id:
                    await safe_response_send(interaction.response, _tr(multilanguage,
                        "This is not your dungeon selection!",
                        "هذا ليس اختيار الزنزانة الخاص بك!",
                        "Ce n'est pas votre sélection de donjon !"
                    ), ephemeral=True)
                    return

                nonlocal current_page
                current_page = (current_page + 1) % len(dungeons_to_display)
                await safe_response_edit(interaction.response, embed=await update_embed(), view=self)

            @discord.ui.button(label="🚪 Enter", style=discord.ButtonStyle.primary)
            async def enter_button(self, interaction: discord.Interaction, button: discord.ui.Button):
                if interaction.user.id != ctx.author.id:
                    await safe_response_send(interaction.response, _tr(multilanguage,
                        "This is not your dungeon selection!",
                        "هذا ليس اختيار الزنزانة الخاص بك!",
                        "Ce n'est pas votre sélection de donjon !"
                    ), ephemeral=True)
                    return

                self.value = "enter"
                self.stop()
                await safe_defer(interaction.response)

        view = DungeonView(timeout=60)
        ok, message = await safe_send(ctx, embed=await update_embed(), view=view)
        if not ok or message is None:
            self.dungeons_in_progress.discard(ctx.author.id)
            update_dungeon_current_query = "UPDATE user SET dungeon_current = 0 WHERE user_id = %s"
            await execute_query_with_retry(update_dungeon_current_query, (user_id,))
            return

        await view.wait()

        if view.value != "enter":
            # Timeout occurred
            self.dungeons_in_progress.discard(ctx.author.id)
            update_dungeon_current_query = "UPDATE user SET dungeon_current = 0 WHERE user_id = %s"
            await execute_query_with_retry(update_dungeon_current_query, (user_id,))
            await safe_send(ctx, _tr(multilanguage,
                "The gates have sealed shut. Your hesitation has cost you this opportunity, hunter.",
                "انغلقت البوابات. ترددك كلفك هذه الفرصة أيها الصياد.",
                "Les portes se sont refermées. Votre hésitation vous a coûté cette opportunité, chasseur."
            ))
            return

        # User clicked enter button
        selected_dungeon = dungeons_to_display[current_page]

        # Find the corresponding DungeonRank enum member
        selected_rank_enum = None
        for dr in DungeonRank:
            if dr.value[9] == selected_dungeon['dungeon_id']:
                selected_rank_enum = dr
                break
        if not selected_rank_enum:
            await safe_send(ctx, _tr(multilanguage,
                "Error: Could not find matching dungeon rank. Please contact an administrator.",
                "خطأ: تعذر العثور على رتبة زنزانة مطابقة. يرجى التواصل مع المسؤول.",
                "Erreur : Impossible de trouver le rang de donjon correspondant. Veuillez contacter un administrateur."
            ))
            update_dungeon_current_query = "UPDATE user SET dungeon_current = 0 WHERE user_id = %s"
            await execute_query_with_retry(update_dungeon_current_query, (user_id,))
            self.dungeons_in_progress.discard(ctx.author.id)
            return

        try:
            await self.enter_dungeon(ctx, selected_dungeon, selected_rank_enum, user_id, inventory_id, team_id,
                                     dungeon_cards_query, user_cards_query, multilanguage, current_time)
        except Exception as error:
            await self.reset_dungeon_state(ctx, user_id)
            logging.error(f"Error entering selected dungeon: {error}")
            await safe_send(ctx, _tr(multilanguage,
                "An unexpected error occurred while entering the dungeon. Please try again later.",
                "حدث خطأ غير متوقع أثناء دخول الزنزانة. يرجى المحاولة لاحقًا.",
                "Une erreur inattendue s’est produite lors de l’entrée dans le donjon. Veuillez réessayer plus tard."
            ))

    async def enter_dungeon(self, ctx, selected_dungeon, selected_rank_enum, user_id, inventory_id, team_id,
                           dungeon_cards_query, user_cards_query, multilanguage, current_time):
        """Handle the actual dungeon entry logic after selection"""

        # Check for dungeon key
        key_query = """
        SELECT idk.quantiter
        FROM inventory_dungeonkey idk
        JOIN dungeon d ON idk.dungeonkey_id = d.dungeonkey_id
        WHERE idk.inventory_id = %s AND d.dungeon_id = %s
        """
        key_result = await execute_query_with_retry(key_query, (inventory_id, selected_dungeon['dungeon_id']))
        if not key_result or key_result[0]['quantiter'] <= 0:
            update_dungeon_current_query = "UPDATE user SET dungeon_current = 0 WHERE user_id = %s"
            await execute_query_with_retry(update_dungeon_current_query, (user_id,))
            self.dungeons_in_progress.discard(ctx.author.id)
            await safe_send(ctx, _tr(multilanguage,
                "You don't have the required key for this dungeon.",
                "ليس لديك المفتاح المطلوب لهذه الزنزانة.",
                "Vous n'avez pas la clé requise pour ce donjon."
            ))
            return

        # Check for fighter card
        fighter_query = """
        SELECT COUNT(*) as fighter_count
        FROM team_cards tc
        JOIN user_cards uc ON tc.card_id = uc.card_id
        JOIN card c ON uc.card_id = c.card_id
        WHERE tc.team_id = %s AND c.type_card = 'Fighter'
        """
        fighter_result = await execute_query_with_retry(fighter_query, (team_id,))

        if fighter_result[0]['fighter_count'] == 0:
            update_dungeon_current_query = "UPDATE user SET dungeon_current = 0 WHERE user_id = %s"
            await execute_query_with_retry(update_dungeon_current_query, (user_id,))
            self.dungeons_in_progress.discard(ctx.author.id)
            await safe_send(ctx, _tr(multilanguage,
                "Your team needs at least one Fighter card to enter the dungeon.",
                "يجب أن يحتوي فريقك على بطاقة مقاتل واحدة على الأقل لدخول الزنزانة.",
                "Votre équipe a besoin d'au moins une carte Combattant pour entrer dans un donjon."
            ))
            return

        # Get dungeon cards
        dungeon_cards = await execute_query_with_retry(dungeon_cards_query, (selected_dungeon['dungeon_id'],))
        print(dungeon_cards)
        if not dungeon_cards:
            update_dungeon_current_query = "UPDATE user SET dungeon_current = 0 WHERE user_id = %s"
            await execute_query_with_retry(update_dungeon_current_query, (user_id,))
            self.dungeons_in_progress.discard(ctx.author.id)
            await safe_send(ctx, _tr(multilanguage,
                f"🚫 This dungeon ({selected_dungeon['dungeon_name']}) has no monsters configured! Please contact an administrator or ensure the `dungeon_cards` table is populated for this dungeon.",
                f"🚫 هذه الزنزانة ({selected_dungeon['dungeon_name']}) لا تحتوي على وحوش مُعدّة! يرجى التواصل مع المسؤول أو التأكد من تعبئة جدول `dungeon_cards` لهذه الزنزانة.",
                f"🚫 Ce donjon ({selected_dungeon['dungeon_name']}) n'a pas de monstres configurés ! Contactez un administrateur ou remplissez la table `dungeon_cards` pour ce donjon."
            ))
            return

        # Get user's fighter cards
        user_fighter_cards = await execute_query_with_retry(user_cards_query, (user_id, team_id))
        print(user_fighter_cards)
        if not user_fighter_cards:
            update_dungeon_current_query = "UPDATE user SET dungeon_current = 0 WHERE user_id = %s"
            await execute_query_with_retry(update_dungeon_current_query, (user_id,))
            self.dungeons_in_progress.discard(ctx.author.id)
            await safe_send(ctx, _tr(multilanguage,
                "🚫 You need at least one 'Fighter' card in your team to enter a dungeon. Please add one to your team.",
                "🚫 تحتاج إلى بطاقة «مقاتل» واحدة على الأقل في فريقك لدخول الزنزانة. يرجى إضافة واحدة إلى فريقك.",
                "🚫 Vous avez besoin d'au moins une carte « Combattant » dans votre équipe pour entrer dans un donjon. Veuillez en ajouter une."
            ))
            return

        # Apply charm effects
        user_fighter_cards = await self.apply_charm_effects(user_id, user_fighter_cards)
        # Start the dungeon
        await self.start_dungeon(
            ctx,
            selected_rank_enum,
            user_fighter_cards,
            dungeon_cards,
            selected_dungeon,
            multilanguage,
            user_id=user_id,
            inventory_id=inventory_id,
            current_time=current_time,
            dungeonkey_id=selected_dungeon['dungeonkey_id'],
        )
    def format_dungeon_description(self, difficulty: DungeonRank, multilanguage: int = 0) -> str:
        if multilanguage == 1:
            descriptions = {
                DungeonRank.E_RANK: "اختبار للمبتدئين، مليء بأضعف الوحوش. مناسب للوافدين الجدد.",
                DungeonRank.D_RANK: "درجة أعلى من E، تحديات أقوى وخصوم أذكى.",
                DungeonRank.C_RANK: "اختبار حقيقي للمحنكين حيث تنتظر الوحوش المتوسطة والفخاخ المعقدة.",
                DungeonRank.B_RANK: "للشجعان المستعدين فقط. توقع أعداء أقوياء وقتالًا تكتيكيًا.",
                DungeonRank.A_RANK: "تحدٍ نخبوي يدفع أمهر الصيادين إلى حدودهم.",
                DungeonRank.S_RANK: "قمة الخطر. لا يدخله إلا الأساطير في مواجهة وحوش أسطورية."
            }
        elif multilanguage == 3:
            descriptions = {
                DungeonRank.E_RANK: "Une épreuve pour débutants, remplie des monstres les plus faibles. Idéale pour commencer.",
                DungeonRank.D_RANK: "Un cran au-dessus, avec des défis plus coriaces et des ennemis plus rusés.",
                DungeonRank.C_RANK: "Un véritable test pour les chasseurs aguerris, avec monstres de milieu de gamme et pièges complexes.",
                DungeonRank.B_RANK: "Réservé aux braves et préparés. Attendez-vous à des ennemis redoutables et du jeu tactique.",
                DungeonRank.A_RANK: "Un défi d’élite poussant même les plus doués à leurs limites.",
                DungeonRank.S_RANK: "Le summum du danger. Seuls les légendes osent affronter des bêtes mythiques."
            }
        else:
            descriptions = {
                DungeonRank.E_RANK: "A beginner's trial, filled with the weakest of monsters. Ideal for new adventurers.",
                DungeonRank.D_RANK: "A step up from E-Rank, introducing slightly tougher challenges and more cunning foes.",
                DungeonRank.C_RANK: "A true test for seasoned hunters, where mid-tier monsters and complex traps await.",
                DungeonRank.B_RANK: "Only for the brave and well-prepared. Expect formidable enemies and strategic combat.",
                DungeonRank.A_RANK: "An elite challenge, pushing even the most skilled hunters to their limits against powerful adversaries.",
                DungeonRank.S_RANK: "The pinnacle of danger. Only legends dare to enter, facing mythical beasts and overwhelming power."
            }
        return descriptions[difficulty]

    def format_danger_level(self, rank_enum: DungeonRank) -> str:
        danger = ':star:' * min(rank_enum.value[9], 3) + '⚪' * (3 - min(rank_enum.value[9], 3))
        return danger

    def format_reward_level(self, rank_enum: DungeonRank) -> str:
        rewards = '💎' * min(rank_enum.value[9], 3) + '⚪' * (3 - min(rank_enum.value[9], 3))
        return rewards

    def generate_environment_description(self, rank_enum: DungeonRank, multilanguage: int = 0) -> str:
        if multilanguage == 1:
            environments = {
                DungeonRank.E_RANK: "غابة ضبابية بأشجار عتيقة ومسارات خفية. مصائد الغوبلن منتشرة.",
                DungeonRank.D_RANK: "غابات أكثر ظلامًا مع مخلوقات أخطر تختبئ في الظلال.",
                DungeonRank.C_RANK: "كهوف مظلمة مغطاة بالشقوق وأوتار العناكب العملاقة.",
                DungeonRank.B_RANK: "كهوف جليدية حيث يعض البرد بقدر ما تفعل الوحوش.",
                DungeonRank.A_RANK: "بوابة هائلة محاطة برموز متوهجة تنبض بالطاقة.",
                DungeonRank.S_RANK: "تحدٍ نهائي حيث يبدو أن الواقع نفسه ينحني لقوة سحرية هائلة."
            }
        elif multilanguage == 3:
            environments = {
                DungeonRank.E_RANK: "Forêt brumeuse aux arbres anciens et sentiers cachés. Pièges de gobelins disséminés.",
                DungeonRank.D_RANK: "Bois plus sombres avec des créatures plus dangereuses.",
                DungeonRank.C_RANK: "Grottes obscures couvertes de toiles géantes et d’équipements abandonnés.",
                DungeonRank.B_RANK: "Cavernes glacées où le froid mord autant que les monstres.",
                DungeonRank.A_RANK: "Une porte colossale entourée de runes lumineuses vibrantes d’énergie.",
                DungeonRank.S_RANK: "Défi ultime où la réalité semble plier sous une pression magique immense."
            }
        else:
            environments = {
                DungeonRank.E_RANK: "A misty forest with ancient trees and hidden paths. Goblin traps and crude fortifications dot the landscape.",
                DungeonRank.D_RANK: "Darker woods with more dangerous creatures lurking in the shadows.",
                DungeonRank.C_RANK: "A dark, cavernous space filled with giant webs. Abandoned equipment of unfortunate adventurers litters the ground.",
                DungeonRank.B_RANK: "Frozen caverns where the cold bites as much as the monsters within.",
                DungeonRank.A_RANK: "A colossal gate surrounded by glowing runes and pulsating with energy. The area hums with mana.",
                DungeonRank.S_RANK: "The ultimate challenge where reality itself seems to bend under immense magical pressure."
            }
        return environments[rank_enum]

    def manage_combat_log(self, battle_log, max_entries=5):
        """
        Manages the battle log by keeping only the most recent entries.
        Similar to the attack-button file's battle log system.
        """
        # Keep only the last max_entries
        if len(battle_log) > max_entries:
            return battle_log[-max_entries:]
        return battle_log

    def create_battle_log_field(self, battle_log, current_round, multilanguage: int = 0):
        """
        Creates a single field for the battle log display
        """
        if not battle_log:
            return (_tr(multilanguage, "🗒️ **Battle Log**", "🗒️ **سجل المعركة**", "🗒️ **Journal de combat**"),
                    _tr(multilanguage, "```css\nBattle starting...\n```", "```css\nبدء المعركة...\n```", "```css\nDébut du combat...\n```"),
                    False)
       
        # Get the last 5 entries
        recent_log = self.manage_combat_log(battle_log, 5)
        log_text = '\n'.join(recent_log)
       
        # Ensure it doesn't exceed Discord's field limit
        if len(log_text) > 1000:
            log_text = log_text[-997] + "..."
       
        return (_tr(multilanguage, "🗒️ **Battle Log**", "🗒️ **سجل المعركة**", "🗒️ **Journal de combat**"),
                f"```css\n{log_text}\n```",
                False)

    def get_image_path(self, image_name):
        """Get the full path for an image from the images directory"""
        if not image_name:
            return None
        return os.path.join(self.image_path, image_name)

    async def update_embed_fields(self, embed, dungeon_message, round_log, current_round, multilanguage: int = 0):
        """
        Updates the embed with the managed battle log
        """
        try:
            # Clear existing fields
            embed.clear_fields()
           
            # Create the battle log field
            field_name, field_value, field_inline = self.create_battle_log_field(round_log, current_round, multilanguage)
            embed.add_field(name=field_name, value=field_value, inline=field_inline)
           
            # Update the message
            await safe_edit(dungeon_message, embed=embed)
        except Exception as e:
            logging.error(f"Error updating embed fields: {str(e)}")
            raise

    def generate_monster_description(self, rank_enum: DungeonRank, multilanguage: int = 0) -> str:
        if multilanguage == 1:
            monsters = {
                DungeonRank.E_RANK: "غوبلن، هوغبُلن",
                DungeonRank.D_RANK: "غوبلن أقوى، كشافة الأورك",
                DungeonRank.C_RANK: "حشرات، فرقة السحالي، عناكب عملاقة",
                DungeonRank.B_RANK: "عناصر جليدية، عمالقة الصقيع",
                DungeonRank.A_RANK: "ابن آوى، فرقة الضرب ب، أورك عاليون",
                DungeonRank.S_RANK: "تنانين عتيقة، أمراء شياطين، وحوش أسطورية"
            }
        elif multilanguage == 3:
            monsters = {
                DungeonRank.E_RANK: "Gobelins, Hobgobelins",
                DungeonRank.D_RANK: "Gobelins plus forts, Éclaireurs orcs",
                DungeonRank.C_RANK: "Insectes, Escouade lézard, Araignées géantes",
                DungeonRank.B_RANK: "Élémentaires de glace, Géants du givre",
                DungeonRank.A_RANK: "Chacals, Équipe d’assaut B, Hauts-Orcs",
                DungeonRank.S_RANK: "Dragons antiques, Seigneurs démons, Bêtes mythiques"
            }
        else:
            monsters = {
                DungeonRank.E_RANK: "Goblins, Hobgoblins",
                DungeonRank.D_RANK: "Stronger Goblins, Orc Scouts",
                DungeonRank.C_RANK: "Insects, Lizard Squad, Giant Spiders",
                DungeonRank.B_RANK: "Ice Elementals, Frost Giants",
                DungeonRank.A_RANK: "Jackals, Strike Team B, High-Orcs",
                DungeonRank.S_RANK: "Ancient Dragons, Demon Lords, Mythical Beasts"
            }
        return monsters[rank_enum]

    def generate_treasure_description(self, rank_enum: DungeonRank, multilanguage: int = 0) -> str:
        if multilanguage == 1:
            treasures = {
                DungeonRank.E_RANK: "صناديق الظل بمكافآت أساسية.",
                DungeonRank.D_RANK: "عدة صناديق ظل مع فرصة لصندوق الصياد.",
                DungeonRank.C_RANK: "صناديق الصياد والظل مع فرصة نادرة لصندوق الملك.",
                DungeonRank.B_RANK: "عدة صناديق صياد وظل مع فرصة جيدة لصندوق الملك.",
                DungeonRank.A_RANK: "فرصة عالية لصناديق الملك وفرصة نادرة للكبسولات العشوائية.",
                DungeonRank.S_RANK: "صناديق ملك مضمونة، كبسولات عشوائية، وفرصة أسطورية."
            }
        elif multilanguage == 3:
            treasures = {
                DungeonRank.E_RANK: "Coffres de l’ombre avec récompenses de base.",
                DungeonRank.D_RANK: "Multiples coffres de l’ombre avec chance de coffre du chasseur.",
                DungeonRank.C_RANK: "Coffres de chasseur et de l’ombre avec chance rare de coffre du monarque.",
                DungeonRank.B_RANK: "Multiples coffres du chasseur et de l’ombre avec bonne chance de coffre du monarque.",
                DungeonRank.A_RANK: "Haute chance de coffres du monarque et rare capsule aléatoire.",
                DungeonRank.S_RANK: "Coffres du monarque garantis, capsules aléatoires et chance légendaire."
            }
        else:
            treasures = {
                DungeonRank.E_RANK: "Shadow Chests with basic rewards.",
                DungeonRank.D_RANK: "Multiple Shadow Chests with chance for Hunter Chest.",
                DungeonRank.C_RANK: "Hunter Chests and Shadow Chests with rare Monarch Chest chance.",
                DungeonRank.B_RANK: "Multiple Hunter and Shadow Chests with good Monarch Chest chance.",
                DungeonRank.A_RANK: "High chance for Monarch Chests and rare Random Capsules.",
                DungeonRank.S_RANK: "Guaranteed Monarch Chests, Random Capsules, and legendary jackpot chance."
            }
        return treasures[rank_enum]

    def get_power_suggestion(self, rank_enum: DungeonRank) -> str:
        power_suggestions = {
            DungeonRank.E_RANK: "💪 **Recommended Power:** 500-1,000",
            DungeonRank.D_RANK: "💪 **Recommended Power:** 1,000-2,000",
            DungeonRank.C_RANK: "💪 **Recommended Power:** 2,000-4,000",
            DungeonRank.B_RANK: "💪 **Recommended Power:** 4,000-7,000",
            DungeonRank.A_RANK: "💪 **Recommended Power:** 7,000-12,000",
            DungeonRank.S_RANK: "💪 **Recommended Power:** 12,000+"
        }
        return power_suggestions[rank_enum]

    async def start_dungeon(
        self,
        ctx,
        rank_enum: DungeonRank,
        user_fighter_cards,
        dungeon_cards,
        dungeon_data,
        multilanguage: int = 0,
        *,
        user_id=None,
        inventory_id=None,
        current_time=None,
        dungeonkey_id=None,
    ):
        # Extract card IDs for the fighters that will participate in the dungeon
        user_card_ids = [card['card_id'] for card in user_fighter_cards]

        embed = discord.Embed(
            title=_tr(multilanguage, f"🏰 {dungeon_data['dungeon_name']}", f"🏰 {dungeon_data['dungeon_name']}", f"🏰 {dungeon_data['dungeon_name']}"),
            description=_tr(multilanguage,
                f"{dungeon_data['dungeon_description']}\n\n{rank_enum.value[10]}",
                f"{dungeon_data['dungeon_description']}\n\n{rank_enum.value[10]}",
                f"{dungeon_data['dungeon_description']}\n\n{rank_enum.value[10]}",
            ),
            color=discord.Color(rank_enum.value[5])
        )

        embed.set_image(url=rank_enum.value[2])

        embed.add_field(name=_tr(multilanguage, "📊 Rank", "📊 الرتبة", "📊 Rang"),
                        value=f"\n[{rank_enum.value[3]}]\n", inline=True)

        danger_level = '█' * rank_enum.value[9] + '░' * (6 - rank_enum.value[9])
        embed.add_field(name=_tr(multilanguage, "☠️ Danger Level", "☠️ مستوى الخطر", "☠️ Niveau de danger"),
                        value=f"\n{danger_level}\n", inline=True)

        rewards = '🌟' * min(rank_enum.value[9], 3)
        embed.add_field(name=_tr(multilanguage, "💰 Rewards", "💰 المكافآت", "💰 Récompenses"),
                        value=f"\n{rewards}\n", inline=True)

        embed.add_field(name=_tr(multilanguage, "📜 Your Mission", "📜 مهمتك", "📜 Votre mission"),
                        value=self.generate_mission(multilanguage), inline=False)

        embed.add_field(name=_tr(multilanguage, "🕒 Status", "🕒 الحالة", "🕒 Statut"),
                        value=_tr(multilanguage, "```diff\n+ Dungeon exploration in progress...\n```",
                                "```diff\n+ استكشاف الزنزانة جارٍ...\n```",
                                "```diff\n+ Exploration du donjon en cours...\n```"),
                        inline=False)

        embed.set_footer(text=_tr(multilanguage,
            "'I alone level up.' - Sung Jin-Woo",
            "'أنا وحدي أرتقي.' - سونغ جين-وو",
            "'Je progresse seul.' - Sung Jin-Woo"
        ))

        # Send the initial dungeon message without the Run Away button
        ok, dungeon_message = await safe_send(ctx, embed=embed)
        if not ok or dungeon_message is None:
            await self.reset_dungeon_state(ctx, user_id)
            return

        entry_consumed = await self.consume_dungeon_entry(user_id, inventory_id, dungeonkey_id, current_time)
        if not entry_consumed:
            await self.reset_dungeon_state(ctx, user_id)
            error_embed = discord.Embed(
                title=_tr(multilanguage, "❌ Dungeon Entry Failed", "❌ فشل دخول الزنزانة", "❌ Entrée du donjon échouée"),
                description=_tr(multilanguage,
                    "You no longer have the required key for this dungeon.",
                    "لم يعد لديك المفتاح المطلوب لهذه الزنزانة.",
                    "Vous n’avez plus la clé requise pour ce donjon."
                ),
                color=discord.Color.red()
            )
            await safe_edit(dungeon_message, embed=error_embed, view=None, attachments=[])
            return
        await asyncio.sleep(3)

        # Go!
        await self.dungeon_exploration(ctx, dungeon_message, rank_enum, user_fighter_cards, dungeon_cards, user_card_ids, multilanguage)

    def calculate_poison_chance(self, attacker_power, target_power):
        """Calculate the chance of poison being applied based on power difference"""
        # If attacker is stronger or equal, 100% chance
        print("me: ",attacker_power)
        print("monster: ",target_power)
        if attacker_power >= target_power:
            return 100
        
        # Calculate power difference (only when attacker is weaker)
        power_diff = target_power - attacker_power
        
        # Base chance is 100%
        chance = 100
        
        # For differences up to 100k: -5% per 10k
        if power_diff <= 100000:
            chance -= (power_diff // 10000) * 5
        else:
            # At 100k: -50%
            chance = 50
            
            # Beyond 100k, calculate additional reduction
            excess_diff = power_diff - 100000
            
            if excess_diff <= 25000:  # 100k-125k
                chance = 45
            elif excess_diff <= 50000:  # 125k-150k
                chance = 40
            elif excess_diff <= 75000:  # 150k-175k
                chance = 35
            else:  # 175k-200k+
                chance = 30  # Minimum 30% chance
        print("chance:",chance)
        return max(30, chance)  # Ensure minimum 30% chance
    def truncate_log(self, log_entries: List[str], max_length: int = 1000) -> List[str]:
        """Truncate log entries to prevent Discord embed field length overflow."""
        truncated_log = []
        current_length = 0
        for entry in reversed(log_entries):
            entry_length = len(entry)
            if current_length + entry_length > max_length:
                if not truncated_log or truncated_log[-1] != "...":
                    truncated_log.append(" ")
                break
            truncated_log.insert(0, entry)
            current_length += entry_length
        return truncated_log


    def calculate_effective_damage(self, attack_damage: int, defense_stat: int) -> int:
        k = 100
        effective_damage = attack_damage * (1 - (defense_stat / (defense_stat + k)))
        return round(effective_damage)


    def create_health_bar(self, entity, is_monster=False, damage_taken=None):
        # Ensure health_stat is greater than 0 to avoid division by zero
        health_stat = max(1, entity['health_stat'])
        current_health = max(0, entity['current_health'])
        health_percentage = min(100, (current_health / health_stat) * 100)
        filled_blocks = min(10, max(0, int(health_percentage / 10)))
        empty_blocks = 10 - filled_blocks

        if is_monster:
            filled_square = self.red_square if health_percentage > 0 else self.gray_square
        else:
            filled_square = self.green_square

        health_bar = f"{filled_square * filled_blocks}{self.gray_square * empty_blocks}"
        name = entity.get('card_name', 'Unknown')
        if len(name) > 15:
            name = name[:12] + "..."
        damage_text = f" (-{damage_taken})" if damage_taken else ""
        base_display = f"{name}: {health_bar} **({health_percentage:.1f}%){damage_text}** ({max(0, current_health)}/{health_stat} HP)"
        if 'shield_health' in entity and 'max_shield' in entity:
            shield_display = self.create_shield_bar(entity['shield_health'], entity['max_shield'])
            return f"{base_display}\n{shield_display}"
        return base_display


    def create_shield_bar(self, current_shield, max_shield):
        """Create a shield bar for S-Rank boss using simple text"""
        if max_shield == 0:
            return "🛡️ Shield: ERROR (0/0)"
        shield_percentage = min(100, (current_shield / max_shield) * 100)
        filled_blocks = min(10, max(0, int(shield_percentage / 10)))
        empty_blocks = 10 - filled_blocks
        shield_bar = f"{'█' * filled_blocks}{'░' * empty_blocks}"
        return f"🛡️ Shield: {shield_bar} {current_shield}/{max_shield} ({shield_percentage:.0f}%)"


    async def dungeon_exploration(self, ctx, dungeon_message, difficulty, user_fighter_cards, dungeon_cards, user_card_ids=None, multilanguage: int = 0):
        try:
            logging.info(f"Starting dungeon exploration for user {ctx.author.id} with difficulty {difficulty.value[0]}")
            if ctx.author.id not in self.dungeons_in_progress:
                self.dungeons_in_progress.add(ctx.author.id)

            dungeon_rounds = {}
            for card in dungeon_cards:
                round_num = card['round']
                dungeon_rounds.setdefault(round_num, []).append(card)

            total_rounds = len(dungeon_rounds)
            embed = dungeon_message.embeds[0]
            embed.clear_fields()
            original_user_team = user_fighter_cards.copy()

            # Now add the Run Away button after the initial dungeon info is shown
            view = DungeonView(self, ctx, multilanguage)
            embed.set_footer(text=_tr(multilanguage,
                "Use the Run Away button to escape the dungeon",
                "استخدم زر «اهرب» للهروب من الزنزانة",
                "Utilisez le bouton « Fuir » pour quitter le donjon"
            ))

            try:
                await safe_edit(dungeon_message, embed=embed, view=view)
            except discord.errors.NotFound:
                ok, new_dungeon_message = await safe_send(ctx, embed=embed, view=view)
                if ok and new_dungeon_message is not None:
                    dungeon_message = new_dungeon_message

            evasion_pass_used = False
            rounds_to_play = sorted(list(dungeon_rounds.keys()))
            round_index = 0

            while round_index < len(rounds_to_play):
                current_round = rounds_to_play[round_index]

                if ctx.author.id in self.fled_dungeons:
                    logging.info(f"User {ctx.author.id} has fled the dungeon, stopping exploration")
                    self.fled_dungeons.discard(ctx.author.id)
                    return

                # Reset user team health each round
                user_team = []
                for card in original_user_team:
                    new_card = card.copy()
                    new_card['current_health'] = new_card['health_stat']
                    user_team.append(new_card)

                round_description = _tr(multilanguage,
                    f"Round {current_round} of the {difficulty.value[4]}",
                    f"الجولة {current_round} من {difficulty.value[4]}",
                    f"Manche {current_round} du {difficulty.value[4]}"
                )
                round_log = [round_description]

                # Skills init
                user_id = await self.get_user_id(ctx.author.id)
                self.reset_round_skill_states(user_team)
                skill_init_messages = await self.initialize_round_skills(user_id, user_team, round_log)
                if skill_init_messages:
                    round_log.extend(skill_init_messages)

                # Summons before combat
                active_summons = []
                for card in user_team:
                    if card['current_health'] > 0:
                        summon_messages, summons = await self.handle_summon_skills(card, user_id, round_log)
                        if summon_messages:
                            round_log.extend(summon_messages)
                        if summons:
                            active_summons.extend(summons)
                combat_team = active_summons + [c for c in user_team if c['current_health'] > 0]
                if combat_team:
                    user_team = combat_team

                embed.clear_fields()
                embed.add_field(name=_tr(multilanguage, "⚔️ Round", "⚔️ الجولة", "⚔️ Manche") + f" {current_round}",
                                value=round_description, inline=False)
                try:
                    await safe_edit(dungeon_message, embed=embed)
                except discord.errors.NotFound:
                    return

                await asyncio.sleep(1)
                round_monsters = dungeon_rounds[current_round].copy()
                fight_number = 1

                while round_monsters and user_team:
                    if ctx.author.id in self.fled_dungeons:
                        logging.info(f"User {ctx.author.id} has fled the dungeon, stopping exploration")
                        self.fled_dungeons.discard(ctx.author.id)
                        return

                    current_fight_complete = False
                    monster = round_monsters[0]
                    user_card = user_team[0]
                    if 'current_health' not in monster:
                        monster['current_health'] = monster['health_stat']

                    # S-Rank boss shield special (example)
                    if current_round == 4 and difficulty.value[9] == 3 and monster['card_id'] == 19:
                        if 'shield_health' not in monster:
                            shield_amount = int(monster['health_stat'] * 0.5)
                            monster['shield_health'] = shield_amount
                            monster['max_shield'] = shield_amount
                            round_log.append(_tr(multilanguage,
                                "🛡️ **Kargalgan's shield activates!** A powerful barrier surrounds the boss!",
                                "🛡️ **درع كارغالغان يتفعّل!** حاجز قوي يحيط بالزعيم!",
                                "🛡️ **Le bouclier de Kargalgan s’active !** Une barrière puissante entoure le boss !"
                            ))

                    user_turn_count = 0
                    monster_turn_count = 0

                    if current_round == 4:  # Boss round specials
                        boss_special_messages = await self.apply_boss_special_effects(monster, difficulty.value[9], current_round, round_log)
                        if boss_special_messages:
                            round_log.extend(boss_special_messages)

                    round_log.append(_tr(multilanguage,
                        f"\n**Fight {fight_number}: {user_card['card_name']} vs {monster['card_name']}**",
                        f"\n**المعركة {fight_number}: {user_card['card_name']} ضد {monster['card_name']}**",
                        f"\n**Combat {fight_number} : {user_card['card_name']} vs {monster['card_name']}**"
                    ))

                    try:
                        await self.update_embed_with_health_bars(embed, dungeon_message, round_log, current_round, user_card, monster, multilanguage)
                        files = []
                        card_image_path = self.get_image_path(monster['image'])
                        if card_image_path and os.path.exists(card_image_path):
                            try:
                                card_filename = f"card_{os.path.basename(card_image_path)}"
                                card_file = discord.File(card_image_path, filename=card_filename)
                                files.append(card_file)
                                embed.set_image(url=f"attachment://{card_filename}")
                                await safe_edit(dungeon_message, embed=embed, attachments=files)
                            except discord.errors.NotFound:
                                return
                            except Exception as e:
                                logging.error(f"Error loading card image: {e}")
                                try:
                                    await safe_edit(dungeon_message, embed=embed)
                                except discord.errors.NotFound:
                                    return
                        else:
                            try:
                                await safe_edit(dungeon_message, embed=embed, min_interval=1.0)
                            except discord.errors.NotFound:
                                return
                    except discord.errors.NotFound:
                        return

                    await asyncio.sleep(1)

                    # === Combat loop ===
                    while (not current_fight_complete) and monster['current_health'] > 0 and user_card['current_health'] > 0:
                        if ctx.author.id in self.fled_dungeons:
                            logging.info(f"User {ctx.author.id} has fled the dungeon, stopping exploration")
                            self.fled_dungeons.discard(ctx.author.id)
                            return

                        await asyncio.sleep(0.7)
                        user_turn_count += 1

                        # user turn effects (D drain / B freeze)
                        user_effect_messages, card_defeated_or_skip = await self.handle_user_turn_effects(
                            user_card, monster, difficulty.value[9], current_round, user_turn_count, round_log
                        )
                        if user_effect_messages:
                            round_log.extend(user_effect_messages)
                            try:
                                await self.update_embed_with_health_bars(embed, dungeon_message, round_log, current_round, user_card, monster, multilanguage)
                                await safe_edit(dungeon_message, embed=embed)
                            except discord.errors.NotFound:
                                return
                            await asyncio.sleep(0)

                        if card_defeated_or_skip and user_card['current_health'] <= 0:
                            user_team.pop(0)
                            current_fight_complete = True
                            fight_number += 1
                            break

                        if card_defeated_or_skip and user_card['current_health'] > 0:
                            pass  # skip turn (freeze)
                        else:
                            if user_card['current_health'] > 0:
                                defensive_messages, dodge_success, paralysis_success = await self.handle_defensive_skills(user_card, monster, round_log)
                                if defensive_messages:
                                    round_log.extend(defensive_messages)

                                if 'skill_state' in user_card and user_card['skill_state']['skill_info']['skill_category_id'] == 5:
                                    shield_messages = await self.apply_shield_skill(user_card, user_card['skill_state']['skill_details'], round_log)
                                    if shield_messages:
                                        round_log.extend(shield_messages)

                                skill_messages, bonus_damage = await self.handle_turn_based_skills(user_card, True, round_log)
                                if skill_messages:
                                    round_log.extend(skill_messages)

                                user_attack_messages = await self.perform_combat_round(
                                    user_card, monster, is_user_turn=True, bonus_damage=bonus_damage, multilanguage=multilanguage
                                )
                                round_log.extend(user_attack_messages)

                                if 'skill_state' in user_card and user_card['skill_state']['skill_info']['skill_category_id'] == 6:
                                    # <CHANGE> Calculate poison application chance based on power difference
                                    poison_chance = self.calculate_poison_chance(user_card['power_stat'], monster['power_stat'])
                                    poison_roll = random.randint(1, 100)
                                    
                                    if poison_roll <= poison_chance:
                                        # Poison application successful
                                        if 'skill_state' not in monster:
                                            monster['skill_state'] = {
                                                'poison_active': True,
                                                'current_poison_percent': 2.0
                                            }
                                        else:
                                            if not monster['skill_state'].get('poison_active', False):
                                                monster['skill_state']['poison_active'] = True
                                                monster['skill_state']['current_poison_percent'] = 2.0
                                        
                                        poison_messages = await self.handle_poison_skill(
                                            monster, 
                                            user_card['skill_state']['skill_details'], 
                                            multilanguage
                                        )
                                        if poison_messages:
                                            round_log.extend(poison_messages)
                                    else:
                                        # Poison application failed
                                        round_log.append(_tr(
                                            multilanguage,
                                            f"☠️ **Poison failed to apply!** ({poison_chance}% chance, rolled {poison_roll})",
                                            f"☠️ **فشل تطبيق السم!** ({poison_chance}% فرصة، تم الحصول على {poison_roll})",
                                            f"☠️ **Le poison n'a pas pu être appliqué !** ({poison_chance}% de chance, obtenu {poison_roll})"
                                        ))

                                threshold_messages = self.handle_boss_health_threshold(monster, difficulty.value[9], current_round, round_log)
                                if threshold_messages:
                                    round_log.extend(threshold_messages)

                        try:
                            await self.update_embed_with_health_bars(embed, dungeon_message, round_log, current_round, user_card, monster, multilanguage)
                            await safe_edit(dungeon_message, embed=embed, min_interval=1.0)
                        except discord.errors.NotFound:
                            return

                        await asyncio.sleep(0.7)

                        if monster['current_health'] <= 0:
                            round_monsters.pop(0)
                            current_fight_complete = True
                            if user_card['current_health'] <= 0:
                                user_team.pop(0)
                            fight_number += 1
                            break

                        if monster['current_health'] > 0:
                            monster_turn_count += 1
                            boss_turn_messages, damage_multiplier = await self.handle_boss_turn_effects(
                                monster, difficulty.value[9], current_round, monster_turn_count, round_log
                            )
                            if boss_turn_messages:
                                round_log.extend(boss_turn_messages)
                                try:
                                    await self.update_embed_with_health_bars(embed, dungeon_message, round_log, current_round, user_card, monster, multilanguage)
                                    await safe_edit(dungeon_message, embed=embed, min_interval=1.0)
                                except discord.errors.NotFound:
                                    return
                                await asyncio.sleep(1)

                            if ('skill_state' in monster and
                                'skill_info' in monster['skill_state'] and
                                monster['skill_state']['skill_info']['skill_category_id'] == 6):
                                # <CHANGE> Calculate poison application chance based on power difference
                                poison_chance = self.calculate_poison_chance(monster['power_stat'], user_card['power_stat'])
                                poison_roll = random.randint(1, 100)
                                
                                if poison_roll <= poison_chance:
                                    # Poison application successful
                                    if 'skill_state' not in user_card:
                                        user_card['skill_state'] = {
                                            'poison_active': True,
                                            'current_poison_percent': 2.0
                                        }
                                    else:
                                        if not user_card['skill_state'].get('poison_active', False):
                                            user_card['skill_state']['poison_active'] = True
                                            user_card['skill_state']['current_poison_percent'] = 2.0
                                    
                                    poison_messages = await self.handle_poison_skill(
                                        user_card, 
                                        monster['skill_state']['skill_details'], 
                                        multilanguage
                                    )
                                    if poison_messages:
                                        round_log.extend(poison_messages)
                                else:
                                    # Poison application failed
                                    round_log.append(_tr(
                                        multilanguage,
                                        f"☠️ **Poison failed to apply!** ({poison_chance}% chance, rolled {poison_roll})",
                                        f"☠️ **فشل تطبيق السم!** ({poison_chance}% فرصة، تم الحصول على {poison_roll})",
                                        f"☠️ **Le poison n'a pas pu être appliqué !** ({poison_chance}% de chance, obtenu {poison_roll})"
                                    ))

                            defensive_messages, dodge_success, paralysis_success = await self.handle_defensive_skills(monster, user_card, round_log)
                            if defensive_messages:
                                round_log.extend(defensive_messages)

                            if not paralysis_success and not dodge_success:
                                monster_attack_messages = await self.perform_combat_round(
                                    monster, user_card, is_user_turn=False, damage_multiplier=damage_multiplier, multilanguage=multilanguage
                                )
                                round_log.extend(monster_attack_messages)

                            threshold_messages = self.handle_boss_health_threshold(monster, difficulty.value[9], current_round, round_log)
                            if threshold_messages:
                                round_log.extend(threshold_messages)

                            try:
                                await self.update_embed_with_health_bars(embed, dungeon_message, round_log, current_round, user_card, monster, multilanguage)
                                await safe_edit(dungeon_message, embed=embed, min_interval=1.0)
                            except discord.errors.NotFound:
                                return

                            await asyncio.sleep(1)

                            if user_card['current_health'] <= 0:
                                user_team.pop(0)
                                current_fight_complete = True
                                fight_number += 1
                                break

                # after fights in round
                if not round_monsters and current_round < total_rounds:
                    if ctx.author.id in self.fled_dungeons:
                        logging.info(f"User {ctx.author.id} has fled the dungeon, stopping exploration")
                        self.fled_dungeons.discard(ctx.author.id)
                        return

                    if current_round == 1 and not evasion_pass_used:
                        should_skip = await self.handle_dungeon_skip_check(ctx, current_round)
                        if should_skip:
                            evasion_pass_used = True
                            round_log.append(_tr(multilanguage,
                                "⚡ **Evasion Pass activated!** Skipping directly to the boss round!",
                                "⚡ **تم تفعيل تصريح المراوغة!** الانتقال مباشرة إلى جولة الزعيم!",
                                "⚡ **Passe d’évasion activé !** Saut direct au boss !"
                            ))
                            embed.clear_fields()
                            embed.add_field(
                                name=_tr(multilanguage, "⚡ Evasion Pass Activated", "⚡ تم تفعيل تصريح المراوغة", "⚡ Passe d’évasion activé"),
                                value=_tr(multilanguage, "Skipping directly to the boss battle...", "الانتقال مباشرة إلى معركة الزعيم...", "Passage direct au combat de boss..."),
                                inline=False
                            )
                            try:
                                await safe_edit(dungeon_message, embed=embed, attachments=[])
                            except discord.errors.NotFound:
                                return
                            await asyncio.sleep(2)
                            rounds_to_play = [4]
                            round_index = -1
                            round_index += 1
                            continue

                    embed.clear_fields()
                    embed.add_field(
                        name=_tr(multilanguage, f"🚪 Round {current_round} Complete", f"🚪 اكتملت الجولة {current_round}", f"🚪 Manche {current_round} terminée"),
                        value=_tr(multilanguage, f"Moving to Round {current_round + 1}...", f"الانتقال إلى الجولة {current_round + 1}...", f"Passage à la manche {current_round + 1}..."),
                        inline=False
                    )
                    embed.set_image(url=difficulty.value[2])
                    try:
                        await safe_edit(dungeon_message, embed=embed, attachments=[])
                    except discord.errors.NotFound:
                        return
                    await asyncio.sleep(2)
                    embed.clear_fields()

                elif not round_monsters and current_round == total_rounds:
                    # disable button
                    try:
                        if hasattr(dungeon_message, 'view') and dungeon_message.view:
                            for item in dungeon_message.view.children:
                                item.disabled = True
                            await safe_edit(dungeon_message, view=dungeon_message.view)
                    except Exception as e:
                        logging.error(f"Error disabling Run Away button: {str(e)}")

                    await self.handle_dungeon_victory(ctx, dungeon_message, embed, difficulty, user_card_ids, multilanguage)
                    return

                elif not user_team:
                    embed.set_image(url=difficulty.value[2])
                    try:
                        if hasattr(dungeon_message, 'view') and dungeon_message.view:
                            for item in dungeon_message.view.children:
                                item.disabled = True
                            await safe_edit(dungeon_message, view=dungeon_message.view)
                    except Exception as e:
                        logging.error(f"Error disabling Run Away button: {str(e)}")

                    await self.handle_dungeon_defeat(ctx, dungeon_message, embed, difficulty, multilanguage)
                    try:
                        await safe_edit(dungeon_message, embed=embed, attachments=[])
                    except discord.errors.NotFound:
                        pass
                    return

                round_index += 1

            try:
                await self.reset_dungeon_state(ctx)
            except Exception as cleanup_error:
                logging.error(f"Error during final cleanup: {str(cleanup_error)}")

        except Exception as e:
            logging.error(f"Critical error in dungeon exploration: {str(e)}")
            logging.error(traceback.format_exc())
            try:
                user_id = await self.get_user_id(ctx.author.id)
                await self.reset_dungeon_state(ctx, user_id, clear_timer=True)
            except Exception as reset_error:
                logging.error(f"Failed to reset dungeon state: {str(reset_error)}")

            try:
                await send_support_error(
                    ctx.bot,
                    1291676421109973043,
                    "Dungeon system",
                    e,
                    ctx=ctx,
                    extra={
                        "difficulty": difficulty.value[0] if difficulty else None,
                        "user_cards": compact_card_rows(user_fighter_cards),
                        "opponents": compact_opponent_rows(dungeon_cards),
                    },
                )
                await safe_send(ctx, _tr(multilanguage,
                    "An unexpected error occurred. Our support team has been notified and will investigate the issue.",
                    "حدث خطأ غير متوقع. تم إخطار فريق الدعم لدينا وسيتحقق من المشكلة.",
                    "Une erreur inattendue s’est produite. Notre équipe de support a été informée et enquêtera."
                ))
            except Exception as send_error:
                print(f"Failed to send error to support channel: {str(send_error)}")
                await safe_send(ctx, _tr(multilanguage,
                    "An unexpected error occurred. Please try again later.",
                    "حدث خطأ غير متوقع. يرجى المحاولة لاحقًا.",
                    "Une erreur inattendue s’est produite. Veuillez réessayer plus tard."
                ))


    async def update_embed_with_health_bars(self, embed, dungeon_message, round_log, current_round, user_card, monster, multilanguage: int = 0):
        """Updates the embed with the managed battle log and current health bars separately"""
        try:
            embed.clear_fields()
            field_name, field_value, field_inline = self.create_battle_log_field(round_log, current_round, multilanguage)
            embed.add_field(name=field_name, value=field_value, inline=field_inline)

            health_status = (
                f"**{user_card['card_name'][:15]}**\n{self.create_health_bar(user_card, is_monster=False)}\n\n"
                f"**{monster['card_name'][:15]}**\n{self.create_health_bar(monster, is_monster=True)}"
            )
            embed.add_field(
                name=_tr(multilanguage, "⚔️ **Combat Status**", "⚔️ **حالة القتال**", "⚔️ **État du combat**"),
                value=health_status,
                inline=False
            )
        except Exception as e:
            logging.error(f"Error updating embed with health bars: {str(e)}")
            raise

    async def process_dungeon_victory_rewards(self, ctx, user_id, inventory_id, difficulty, user_card_ids=None, multilanguage: int = 0):
        """Commit all dungeon victory rewards together; Discord notifications are sent after commit."""
        async with db_transaction():
            state_rows = await execute_query_with_retry(
                "SELECT dungeon_current FROM user WHERE user_id = %s FOR UPDATE",
                (user_id,),
            )
            if not state_rows or state_rows[0].get("dungeon_current") != 1:
                return None

            quest_message = await self.check_and_update_quest(user_id, multilanguage)
            reward_result = await self.treasure_room(
                ctx,
                inventory_id,
                difficulty.value[9],
                f"{difficulty.value[0].lower()} gate",
                user_card_ids,
                multilanguage,
                in_transaction=True,
            )
            rare_weapon = await self.handle_rare_weapon_drop(
                ctx,
                difficulty.value[9],
                user_id=user_id,
                inventory_id=inventory_id,
                notify=False,
            )
            await execute_query_with_retry(
                "UPDATE user SET dungeon_current = 0 WHERE user_id = %s",
                (user_id,),
            )

        return {
            "quest_message": quest_message,
            "reward_result": reward_result,
            "rare_weapon": rare_weapon,
        }

    async def send_dungeon_reward_notifications(self, ctx, reward_payload, multilanguage: int = 0):
        """Display committed dungeon rewards without mutating reward state."""
        if not reward_payload:
            return

        quest_message = reward_payload.get("quest_message")
        if quest_message:
            await safe_send(ctx, quest_message)

        reward_result = reward_payload.get("reward_result")
        if reward_result and reward_result.get('guild_contribution'):
            await safe_send(ctx, f"Your guild received a contribution of {reward_result['guild_contribution']} <:won:1304034228966658048>.")
        if reward_result:
            await self.send_rewards_embed(
                ctx,
                reward_result['xp_rewards'],
                reward_result['total_xp_gained'],
                reward_result['total_cards_leveled'],
                reward_result['money_reward'],
                reward_result['gamble_reward'],
                reward_result['rewards'],
                reward_result['orb_reward'],
                multilanguage
            )

        rare_weapon = reward_payload.get("rare_weapon")
        if rare_weapon:
            embed = discord.Embed(
                title="✨ Rare Weapon Drop!",
                description=f"You obtained a rare weapon: **{rare_weapon['weapon_name']}** ({rare_weapon['weapon_rarity']})!",
                color=0xFFD700
            )
            await safe_send(ctx, embed=embed)


    async def handle_dungeon_victory(self, ctx, dungeon_message, embed, difficulty, user_card_ids=None, multilanguage: int = 0):
        try:
            user_id = await self.get_user_id(ctx.author.id)

            embed.clear_fields()
            embed.add_field(name=_tr(multilanguage, "🎉 Dungeon Cleared!", "🎉 تم إنهاء الزنزانة!", "🎉 Donjon nettoyé !"),
                            value=_tr(multilanguage,
                                    "Congratulations! You've defeated the boss and cleared the dungeon!",
                                    "تهانينا! لقد هزمت الزعيم وأنهيت الزنزانة!",
                                    "Félicitations ! Vous avez vaincu le boss et terminé le donjon !"),
                            inline=False)
            embed.set_image(url=difficulty.value[2])

            try:
                await safe_edit(dungeon_message, embed=embed, view=None, attachments=[])
            except discord.errors.NotFound:
                ok, dungeon_message = await safe_send(ctx, embed=embed)
            except Exception as edit_error:
                logging.error(f"Error editing dungeon message: {str(edit_error)}")
                try:
                    ok, dungeon_message = await safe_send(ctx, embed=embed)
                except:
                    pass

            inventory_id = await self.get_inventory_id(user_id)
            reward_payload = await self.process_dungeon_victory_rewards(
                ctx,
                user_id,
                inventory_id,
                difficulty,
                user_card_ids,
                multilanguage
            )
            self.dungeons_in_progress.discard(ctx.author.id)
            self.fled_dungeons.discard(ctx.author.id)
            await self.send_dungeon_reward_notifications(ctx, reward_payload, multilanguage)
        except Exception as e:
            logging.error(f"Error handling dungeon rewards: {str(e)}")
            traceback.print_exc()
            try:
                if user_id:
                    await self.reset_dungeon_state(ctx, user_id)
            except:
                pass
            await safe_send(ctx, _tr(multilanguage,
                "There was an error processing your dungeon rewards. Please contact an administrator.",
                "حدث خطأ أثناء معالجة مكافآت الزنزانة. يرجى التواصل مع المسؤول.",
                "Une erreur est survenue lors du traitement de vos récompenses. Veuillez contacter un administrateur."
            ))


    async def handle_dungeon_defeat(self, ctx, dungeon_message, embed, difficulty, multilanguage: int = 0):
        try:
            user_id = await self.get_user_id(ctx.author.id)
            await self.reset_dungeon_state(ctx, user_id)

            embed.clear_fields()
            embed.add_field(
                name=_tr(multilanguage, "❌ Dungeon Failed", "❌ فشل الزنزانة", "❌ Échec du donjon"),
                value=_tr(multilanguage,
                    f"{ctx.author.mention} Your team has been defeated. Better luck next time!",
                    f"{ctx.author.mention} لقد هُزِم فريقك. حظًا أوفر في المرة القادمة!",
                    f"{ctx.author.mention} Votre équipe a été vaincue. Bonne chance la prochaine fois !"),
                inline=False
            )

            money_loss = self.calculate_money_loss(difficulty)
            await self.update_user_currency(user_id, -money_loss, 1, 1)
            embed.add_field(
                name=_tr(multilanguage, "💸 Currency Lost", "💸 العملات المفقودة", "💸 Monnaie perdue"),
                value=_tr(multilanguage,
                        f"You lost {money_loss} <:won:1304034228966658048> for failing the dungeon.",
                        f"لقد خسرت {money_loss} <:won:1304034228966658048> بسبب فشل الزنزانة.",
                        f"Vous avez perdu {money_loss} <:won:1304034228966658048> pour avoir échoué le donjon."),
                inline=False
            )

            embed.set_image(url=difficulty.value[2])
            try:
                await safe_edit(dungeon_message, embed=embed, view=None, attachments=[])
            except discord.errors.NotFound:
                await safe_send(ctx, embed=embed)
            except Exception as edit_error:
                logging.error(f"Error editing dungeon message: {str(edit_error)}")
                try:
                    await safe_send(ctx, embed=embed)
                except:
                    pass

        except Exception as e:
            logging.error(f"Error handling dungeon failure: {str(e)}")
            traceback.print_exc()
            try:
                if user_id:
                    await self.reset_dungeon_state(ctx, user_id)
            except:
                pass
            await safe_send(ctx, _tr(multilanguage,
                "There was an error processing your dungeon defeat. Please contact an administrator.",
                "حدث خطأ أثناء معالجة هزيمتك في الزنزانة. يرجى التواصل مع المسؤول.",
                "Une erreur est survenue lors du traitement de votre défaite. Veuillez contacter un administrateur."
            ))


    async def handle_run_away(self, ctx, dungeon_message, multilanguage: int = 0):
        try:
            user_id = await self.get_user_id(ctx.author.id)
            inventory_id = await self.get_inventory_id(user_id)
            check_crystal_query = """
            SELECT quantiter FROM inventory_product
            WHERE inventory_id = %s AND product_id = 13
            """
            crystal_result = await execute_query_with_retry(check_crystal_query, (inventory_id,))

            if not crystal_result or crystal_result[0]['quantiter'] <= 0:
                await safe_send(ctx, _tr(multilanguage,
                    "You don't have a return crystal to escape the dungeon.",
                    "ليس لديك بلورة عودة للهروب من الزنزانة.",
                    "Vous n’avez pas de cristal de retour pour quitter le donjon."
                ))
                await execute_query_with_retry("UPDATE user SET dungeon_current = 0 WHERE user_id = %s", (user_id,))
                self.dungeons_in_progress.discard(ctx.author.id)
                return False

            if crystal_result[0]['quantiter'] == 1:
                await execute_query_with_retry(
                    "DELETE FROM inventory_product WHERE inventory_id = %s AND product_id = 13",
                    (inventory_id,)
                )
            else:
                await execute_query_with_retry(
                    "UPDATE inventory_product SET quantiter = quantiter - 1 WHERE inventory_id = %s AND product_id = 13",
                    (inventory_id,)
                )

            await execute_query_with_retry("UPDATE user SET dungeon_current = 0 WHERE user_id = %s", (user_id,))
            self.dungeons_in_progress.discard(ctx.author.id)

            await safe_send(ctx, _tr(multilanguage,
                "You used a return crystal to escape the dungeon. Your adventure has ended, but you'll still need to wait for the cooldown before entering another dungeon.",
                "لقد استخدمت بلورة العودة للهرب من الزنزانة. انتهت مغامرتك، ولكن ستحتاج للانتظار حتى انتهاء فترة التهدئة قبل دخول زنزانة أخرى.",
                "Vous avez utilisé un cristal de retour pour quitter le donjon. Votre aventure est terminée, mais vous devrez attendre le délai de recharge avant d’entrer dans un autre donjon."
            ))
            return True
        except Exception as e:
            logging.error(f"Error handling run away: {str(e)}")
            traceback.print_exc()
            try:
                if user_id:
                    await execute_query_with_retry("UPDATE user SET dungeon_current = 0 WHERE user_id = %s", (user_id,))
                    self.dungeons_in_progress.discard(ctx.author.id)
            except:
                pass
            await safe_send(ctx, _tr(multilanguage,
                "There was an error processing your escape. Please contact an administrator.",
                "حدث خطأ أثناء معالجة هروبك. يرجى التواصل مع المسؤول.",
                "Une erreur est survenue lors du traitement de votre fuite. Veuillez contacter un administrateur."
            ))
            return False


    def calculate_money_loss(self, rank_enum):
        # rank_enum is the enum (difficulty); values unchanged
        loss_amounts = {
            DungeonRank.E_RANK: 100,
            DungeonRank.D_RANK: 200,
            DungeonRank.C_RANK: 500,
            DungeonRank.B_RANK: 700,
            DungeonRank.A_RANK: 1000,
            DungeonRank.S_RANK: 1500
        }
        return loss_amounts.get(rank_enum, 100)
    async def check_premium_status(self, user_id):
        """Helper function to check and update premium status"""
        try:
            # Get premium info
            premium_query = """
            SELECT prenium, prenium_date, roll_cap
            FROM user
            WHERE user_id = %s
            """
            result = await execute_query_with_retry(premium_query, (user_id,))
            if not result:
                return 0, False  # No premium

            premium = result[0]['prenium']
            premium_date = result[0]['prenium_date']
            roll_cap = result[0]['roll_cap']

            if premium == 0:
                return 0, False  # No premium

            if premium_date:
                current_date = datetime.now()
                days_diff = (current_date - premium_date).days
               
                if days_diff > 30:
                    # Reset premium and adjust roll_cap based on premium level
                    roll_cap_reduction = min(premium, 3)  # Reduce by premium level (max 3)
                    update_query = """
                    UPDATE user
                    SET prenium = 0,
                        prenium_date = NULL,
                        roll_cap = roll_cap - %s
                    WHERE user_id = %s
                    """
                    await execute_query_with_retry(update_query, (roll_cap_reduction, user_id))
                    return 0, False  # Premium expired
               
                return premium, True  # Active premium
           
            return 0, False  # No premium date set
        except Exception as e:
            logging.error(f"Error checking premium status: {e}")
            return 0, False


    async def check_and_update_quest(self, user_id, multilanguage: int = 0):
        # Check if the user has the "Conquer 1 dungeon" quest
        check_quest_query = """
        SELECT uq.userquests_id, uq.current_count, uq.completion_status, q.required_count, q.quest_id
        FROM user_quests uq
        JOIN quest q ON uq.quest_id = q.quest_id
        WHERE uq.user_id = %s AND q.quest_name = 'Conquer 1 dungeon'
        """
        quest_result = await execute_query_with_retry(check_quest_query, (user_id,))
        if quest_result:
            quest = quest_result[0]
            if quest['completion_status'] == 0:
                new_count = quest['current_count'] + 1
                completion_status = 1 if new_count >= quest['required_count'] else 0
                await execute_query_with_retry(
                    "UPDATE user_quests SET current_count = %s, completion_status = %s WHERE userquests_id = %s",
                    (new_count, completion_status, quest['userquests_id'])
                )

                if completion_status == 1:
                    inventory_id = await self.get_inventory_id(user_id)
                    await add_inventory_quantity("inventory_chest", inventory_id, 1, 1)
                    return _tr(multilanguage,
                        "Quest 'Conquer 1 dungeon' completed! You have received a reward.",
                        "تم إكمال مهمة 'اقهر زنزانة واحدة'! لقد حصلت على مكافأة.",
                        "Quête « Vaincre 1 donjon » terminée ! Vous avez reçu une récompense."
                    )
                else:
                    return _tr(multilanguage,
                        f"Quest 'Conquer 1 dungeon' progress: {new_count}/{quest['required_count']}",
                        f"تقدم مهمة 'اقهر زنزانة واحدة': {new_count}/{quest['required_count']}",
                        f"Progression de la quête « Vaincre 1 donjon » : {new_count}/{quest['required_count']}"
                    )
        return None


    def generate_round_description(self, round_number, multilanguage: int = 0):
        if multilanguage == 1:
            descriptions = [
                "الهواء يزداد كثافة بينما تتوغل أعمق في الزنزانة.",
                "ظلال ترقص على الجدران، تلمح إلى الأخطار القادمة.",
                "أصداء خطواتك تغمرها أصوات بعيدة مقلقة.",
                "قشعريرة تسري في جسدك بينما تستعد للتحدي التالي.",
                "حتى الحجارة تحت قدميك تنبض بطاقة مظلمة قديمة."
            ]
        elif multilanguage == 3:
            descriptions = [
                "L’air devient plus lourd à mesure que vous progressez dans le donjon.",
                "Des ombres dansent sur les murs, annonçant les dangers à venir.",
                "L’écho de vos pas est couvert par de sinistres bruits lointains.",
                "Un frisson vous parcourt tandis que vous vous préparez au prochain défi.",
                "Les pierres sous vos pieds semblent pulser d’une ancienne énergie sombre."
            ]
        else:
            descriptions = [
                "The air grows thicker as you delve deeper into the dungeon.",
                "Shadows dance on the walls, hinting at the dangers that lie ahead.",
                "The echoes of your footsteps are drowned out by distant, ominous sounds.",
                "You feel a chill run down your spine as you prepare for the next challenge.",
                "The very stones beneath your feet seem to pulse with an ancient, dark energy."
            ]
        return random.choice(descriptions)


    def generate_dungeon_name(self, difficulty: DungeonRank) -> str:
        return f"{difficulty.value[0]} Dungeon of Shadows"


    def generate_dungeon_description(self, difficulty: DungeonRank) -> str:
        return "The shadows dance around you as you step into the unknown. Every sound makes you tense, every step feels heavier than the last."


    def generate_mission(self, multilanguage: int = 0) -> str:
        return _tr(multilanguage,
            "Your objective is to complete the mission and take down the boss. Only by succeeding will you claim the rewards awaiting you.",
            "هدفك هو إكمال المهمة والقضاء على الزعيم. لن تنال المكافآت بانتظارك إلا بالنجاح.",
            "Votre objectif est de mener la mission à bien et d’abattre le boss. Ce n’est qu’en réussissant que vous obtiendrez les récompenses."
        )


    def generate_stage_description(self, stage: str, multilanguage: int = 0) -> str:
        return _tr(multilanguage,
            f"You've arrived at the {stage}. Prepare yourself for what's to come.",
            f"لقد وصلت إلى {stage}. استعد لما هو قادم.",
            f"Vous êtes arrivé à {stage}. Préparez-vous pour la suite."
        )


    async def treasure_room(self, ctx, inventory_id, dungeon_rank, dungeon_name, user_card_ids=None, multilanguage:int =0, in_transaction: bool = False):
        if not in_transaction:
            async with db_transaction():
                reward_result = await self.treasure_room(
                    ctx,
                    inventory_id,
                    dungeon_rank,
                    dungeon_name,
                    user_card_ids=user_card_ids,
                    multilanguage=multilanguage,
                    in_transaction=True
                )
            if reward_result and reward_result.get('guild_contribution'):
                await safe_send(ctx, f"Your guild received a contribution of {reward_result['guild_contribution']} <:won:1304034228966658048>.")
            if reward_result:
                await self.send_rewards_embed(
                    ctx,
                    reward_result['xp_rewards'],
                    reward_result['total_xp_gained'],
                    reward_result['total_cards_leveled'],
                    reward_result['money_reward'],
                    reward_result['gamble_reward'],
                    reward_result['rewards'],
                    reward_result['orb_reward'],
                    multilanguage
                )
            return reward_result['rewards'] if reward_result else None

        # Get chest rewards based on dungeon rank
        rewards = {}
       
        # Get user_id from inventory_id
        user_query = """
        SELECT user_id
        FROM inventory
        WHERE inventory_id = %s
        """
        user_result = await execute_query_with_retry(user_query, (inventory_id,))
        user_id = user_result[0]['user_id']
        premium_level, is_premium_active = await self.check_premium_status(user_id)
        def calculate_premium_bonus(premium_level, base_amount):
            """Calculate bonus based on premium level"""
            bonus_multipliers = {
                1: 1.10,  # 10% bonus
                2: 1.20,  # 20% bonus
                3: 1.30   # 30% bonus
            }
            multiplier = bonus_multipliers.get(premium_level, 1.0)
            return int(base_amount * multiplier)
       
        # Orb reward mapping based on dungeon rank
        orb_mapping = {
            1: 6,  # E-Rank -> E-Rank Orb (orb_id 6)
            2: 4,  # C-Rank -> C-Rank Orb (orb_id 4)
            3: 1,  # S-Rank -> S-Rank Orb (orb_id 1)
            4: 5,  # D-Rank -> D-Rank Orb (orb_id 5)
            5: 3,  # B-Rank -> B-Rank Orb (orb_id 3)
            6: 2   # A-Rank -> A-Rank Orb (orb_id 2)
        }
       
        # 50% chance to get orb reward
        orb_reward = None
        if random.random() < 0.5:
            orb_id = orb_mapping.get(dungeon_rank)
            if orb_id:
                orb_quantity = random.randint(1, 3)

                await add_inventory_quantity("inventory_orbs", inventory_id, orb_id, orb_quantity)
               
                # Get orb details for reward display
                orb_details_query = """
                SELECT orb_name, orb_icon FROM leveling_orbs WHERE orb_id = %s
                """
                orb_details = await execute_query_with_retry(orb_details_query, (orb_id,))
                if orb_details:
                    orb_reward = {
                        'orb_id': orb_id,
                        'orb_name': orb_details[0]['orb_name'],
                        'orb_icon': orb_details[0]['orb_icon'],
                        'quantity': orb_quantity
                    }
       
        # Reward logic based on dungeon rank
        if dungeon_rank == 1:  # E-Rank
            # 1 shadow chest
            rewards[1] = {'chest_id': 1, 'quantity': 1}
            # 10% chance for hunter chest
            if random.random() < 0.1:
                rewards[2] = {'chest_id': 2, 'quantity': 1}
            # Update low dungeon points
            update_points_query = "UPDATE user SET e_dungeon_points = e_dungeon_points + 1 WHERE user_id = %s"
            await execute_query_with_retry(update_points_query, (user_id,))
           
        elif dungeon_rank == 4:  # D-Rank
            # 1-3 shadow chests
            shadow_count = random.randint(1, 3)
            rewards[1] = {'chest_id': 1, 'quantity': shadow_count}
            # 60% chance for hunter chest
            if random.random() < 0.6:
                rewards[2] = {'chest_id': 2, 'quantity': 1}
            # Update low dungeon points
            update_points_query = "UPDATE user SET d_dungeon_points = d_dungeon_points + 1 WHERE user_id = %s"
            await execute_query_with_retry(update_points_query, (user_id,))
           
        elif dungeon_rank == 2:  # C-Rank
            # 1 hunter chest + 1-2 shadow chests
            rewards[2] = {'chest_id': 2, 'quantity': 1}
            shadow_count = random.randint(1, 2)
            rewards[1] = {'chest_id': 1, 'quantity': shadow_count}
            # 5% chance for monarch chest
            if random.random() < 0.05:
                rewards[3] = {'chest_id': 3, 'quantity': 1}
            # Update medium dungeon points
            update_points_query = "UPDATE user SET c_dungeon_points = c_dungeon_points + 1 WHERE user_id = %s"
            await execute_query_with_retry(update_points_query, (user_id,))
            await self.check_and_update_referral_status(user_id)
           
        elif dungeon_rank == 5:  # B-Rank
            # 1-2 hunter chests + 2-3 shadow chests
            hunter_count = random.randint(1, 2)
            shadow_count = random.randint(2, 3)
            rewards[2] = {'chest_id': 2, 'quantity': hunter_count}
            rewards[1] = {'chest_id': 1, 'quantity': shadow_count}
            # 40% chance for monarch chest
            if random.random() < 0.4:
                rewards[3] = {'chest_id': 3, 'quantity': 1}
            # Update medium dungeon points
            update_points_query = "UPDATE user SET b_dungeon_points = b_dungeon_points + 1 WHERE user_id = %s"
            await execute_query_with_retry(update_points_query, (user_id,))
            await self.check_and_update_referral_status(user_id)
           
        elif dungeon_rank == 6:  # A-Rank
            # 80% chance for monarch chest
            hunter_count = random.randint(2, 3)
            rewards[2] = {'chest_id': 2, 'quantity': hunter_count}
            if random.random() < 0.8:
                rewards[3] = {'chest_id': 3, 'quantity': 1}
            # 1-2 random capsules
            capsule_count = random.randint(1, 2)
            capsule_id = random.choice([4, 5, 6, 7])  # Random capsule type
            await self.add_product_to_inventory(inventory_id, capsule_id, capsule_count)
            # Update high dungeon points
            update_points_query = "UPDATE user SET a_dungeon_points = a_dungeon_points + 1 WHERE user_id = %s"
            await execute_query_with_retry(update_points_query, (user_id,))
           
        elif dungeon_rank == 3:  # S-Rank
            jackpot_roll = random.random()
            if jackpot_roll < 0.005:  # 0.5% chance for jackpot
                # Case 2: Jackpot
                capsule_count = random.randint(10, 20)
                capsule_id = random.choice([4, 5, 6, 7])
                await self.add_product_to_inventory(inventory_id, capsule_id, capsule_count)
            else:
                # Case 1: Normal rewards
                # 1-2 monarch chests
                monarch_count = random.randint(1, 2)
                rewards[3] = {'chest_id': 3, 'quantity': monarch_count}
                # 1-3 random capsules
                capsule_count = random.randint(1, 3)
                capsule_id = random.choice([4, 5, 6, 7])
                await self.add_product_to_inventory(inventory_id, capsule_id, capsule_count)
            # Update high dungeon points
            update_points_query = "UPDATE user SET s_dungeon_points = s_dungeon_points + 1 WHERE user_id = %s"
            await execute_query_with_retry(update_points_query, (user_id,))
       
        # Update inventory_chest for chest rewards
        for chest_entry in rewards.values():
            await add_inventory_quantity(
                "inventory_chest",
                inventory_id,
                chest_entry['chest_id'],
                chest_entry['quantity']
            )
       
        # Calculate money reward from dungeon_moneycap
        money_query = "SELECT dungeon_moneycap FROM dungeon WHERE dungeon_id = %s"
        money_result = await execute_query_with_retry(money_query, (dungeon_rank,))
        money_cap = money_result[0]["dungeon_moneycap"]
        money_reward = await self.calculate_money_reward(ctx, money_cap, 0)
        gamble_reward = await self.calculate_money_reward(ctx, money_cap, 1)
       
        if is_premium_active:
            money_reward = calculate_premium_bonus(premium_level, money_reward)
            gamble_reward = calculate_premium_bonus(premium_level, gamble_reward)
       
        # Handle guild contribution and update user currency
        final_money_reward, guild_contribution = await self.handle_guild_contribution(ctx, user_id, money_reward, notify=False)
        await self.update_user_currency(user_id, gamble_reward, 0, 1)
        final_gamble_reward = gamble_reward
       
        # Handle XP rewards
        team_id = await self.get_team_id(user_id)
        xp_rewards, total_xp_gained, total_cards_leveled = await self.handle_xp_rewards(
            ctx,
            user_id,
            team_id,
            dungeon_rank,
            final_money_reward,
            final_gamble_reward,
            rewards,
            user_card_ids,
            orb_reward,
            multilanguage,
            send_embed=False
        )
       
        return {
            'rewards': rewards,
            'xp_rewards': xp_rewards,
            'total_xp_gained': total_xp_gained,
            'total_cards_leveled': total_cards_leveled,
            'money_reward': final_money_reward,
            'gamble_reward': final_gamble_reward,
            'orb_reward': orb_reward,
            'guild_contribution': guild_contribution
        }

    async def handle_xp_rewards(self, ctx, user_id, team_id, dungeon_id, money_reward, gamble_reward, rewards, fighter_card_ids=None, orb_reward=None, multilanguage : int = 0, send_embed: bool = True):
        # Get the user's team cards
        if fighter_card_ids:
            # Get the user's cards that participated in the dungeon
            placeholders = ', '.join(['%s'] * len(fighter_card_ids))
            team_cards_query = f"""
            SELECT c.card_id, c.card_name, c.type_card, c.card_icon, c.card_rank, c.xp_cap, uc.max_leveled,
                uc.current_level, uc.current_xp, uc.attack_stat, uc.health_stat, uc.defence_stat,
                uc.speed_stat, uc.base_attack, uc.base_health, uc.base_defence, uc.base_speed,
                uc.id_enhancer, uc.fight_count, uc.charm_attack, uc.charm_defence,
                uc.charm_speed, uc.charm_health, uc.current_statpoints,
                uc.attack_count, uc.defence_count, uc.speed_count, uc.health_count,
                uc.weapon_id, uc.armor_id, uc.shield_id, uc.affinity, uc.evo
            FROM user_cards uc
            JOIN card c ON uc.card_id = c.card_id
            WHERE uc.user_id = %s AND uc.card_id IN ({placeholders}) AND c.type_card='Fighter'
            """
            params = [user_id] + fighter_card_ids
            team_cards = await execute_query_with_retry(team_cards_query, params)
        else:
            # Fallback to original method if no card IDs provided
            team_cards_query = """
            SELECT tc.*, c.card_name, c.type_card, c.card_icon, c.card_rank, c.xp_cap, uc.max_leveled,
                uc.current_level, uc.current_xp, uc.attack_stat, uc.health_stat, uc.defence_stat,
                uc.speed_stat, uc.base_attack, uc.base_health, uc.base_defence, uc.base_speed,
                uc.id_enhancer, uc.fight_count, uc.charm_attack, uc.charm_defence,
                uc.charm_speed, uc.charm_health, uc.current_statpoints,
                uc.attack_count, uc.defence_count, uc.speed_count, uc.health_count,
                uc.weapon_id, uc.armor_id, uc.shield_id, uc.affinity, uc.evo
            FROM team_cards tc
            JOIN user_cards uc ON tc.card_id = uc.card_id AND uc.user_id = %s
            JOIN card c ON tc.card_id = c.card_id
            WHERE tc.team_id = %s AND c.type_card='Fighter'
            """
            team_cards = await execute_query_with_retry(team_cards_query, (user_id, team_id))
       
        # Get affinity caps for each card based on their current evolution level
        affinity_caps = {}
        if team_cards:
            for card in team_cards:
                card_id = card['card_id']
                card_evo = card['evo']
               
                if card_evo in [1, 2]:
                    # Get affinity cap from card_upgrades table for the specific evo level
                    affinity_cap_query = """
                    SELECT affinity_cap
                    FROM card_upgrades
                    WHERE card_id = %s AND evo = %s
                    """
                    affinity_cap_result = await execute_query_with_retry(affinity_cap_query, (card_id, card_evo))
                    if affinity_cap_result:
                        affinity_caps[card_id] = affinity_cap_result[0]['affinity_cap']
                    else:
                        affinity_caps[card_id] = 25  # Default fallback
                elif card_evo == 3:
                    affinity_caps[card_id] = 100  # Fixed cap for evo 3
                else:
                    affinity_caps[card_id] = 25  # Default fallback
       
        # Check for support cards in the team
        support_query = """
        SELECT COUNT(*) as support_count
        FROM team_cards tc
        JOIN card c ON tc.card_id = c.card_id
        JOIN user_cards uc ON tc.card_id = uc.card_id AND uc.user_id = %s
        WHERE tc.team_id = %s AND c.type_card = 'Support'
        """
        support_result = await execute_query_with_retry(support_query, (user_id, team_id))
        has_support_cards = support_result[0]['support_count'] > 0 if support_result else False

        # Get support card boosts if there are any support cards
        support_boosts = None
        if has_support_cards:
            support_boosts = await self.calculate_total_support_boosts(team_id, user_id)
       
        xp_rewards = []
        total_xp_gained = 0
        total_cards_leveled = 0
       
        for card in team_cards:
            skip_xp_and_stats = card['current_level'] == card['max_leveled']
           
            # Calculate base XP reward and affinity gain regardless of level
            xp_reward = random.randint(20, 100) if not skip_xp_and_stats else 0
           
            current_affinity = card['affinity']
            card_evo = card['evo']
            print("current affinity : ", current_affinity)
            print("evo:",card_evo)
           
            # Determine affinity gain range based on evo level (always calculate this)
            if card_evo == 1:
                affinity_gain = round(random.uniform(0.05, 0.07), 2)
            elif card_evo == 2:
                affinity_gain = round(random.uniform(0.02, 0.05), 2)
            elif card_evo == 3:
                affinity_gain = round(random.uniform(0.01, 0.03), 2)
            else:
                affinity_gain = 0  # Default for unknown evo levels

            # Apply dungeon-specific XP modifiers only if not at max level
            if not skip_xp_and_stats:
                if dungeon_id == 1:  # Dungeon 1 expects card rank 7
                    if card['card_rank'] == 7:
                        xp_reward = xp_reward
                    elif card['card_rank'] < 7:
                        xp_reward = xp_reward // 2
                    else:
                        xp_reward = xp_reward * 2

                elif dungeon_id == 2:  # Dungeon 2 expects card rank 5
                    if card['card_rank'] == 5:
                        xp_reward = xp_reward
                    elif card['card_rank'] < 5:
                        xp_reward = xp_reward // 2
                    else:
                        xp_reward = xp_reward * 2

                elif dungeon_id == 3:  # Dungeon 3 expects card rank 2
                    if card['card_rank'] == 2:
                        xp_reward = xp_reward
                    elif card['card_rank'] < 2:
                        xp_reward = xp_reward // 2
                    else:
                        xp_reward = xp_reward * 2

                elif dungeon_id == 4:  # Dungeon 4 expects card rank 6
                    if card['card_rank'] == 6:
                        xp_reward = xp_reward
                    elif card['card_rank'] < 6:
                        xp_reward = xp_reward // 2
                    else:
                        xp_reward = xp_reward * 2

                elif dungeon_id == 5:  # Dungeon 5 expects card rank 4
                    if card['card_rank'] == 4:
                        xp_reward = xp_reward
                    elif card['card_rank'] < 4:
                        xp_reward = xp_reward // 2
                    else:
                        xp_reward = xp_reward * 2

                elif dungeon_id == 6:  # Dungeon 6 expects card rank 3
                    if card['card_rank'] == 3:
                        xp_reward = xp_reward
                    elif card['card_rank'] < 3:
                        xp_reward = xp_reward // 2
                    else:
                        xp_reward = xp_reward * 2
           
            enhancer_active = False
            if card['id_enhancer'] is not None and card['id_enhancer'] != 0:
                enhancer_active = True
                if not skip_xp_and_stats:
                    xp_reward = int(xp_reward * 1.2)  # 20% XP boost
                affinity_gain = round(affinity_gain * 1.5, 2)
               
                new_fight_count = card['fight_count'] - 1
                if new_fight_count <= 0:
                    update_enhancer_query = """
                    UPDATE user_cards
                    SET fight_count = 0, id_enhancer = NULL
                    WHERE user_id = %s AND card_id = %s
                    """
                    await execute_query_with_retry(update_enhancer_query, (user_id, card['card_id']))
                else:
                    update_enhancer_query = """
                    UPDATE user_cards
                    SET fight_count = %s
                    WHERE user_id = %s AND card_id = %s
                    """
                    await execute_query_with_retry(update_enhancer_query, (new_fight_count, user_id, card['card_id']))
           
            affinity_cap = affinity_caps.get(card['card_id'], 25)
            calculated_affinity = affinity_gain
            affinity_maxed = False

            if current_affinity >= affinity_cap:
                affinity_maxed = True
                calculated_affinity = 0
                new_affinity = current_affinity
            elif current_affinity + calculated_affinity >= affinity_cap:
                calculated_affinity = round(affinity_cap - current_affinity, 2)
                new_affinity = affinity_cap
                affinity_maxed = True
            else:
                new_affinity = round(current_affinity + calculated_affinity, 2)

            if skip_xp_and_stats:
                # Only update affinity for max level cards
                if calculated_affinity > 0:
                    await execute_query_with_retry(
                        "UPDATE user_cards SET affinity = %s WHERE user_id = %s AND card_id = %s",
                        (new_affinity, user_id, card['card_id'])
                    )
               
                xp_rewards.append({
                    'card_name': card['card_name'],
                    'xp_gained': 0,
                    'new_level': card['current_level'],
                    'new_xp': 0,
                    'level_up': False,
                    'enhancer_active': enhancer_active,
                    'old_level': card['current_level'],
                    'xp_cap': card['xp_cap'],
                    'attack_increase': 0,
                    'health_increase': 0,
                    'defence_increase': 0,
                    'speed_increase': 0,
                    'card_icon': card['card_icon'],
                    'new_attack': card['attack_stat'],
                    'new_defence': card['defence_stat'],
                    'new_health': card['health_stat'],
                    'new_speed': card['speed_stat'],
                    'max_level_reached': True,
                    'affinity_gained': calculated_affinity,
                    'new_affinity': new_affinity,
                    'affinity_maxed': affinity_maxed,
                    'affinity_cap': affinity_cap
                })
                continue

            total_xp_gained += xp_reward
            new_xp = card['current_xp'] + xp_reward
            new_level = card['current_level']
            print("real final affinity: ",new_affinity)
           
            # Handle level up
            level_up = False
            if new_xp >= card['xp_cap']:
                if new_level < card['max_leveled']:
                    new_level += 1
                    new_xp = new_xp - card['xp_cap']
                    level_up = True
                    total_cards_leveled += 1
                else:
                    new_xp = 0  # XP stays at 0 if at level cap

            # Generate stat increases if leveled up
            attack_increase = random.randint(2, 4) if level_up else 0
            health_increase = random.randint(3, 5) if level_up else 0
            defence_increase = random.randint(1, 2) if level_up else 0
            speed_increase = random.randint(2, 4) if level_up else 0
            stat_increase = 2 if level_up else 0

            # Check if card has equipment
            has_equipment = card['weapon_id'] is not None or card['armor_id'] is not None or card['shield_id'] is not None

            # Get equipment boosts if card has equipment
            equipment_boosts = None
            if has_equipment:
                equipment_boosts = await self.calculate_equipment_boosts(user_id, card['card_id'])

            # SCENARIO HANDLING:
            # 1. No support in team and no weapon
            if not has_support_cards and not has_equipment:
                # Apply stat buff directly to both base stats and current stats
                new_base_stats = {
                    'attack': card['base_attack'] + attack_increase,
                    'health': card['base_health'] + health_increase,
                    'defence': card['base_defence'] + defence_increase,
                    'speed': card['base_speed'] + speed_increase
                }
               
                new_stats = {
                    'attack': card['attack_stat'] + attack_increase,
                    'health': card['health_stat'] + health_increase,
                    'defence': card['defence_stat'] + defence_increase,
                    'speed': card['speed_stat'] + speed_increase
                }
               
                # Update database
                if level_up:
                    update_query = """
                    UPDATE user_cards
                    SET current_level = %s, current_xp = %s,
                        base_attack = %s, base_health = %s, base_defence = %s, base_speed = %s,
                        attack_stat = %s, health_stat = %s, defence_stat = %s, speed_stat = %s,
                        power_stat = attack_stat+health_stat+defence_stat+speed_stat,
                        current_statpoints = current_statpoints + 2,
                        affinity = %s
                    WHERE user_id = %s AND card_id = %s
                    """
                    await execute_query_with_retry(
                        update_query,
                        (new_level, new_xp,
                        new_base_stats['attack'], new_base_stats['health'], new_base_stats['defence'], new_base_stats['speed'],
                        new_stats['attack'], new_stats['health'], new_stats['defence'], new_stats['speed'],
                        new_affinity, user_id, card['card_id'])
                    )
                else:
                    # Just update XP and affinity if no level up
                    await execute_query_with_retry(
                        "UPDATE user_cards SET current_xp = %s, affinity = %s WHERE user_id = %s AND card_id = %s",
                        (new_xp, new_affinity, user_id, card['card_id'])
                    )
           
            # 2. Support cards but no equipment
            elif has_support_cards and not has_equipment:
                # Reset to base stats first
                reset_query = """
                UPDATE user_cards
                SET attack_stat = base_attack,
                    health_stat = base_health,
                    defence_stat = base_defence,
                    speed_stat = base_speed
                WHERE user_id = %s AND card_id = %s
                """
                await execute_query_with_retry(reset_query, (user_id, card['card_id']))
               
                # Apply stat buff to both base stats and current stats
                new_base_stats = {
                    'attack': card['base_attack'] + attack_increase,
                    'health': card['base_health'] + health_increase,
                    'defence': card['base_defence'] + defence_increase,
                    'speed': card['base_speed'] + speed_increase
                }
               
                # Update base stats and level info
                if level_up:
                    update_base_query = """
                    UPDATE user_cards
                    SET current_level = %s, current_xp = %s,
                        base_attack = %s, base_health = %s, base_defence = %s, base_speed = %s,
                        attack_stat = %s, health_stat = %s, defence_stat = %s, speed_stat = %s,
                        current_statpoints = current_statpoints + 2,
                        affinity = %s
                    WHERE user_id = %s AND card_id = %s
                    """
                    await execute_query_with_retry(
                        update_base_query,
                        (new_level, new_xp,
                        new_base_stats['attack'], new_base_stats['health'], new_base_stats['defence'], new_base_stats['speed'],
                        new_base_stats['attack'], new_base_stats['health'], new_base_stats['defence'], new_base_stats['speed'],
                        new_affinity, user_id, card['card_id'])
                    )
                else:
                    # Just update XP and affinity if no level up
                    await execute_query_with_retry(
                        "UPDATE user_cards SET current_xp = %s, affinity = %s WHERE user_id = %s AND card_id = %s",
                        (new_xp, new_affinity, user_id, card['card_id'])
                    )
               
                # Now apply support boosts to current stats
                await self.apply_support_boosts_to_single_offense(user_id, card['card_id'], support_boosts)
               
                # Get the updated stats after support boosts
                updated_stats_query = """
                SELECT attack_stat, health_stat, defence_stat, speed_stat
                FROM user_cards
                WHERE user_id = %s AND card_id = %s
                """
                updated_stats = await execute_query_with_retry(updated_stats_query, (user_id, card['card_id']))
               
                new_stats = {
                    'attack': updated_stats[0]['attack_stat'],
                    'health': updated_stats[0]['health_stat'],
                    'defence': updated_stats[0]['defence_stat'],
                    'speed': updated_stats[0]['speed_stat']
                }
               
                # Update power_stat
                await execute_query_with_retry(
                    "UPDATE user_cards SET power_stat = attack_stat+health_stat+defence_stat+speed_stat WHERE user_id = %s AND card_id = %s",
                    (user_id, card['card_id'])
                )
           
            # 3. No support cards but has equipment
            elif not has_support_cards and has_equipment:
                # First, get the true base stats by removing equipment boosts from base stats
                true_base_stats = {
                    'attack': card['base_attack'],
                    'health': card['base_health'],
                    'defence': card['base_defence'],
                    'speed': card['base_speed']
                }
               
                # Remove equipment boosts from base stats
                if equipment_boosts:
                    # Remove percentage boosts first (in reverse order of application)
                    true_base_stats['attack'] = true_base_stats['attack'] / (1 + equipment_boosts['pct_attack']/100)
                    true_base_stats['health'] = true_base_stats['health'] / (1 + equipment_boosts['pct_health']/100)
                    true_base_stats['defence'] = true_base_stats['defence'] / (1 + equipment_boosts['pct_defence']/100)
                    true_base_stats['speed'] = true_base_stats['speed'] / (1 + equipment_boosts['pct_speed']/100)
                    # Then remove flat boosts
                    true_base_stats['attack'] -= equipment_boosts['flat_attack']
                    true_base_stats['health'] -= equipment_boosts['flat_health']
                    true_base_stats['defence'] -= equipment_boosts['flat_defence']
                    true_base_stats['speed'] -= equipment_boosts['flat_speed']
               
                # Apply stat buff to true base stats
                new_true_base_stats = {
                    'attack': true_base_stats['attack'] + attack_increase,
                    'health': true_base_stats['health'] + health_increase,
                    'defence': true_base_stats['defence'] + defence_increase,
                    'speed': true_base_stats['speed'] + speed_increase
                }
               
                # Now calculate new base stats with equipment boosts re-applied
                new_base_stats = dict(new_true_base_stats)  # Start with true base stats + stat increases
               
                # Apply equipment boosts to new base stats
                if equipment_boosts:
                    # Apply flat boosts first
                    new_base_stats['attack'] += equipment_boosts['flat_attack']
                    new_base_stats['health'] += equipment_boosts['flat_health']
                    new_base_stats['defence'] += equipment_boosts['flat_defence']
                    new_base_stats['speed'] += equipment_boosts['flat_speed']
                    # Then apply percentage boosts
                    new_base_stats['attack'] *= (1 + equipment_boosts['pct_attack']/100)
                    new_base_stats['health'] *= (1 + equipment_boosts['pct_health']/100)
                    new_base_stats['defence'] *= (1 + equipment_boosts['pct_defence']/100)
                    new_base_stats['speed'] *= (1 + equipment_boosts['pct_speed']/100)
               
                # Update base stats and current stats with the buff
                if level_up:
                    update_base_query = """
                    UPDATE user_cards
                    SET current_level = %s, current_xp = %s,
                        base_attack = %s, base_health = %s, base_defence = %s, base_speed = %s,
                        attack_stat = %s, health_stat = %s, defence_stat = %s, speed_stat = %s,
                        current_statpoints = current_statpoints + 2,
                        affinity = %s
                    WHERE user_id = %s AND card_id = %s
                    """
                    await execute_query_with_retry(
                        update_base_query,
                        (new_level, new_xp,
                        new_base_stats['attack'], new_base_stats['health'], new_base_stats['defence'], new_base_stats['speed'],
                        new_base_stats['attack'], new_base_stats['health'], new_base_stats['defence'], new_base_stats['speed'],
                        new_affinity, user_id, card['card_id'])
                    )
                else:
                    # Just update XP and affinity if no level up
                    await execute_query_with_retry(
                        "UPDATE user_cards SET current_xp = %s, affinity = %s WHERE user_id = %s AND card_id = %s",
                        (new_xp, new_affinity, user_id, card['card_id'])
                    )
               
                # Get the updated stats
                updated_stats_query = """
                SELECT attack_stat, health_stat, defence_stat, speed_stat
                FROM user_cards
                WHERE user_id = %s AND card_id = %s
                """
                updated_stats = await execute_query_with_retry(updated_stats_query, (user_id, card['card_id']))
               
                new_stats = {
                    'attack': updated_stats[0]['attack_stat'],
                    'health': updated_stats[0]['health_stat'],
                    'defence': updated_stats[0]['defence_stat'],
                    'speed': updated_stats[0]['speed_stat']
                }
               
                # Update power_stat
                await execute_query_with_retry(
                    "UPDATE user_cards SET power_stat = attack_stat+health_stat+defence_stat+speed_stat WHERE user_id = %s AND card_id = %s",
                    (user_id, card['card_id'])
                )
           
            # 4. Has both support cards and equipment
            else:
                # First, get the true base stats by removing equipment boosts from base stats
                true_base_stats = {
                    'attack': card['base_attack'],
                    'health': card['base_health'],
                    'defence': card['base_defence'],
                    'speed': card['base_speed']
                }
               
                # Remove equipment boosts from base stats
                if equipment_boosts:
                    # Remove percentage boosts first (in reverse order of application)
                    true_base_stats['attack'] = true_base_stats['attack'] / (1 + equipment_boosts['pct_attack']/100)
                    true_base_stats['health'] = true_base_stats['health'] / (1 + equipment_boosts['pct_health']/100)
                    true_base_stats['defence'] = true_base_stats['defence'] / (1 + equipment_boosts['pct_defence']/100)
                    true_base_stats['speed'] = true_base_stats['speed'] / (1 + equipment_boosts['pct_speed']/100)
                    # Then remove flat boosts
                    true_base_stats['attack'] -= equipment_boosts['flat_attack']
                    true_base_stats['health'] -= equipment_boosts['flat_health']
                    true_base_stats['defence'] -= equipment_boosts['flat_defence']
                    true_base_stats['speed'] -= equipment_boosts['flat_speed']
               
                # Apply stat buff to true base stats
                new_true_base_stats = {
                    'attack': true_base_stats['attack'] + attack_increase,
                    'health': true_base_stats['health'] + health_increase,
                    'defence': true_base_stats['defence'] + defence_increase,
                    'speed': true_base_stats['speed'] + speed_increase
                }
               
                # Now calculate new base stats with equipment boosts re-applied
                new_base_stats = dict(new_true_base_stats)  # Start with true base stats + stat increases
               
                # Apply equipment boosts to new base stats
                if equipment_boosts:
                    # Apply flat boosts first
                    new_base_stats['attack'] += equipment_boosts['flat_attack']
                    new_base_stats['health'] += equipment_boosts['flat_health']
                    new_base_stats['defence'] += equipment_boosts['flat_defence']
                    new_base_stats['speed'] += equipment_boosts['flat_speed']
                    # Then apply percentage boosts
                    new_base_stats['attack'] *= (1 + equipment_boosts['pct_attack']/100)
                    new_base_stats['health'] *= (1 + equipment_boosts['pct_health']/100)
                    new_base_stats['defence'] *= (1 + equipment_boosts['pct_defence']/100)
                    new_base_stats['speed'] *= (1 + equipment_boosts['pct_speed']/100)
               
                # Reset current stats to true base stats + stat increases (without equipment boosts)
                reset_query = """
                UPDATE user_cards
                SET attack_stat = %s,
                    health_stat = %s,
                    defence_stat = %s,
                    speed_stat = %s
                WHERE user_id = %s AND card_id = %s
                """
                await execute_query_with_retry(
                    reset_query,
                    (new_true_base_stats['attack'], new_true_base_stats['health'],
                    new_true_base_stats['defence'], new_true_base_stats['speed'],
                    user_id, card['card_id'])
                )
               
                # Update base stats and level info
                if level_up:
                    update_base_query = """
                    UPDATE user_cards
                    SET current_level = %s, current_xp = %s,
                        base_attack = %s, base_health = %s, base_defence = %s, base_speed = %s,
                        current_statpoints = current_statpoints + 2,
                        affinity = %s
                    WHERE user_id = %s AND card_id = %s
                    """
                    await execute_query_with_retry(
                        update_base_query,
                        (new_level, new_xp,
                        new_base_stats['attack'], new_base_stats['health'], new_base_stats['defence'], new_base_stats['speed'],
                        new_affinity, user_id, card['card_id'])
                    )
                else:
                    # Just update XP and affinity if no level up
                    await execute_query_with_retry(
                        "UPDATE user_cards SET current_xp = %s, affinity = %s WHERE user_id = %s AND card_id = %s",
                        (new_xp, new_affinity, user_id, card['card_id'])
                    )
               
                # Apply equipment boosts to current stats
                await self.apply_equipment_boosts(user_id, card['card_id'], equipment_boosts)
               
                # Then apply support boosts
                await self.apply_support_boosts_to_single_offense(user_id, card['card_id'], support_boosts)
               
                # Get the updated stats after all boosts
                updated_stats_query = """
                SELECT attack_stat, health_stat, defence_stat, speed_stat
                FROM user_cards
                WHERE user_id = %s AND card_id = %s
                """
                updated_stats = await execute_query_with_retry(updated_stats_query, (user_id, card['card_id']))
               
                new_stats = {
                    'attack': updated_stats[0]['attack_stat'],
                    'health': updated_stats[0]['health_stat'],
                    'defence': updated_stats[0]['defence_stat'],
                    'speed': updated_stats[0]['speed_stat']
                }
               
                # Update power_stat
                await execute_query_with_retry(
                    "UPDATE user_cards SET power_stat = attack_stat+health_stat+defence_stat+speed_stat WHERE user_id = %s AND card_id = %s",
                    (user_id, card['card_id'])
                )

            xp_rewards.append({
                'card_name': card['card_name'],
                'xp_gained': xp_reward,
                'new_level': new_level,
                'new_xp': new_xp,
                'enhancer_active': enhancer_active,
                'level_up': level_up,
                'old_level': card['current_level'],
                'xp_cap': card['xp_cap'],
                'attack_increase': attack_increase,
                'health_increase': health_increase,
                'defence_increase': defence_increase,
                'speed_increase': speed_increase,
                'card_icon': card['card_icon'],
                'new_attack': new_stats['attack'],
                'new_defence': new_stats['defence'],
                'new_health': new_stats['health'],
                'new_speed': new_stats['speed'],
                'max_level_reached': new_level == card['max_leveled'],
                'affinity_gained': calculated_affinity,
                'new_affinity': new_affinity,
                'affinity_maxed': affinity_maxed,
                'affinity_cap': affinity_cap
            })
       
        if send_embed:
            await self.send_rewards_embed(ctx, xp_rewards, total_xp_gained, total_cards_leveled, money_reward, gamble_reward, rewards, orb_reward, multilanguage)
        return xp_rewards, total_xp_gained, total_cards_leveled

    async def calculate_equipment_boosts(self, user_id, card_id):
        """Calculate the boosts provided by a card's equipment"""
        try:
            # Get the card's equipment
            equipment_query = """
                SELECT weapon_id, armor_id, shield_id
                FROM user_cards
                WHERE user_id = %s AND card_id = %s
            """
            equipment = await execute_query_with_retry(equipment_query, (user_id, card_id))

            if not equipment:
                return {
                    'flat_attack': 0, 'flat_health': 0, 'flat_defence': 0, 'flat_speed': 0,
                    'pct_attack': 0, 'pct_health': 0, 'pct_defence': 0, 'pct_speed': 0
                }

            equipment = equipment[0]  # Now safe to access first element

            # Initialize boosts
            boosts = {
                'flat_attack': 0, 'flat_health': 0, 'flat_defence': 0, 'flat_speed': 0,
                'pct_attack': 0, 'pct_health': 0, 'pct_defence': 0, 'pct_speed': 0
            }

            # Check each equipment type
            equipment_types = [
                equipment.get('weapon_id'),
                equipment.get('armor_id'),
                equipment.get('shield_id')
            ]

            for eq_id in equipment_types:
                if eq_id:
                    # Query equipment details
                    query = f"""
                    SELECT attack_boost, health_boost, speed_boost, defense_boost,
                        attack_percentage, health_percentage,
                        speed_percentage, defense_percentage
                    FROM weapon
                    WHERE weapon_id = %s
                    """
                    eq_details = await execute_query_with_retry(query, (eq_id,))
                   
                    if eq_details:
                        eq_details = eq_details[0]
                        # Add equipment flat boosts
                        boosts['flat_attack'] += eq_details.get('attack_boost', 0) or 0
                        boosts['flat_health'] += eq_details.get('health_boost', 0) or 0
                        boosts['flat_speed'] += eq_details.get('speed_boost', 0) or 0
                        boosts['flat_defence'] += eq_details.get('defense_boost', 0) or 0
                       
                        # Add equipment percentage boosts
                        boosts['pct_attack'] += eq_details.get('attack_percentage', 0) or 0
                        boosts['pct_health'] += eq_details.get('health_percentage', 0) or 0
                        boosts['pct_speed'] += eq_details.get('speed_percentage', 0) or 0
                        boosts['pct_defence'] += eq_details.get('defense_percentage', 0) or 0

            return boosts
        except Exception as e:
            print(f"Error in calculate_equipment_boosts: {e}")
            # Return default values on error
            return {
                'flat_attack': 0, 'flat_health': 0, 'flat_defence': 0, 'flat_speed': 0,
                'pct_attack': 0, 'pct_health': 0, 'pct_defence': 0, 'pct_speed': 0
            }

    # Fix for the calculate_support_card_boosts function
    async def calculate_support_card_boosts(self, user_id, card_id):
        """Calculate the total boosts from a single support card and its equipment"""
        try:
            # Get support card details
            query = """
            SELECT c.card_id, c.attack, c.health, c.speed, c.defence,
                c.attack_percentage, c.health_percentage, c.speed_percentage, c.defence_percentage,
                uc.weapon_id, uc.armor_id, uc.shield_id
            FROM card c
            JOIN user_cards uc ON c.card_id = uc.card_id
            WHERE c.card_id = %s AND uc.user_id = %s
            """
            support_card_result = await execute_query_with_retry(query, (card_id, user_id))
           
            if not support_card_result:
                return {
                    'flat_attack': 0, 'flat_health': 0, 'flat_defence': 0, 'flat_speed': 0,
                    'pct_attack': 0, 'pct_health': 0, 'pct_speed': 0, 'pct_defence': 0
                }
               
            support_card = support_card_result[0]  # Now safe to access first element
           
            # Initialize boosts
            flat_attack = support_card.get('attack', 0) or 0
            flat_health = support_card.get('health', 0) or 0
            flat_speed = support_card.get('speed', 0) or 0
            flat_defence = support_card.get('defence', 0) or 0
           
            pct_attack = support_card.get('attack_percentage', 0) or 0
            pct_health = support_card.get('health_percentage', 0) or 0
            pct_speed = support_card.get('speed_percentage', 0) or 0
            pct_defence = support_card.get('defence_percentage', 0) or 0
           
            # Add equipment boosts
            equipment_types = [
                support_card.get('weapon_id'),
                support_card.get('armor_id'),
                support_card.get('shield_id')
            ]
           
            for eq_id in equipment_types:
                if eq_id:
                    # Query equipment details
                    query = f"""
                    SELECT attack_boost, health_boost, speed_boost, defense_boost,
                        attack_percentage, health_percentage,
                        speed_percentage, defense_percentage
                    FROM weapon
                    WHERE weapon_id = %s
                    """
                    eq_details_result = await execute_query_with_retry(query, (eq_id,))
                   
                    if eq_details_result:
                        eq_details = eq_details_result[0]
                        # Add equipment flat boosts
                        flat_attack += eq_details.get('attack_boost', 0) or 0
                        flat_health += eq_details.get('health_boost', 0) or 0
                        flat_speed += eq_details.get('speed_boost', 0) or 0
                        flat_defence += eq_details.get('defense_boost', 0) or 0
                       
                        # Add equipment percentage boosts
                        pct_attack += eq_details.get('attack_percentage', 0) or 0
                        pct_health += eq_details.get('health_percentage', 0) or 0
                        pct_speed += eq_details.get('speed_percentage', 0) or 0
                        pct_defence += eq_details.get('defence_percentage', 0) or 0
           
            return {
                'flat_attack': flat_attack,
                'flat_health': flat_health,
                'flat_speed': flat_speed,
                'flat_defence': flat_defence,
                'pct_attack': pct_attack,
                'pct_health': pct_health,
                'pct_speed': pct_speed,
                'pct_defence': pct_defence
            }
        except Exception as e:
            print(f"Error in calculate_support_card_boosts: {e}")
            # Return default values on error
            return {
                'flat_attack': 0, 'flat_health': 0, 'flat_defence': 0, 'flat_speed': 0,
                'pct_attack': 0, 'pct_health': 0, 'pct_speed': 0, 'pct_defence': 0
            }
    async def add_product_to_inventory(self, inventory_id, product_id, quantity):
        """Helper method to add products (like capsules) to inventory"""
        await add_inventory_quantity("inventory_product", inventory_id, product_id, quantity)
    async def apply_equipment_boosts(self, user_id, card_id, equipment_boosts):
        """Apply equipment boosts to a card's stats"""
        if not equipment_boosts:
            return
       
        # Apply flat boosts first
        flat_query = """
        UPDATE user_cards
        SET attack_stat = attack_stat + %s,
            health_stat = health_stat + %s,
            defence_stat = defence_stat + %s,
            speed_stat = speed_stat + %s
        WHERE user_id = %s AND card_id = %s
        """
        await execute_query_with_retry(
            flat_query,
            (equipment_boosts['flat_attack'], equipment_boosts['flat_health'],
            equipment_boosts['flat_defence'], equipment_boosts['flat_speed'],
            user_id, card_id)
        )
       
        # Then apply percentage boosts
        pct_query = """
        UPDATE user_cards
        SET attack_stat = attack_stat * (1 + %s/100),
            health_stat = health_stat * (1 + %s/100),
            defence_stat = defence_stat * (1 + %s/100),
            speed_stat = speed_stat * (1 + %s/100)
        WHERE user_id = %s AND card_id = %s
        """
        await execute_query_with_retry(
            pct_query,
            (equipment_boosts['pct_attack'], equipment_boosts['pct_health'],
            equipment_boosts['pct_defence'], equipment_boosts['pct_speed'],
            user_id, card_id)
        )

    async def calculate_total_support_boosts(self, team_id, user_id):
        """
        Calculate the total flat and percentage boosts from all support cards in the team
        """
        try:
            # Get all support cards in the team
            query = """
            SELECT c.card_id, c.attack, c.health, c.speed, c.defence,
                c.attack_percentage, c.health_percentage, c.speed_percentage, c.defence_percentage,
                uc.weapon_id, uc.armor_id, uc.shield_id
            FROM team_cards tc
            JOIN user_cards uc ON tc.card_id = uc.card_id
            JOIN card c ON uc.card_id = c.card_id
            WHERE tc.team_id = %s AND uc.user_id = %s AND c.type_card = 'Support'
            """
            support_cards = await execute_query_with_retry(query, (team_id, user_id))
           
            # Check if support_cards is None or empty
            if not support_cards:
                # Return default values if no support cards found
                return {
                    'flat_attack': 0, 'flat_health': 0, 'flat_defence': 0, 'flat_speed': 0,
                    'pct_attack': 0, 'pct_health': 0, 'pct_defence': 0, 'pct_speed': 0
                }
           
            # Initialize total boosts
            total_flat_attack = 0
            total_flat_health = 0
            total_flat_speed = 0
            total_flat_defence = 0
           
            total_pct_attack = 0
            total_pct_health = 0
            total_pct_speed = 0
            total_pct_defence = 0
           
            # Calculate total boosts from all support cards and their equipment
            for support in support_cards:
                # Add support card flat boosts (with null checks)
                total_flat_attack += support.get('attack', 0) or 0
                total_flat_health += support.get('health', 0) or 0
                total_flat_speed += support.get('speed', 0) or 0
                total_flat_defence += support.get('defence', 0) or 0
               
                # Add support card percentage boosts (with null checks)
                total_pct_attack += support.get('attack_percentage', 0) or 0
                total_pct_health += support.get('health_percentage', 0) or 0
                total_pct_speed += support.get('speed_percentage', 0) or 0
                total_pct_defence += support.get('defence_percentage', 0) or 0
               
                # Add equipment boosts - FIX: Don't try to unpack equipment IDs
                equipment_ids = [
                    support.get('weapon_id'),
                    support.get('armor_id'),
                    support.get('shield_id')
                ]
               
                for eq_id in equipment_ids:
                    if eq_id:
                        try:
                            # Query equipment details
                            query = """
                            SELECT attack_boost, health_boost, speed_boost, defense_boost,
                                attack_percentage, health_percentage,
                                speed_percentage, defense_percentage
                            FROM weapon
                            WHERE weapon_id = %s
                            """
                            eq_details = await execute_query_with_retry(query, (eq_id,))
                           
                            if eq_details and eq_details[0]:
                                eq_details = eq_details[0]
                                # Add equipment flat boosts
                                total_flat_attack += eq_details.get('attack_boost', 0) or 0
                                total_flat_health += eq_details.get('health_boost', 0) or 0
                                total_flat_speed += eq_details.get('speed_boost', 0) or 0
                                total_flat_defence += eq_details.get('defense_boost', 0) or 0
                               
                                # Add equipment percentage boosts
                                total_pct_attack += eq_details.get('attack_percentage', 0) or 0
                                total_pct_health += eq_details.get('health_percentage', 0) or 0
                                total_pct_speed += eq_details.get('speed_percentage', 0) or 0
                                total_pct_defence += eq_details.get('defense_percentage', 0) or 0
                        except Exception as e:
                            print(f"Error querying equipment with ID {eq_id}: {e}")
                            # Continue with the next equipment
                            continue
           
            return {
                'flat_attack': total_flat_attack,
                'flat_health': total_flat_health,
                'flat_speed': total_flat_speed,
                'flat_defence': total_flat_defence,
                'pct_attack': total_pct_attack,
                'pct_health': total_pct_health,
                'pct_speed': total_pct_speed,
                'pct_defence': total_pct_defence
            }
        except Exception as e:
            print(f"Error in calculate_total_support_boosts: {e}")
            # Return default values on error
            return {
                'flat_attack': 0, 'flat_health': 0, 'flat_defence': 0, 'flat_speed': 0,
                'pct_attack': 0, 'pct_health': 0, 'pct_defence': 0, 'pct_speed': 0
            }
    async def apply_support_boosts_to_single_offense(self, user_id, card_id, support_boosts):
        """Apply the given support boosts to a single offense card"""
        if not support_boosts:
            return
           
        # Apply flat boosts first
        flat_query = """
        UPDATE user_cards
        SET attack_stat = attack_stat + %s,
            health_stat = health_stat + %s,
            defence_stat = defence_stat + %s,
            speed_stat = speed_stat + %s
        WHERE user_id = %s AND card_id = %s
        """
        await execute_query_with_retry(
            flat_query,
            (support_boosts['flat_attack'], support_boosts['flat_health'],
            support_boosts['flat_defence'], support_boosts['flat_speed'],
            user_id, card_id)
        )
       
        # Then apply percentage boosts
        pct_query = """
        UPDATE user_cards
        SET attack_stat = attack_stat * (1 + %s/100),
            health_stat = health_stat * (1 + %s/100),
            defence_stat = defence_stat * (1 + %s/100),
            speed_stat = speed_stat * (1 + %s/100)
        WHERE user_id = %s AND card_id = %s
        """
        await execute_query_with_retry(
            pct_query,
            (support_boosts['pct_attack'], support_boosts['pct_health'],
            support_boosts['pct_defence'], support_boosts['pct_speed'],
            user_id, card_id)
        )

    async def calculate_money_reward(self, ctx, money_cap, case):
        user_id = await self.get_user_id(ctx.author.id)
        # Check if user has card_id 108
        check_card_query = """
        SELECT card_id FROM user_cards
        WHERE user_id = %s AND card_id = 106
        """
        card_result = await execute_query_with_retry(check_card_query, (user_id,))
        if case == 0:
            min_reward = int(money_cap * 0.2)
            # Calculate base money prize
            money_prize = random.randint(min_reward, money_cap)
           
            # Add bonus if user has card 108
            if card_result:
                print("lol")
                money_prize += 200
            else:
                print("no")
           
            return money_prize
        else:
            money_prize = random.randint(50, money_cap)
            return money_prize

    async def send_rewards_embed(
        self,
        ctx,
        xp_rewards,
        total_xp_gained,
        total_cards_leveled,
        money_reward,
        gamble_reward,
        rewards,
        orb_reward=None,
        multilanguage: int = 0,
    ):
        # Build chest line
        reward_message = _tr(
            multilanguage,
            "You've obtained the following chests: ",
            "لقد حصلت على الصناديق التالية: ",
            "Vous avez obtenu les coffres suivants : ",
        )
        for chest_info in rewards.values():
            chest_name_query = "SELECT chest_icon FROM chest WHERE chest_id = %s"
            chest_name_result = await execute_query_with_retry(chest_name_query, (chest_info['chest_id'],))
            chest_name = chest_name_result[0]["chest_icon"]
            reward_message += f"{chest_info['quantity']}x {chest_name} "

        # Money/Gamble lines
        description_text = (
            f"{reward_message}\n"
            + _tr(
                multilanguage,
                f"Won Received: **{money_reward}** <:won:1304034228966658048>",
                f"الوون المستلم: **{money_reward}** <:won:1304034228966658048>",
                f"Won reçu : **{money_reward}** <:won:1304034228966658048>",
            )
            + "\n"
            + _tr(
                multilanguage,
                f"GWon Received : **{gamble_reward}** <:gamble:1314341368176574564>",
                f"GWon المستلم : **{gamble_reward}** <:gamble:1314341368176574564>",
                f"GWon reçu : **{gamble_reward}** <:gamble:1314341368176574564>",
            )
        )

        if orb_reward:
            description_text += "\n" + _tr(
                multilanguage,
                f"Orb Obtained: **{orb_reward['quantity']}x** {orb_reward['orb_icon']}",
                f"كرة سحرية تم الحصول عليها: **{orb_reward['quantity']}x** {orb_reward['orb_icon']}",
                f"Orbe obtenu : **{orb_reward['quantity']}x** {orb_reward['orb_icon']}",
            )

        description_text += (
            "\n"
            + _tr(multilanguage, "Total XP Gained:", "إجمالي الخبرة المكتسبة:", "XP total gagné :")
            + f" **{total_xp_gained}**\n"
            + _tr(multilanguage, "Cards Leveled Up:", "البطاقات التي ارتقت:", "Cartes montées de niveau :")
            + f" **{total_cards_leveled}**"
        )

        embed = discord.Embed(
            title=_tr(multilanguage, "🏆 Battle Rewards Summary 🏆", "🏆 ملخص مكافآت المعركة 🏆", "🏆 Récapitulatif des récompenses 🏆"),
            color=discord.Color.gold(),
            description=description_text,
        )

        # Per-card breakdown
        for reward in xp_rewards:
            # MAX LEVEL cards
            if reward.get('max_level_reached', False):
                card_info = _tr(
                    multilanguage,
                    "No further XP or stat increases possible.",
                    "لا مزيد من الخبرة أو زيادات الإحصائيات ممكنة.",
                    "Plus d’XP ni d’augmentation de stats possibles.",
                )

                # Affinity on max level?
                if reward.get('affinity_gained', 0) > 0:
                    aff_line = _tr(multilanguage, "Affinity", "الأُلفة", "Affinité")
                    affinity_info = f"\n✨ **{aff_line} +{reward['affinity_gained']:.2f}** (Total: {reward['new_affinity']:.2f}"
                    if reward.get('affinity_maxed', False):
                        affinity_info += _tr(multilanguage, " - MAX AFFINITY!)", " - الأُلفة القصوى!)", " - AFFINITÉ MAX !)")
                    else:
                        affinity_info += f"/{reward.get('affinity_cap', 0):.2f})"
                    card_info += affinity_info
                elif reward.get('affinity_maxed', False):
                    card_info += "\n" + _tr(
                        multilanguage,
                        "🌟 **Affinity is already maxed!**",
                        "🌟 **الأُلفة وصلت إلى الحد الأقصى بالفعل!**",
                        "🌟 **L’affinité est déjà au maximum !**",
                    )

                embed.add_field(
                    name=f"{reward.get('card_icon','')} {reward.get('card_name','')}: 🏆 " + _tr(multilanguage, "**MAX LEVEL**", "**أقصى مستوى**", "**NIVEAU MAX**"),
                    value=card_info,
                    inline=False,
                )
                continue

            # XP progress
            xp_cap = max(1, reward.get('xp_cap', 1))
            xp_percentage = (reward.get('new_xp', 0) / xp_cap) * 100
            progress_bar = self.create_xp_progress_bar(xp_percentage)

            card_info = (
                ( _tr(multilanguage, "🔥 Enhanced! ", "🔥 مُعزَّز! ", "🔥 Amélioré ! ") if reward.get('enhancer_active') else "" )
                + f"{progress_bar} **{reward.get('new_xp',0)}/{xp_cap}**\n"
            )

            # Affinity (non-max cards)
            if reward.get('affinity_gained', 0) > 0:
                aff_label = _tr(multilanguage, "Affinity", "الأُلفة", "Affinité")
                affinity_info = f"✨ **{aff_label} +{reward['affinity_gained']:.2f}** (Total: {reward['new_affinity']:.2f}"
                if reward.get('affinity_maxed', False):
                    affinity_info += _tr(multilanguage, " - MAX AFFINITY!)", " - الأُلفة القصوى!)", " - AFFINITÉ MAX !)")
                else:
                    affinity_info += f"/{reward.get('affinity_cap', 0):.2f})"
                card_info += affinity_info

            # Level up block (support both schemas)
            if reward.get('level_up'):
                atk_inc = reward.get('attack_increase', reward.get('stat_increases', {}).get('attack', 0))
                hp_inc = reward.get('health_increase', reward.get('stat_increases', {}).get('health', 0))
                def_inc = reward.get('defence_increase', reward.get('stat_increases', {}).get('defence', 0))
                spd_inc = reward.get('speed_increase', reward.get('stat_increases', {}).get('speed', 0))

                level_up_info = (
                    f"\n{_tr(multilanguage, '✨ **LEVEL UP!**', '✨ **ارتقاء بالمستوى!**', '✨ **NIVEAU + !**')} "
                    f"{reward.get('old_level')} → {reward.get('new_level')}\n"
                    + _tr(multilanguage, "📈 Stats Increased:", "📈 تمّت زيادة الإحصائيات:", "📈 Statistiques augmentées :") + "\n"
                    "```diff\n"
                    f"+ ATK: +{atk_inc}\n"
                    f"+ HP:  +{hp_inc}\n"
                    f"+ DEF: +{def_inc}\n"
                    f"+ SPD: +{spd_inc}\n"
                    "```"
                )
                card_info += level_up_info

            embed.add_field(
                name=f"{reward.get('card_icon','')} {reward.get('card_name','')}: "
                    + _tr(multilanguage, "**+{xp}**", "**+{xp}**", "**+{xp}**").format(xp=reward.get('xp_gained', 0)),
                value=card_info,
                inline=False,
            )

        embed.set_footer(text=_tr(
            multilanguage,
            "Keep fighting to grow stronger! | Solo Leveling PVP",
            "واصل القتال لتصبح أقوى! | Solo Leveling PVP",
            "Continuez à vous battre pour devenir plus fort ! | Solo Leveling PVP",
        ))
        await safe_send(ctx, embed=embed)




    def create_xp_progress_bar(self, percentage, length=10):
        filled = int((percentage / 100.0) * length)
        empty = length - filled
       
        # Use custom emotes for the progress bar
        bar = (self.green_square * filled) + (self.gray_square * empty)
        return f"{bar} **{percentage:.1f}%**"

    async def handle_guild_contribution(self, ctx, user_id, money_reward, notify: bool = True):
        # Check if user is in a guild
        guild_query = "SELECT guild_id FROM user WHERE user_id = %s"
        guild_result = await execute_query_with_retry(guild_query, (user_id,))
        guild_contribution = 0

        if guild_result and guild_result[0]['guild_id']:
            guild_id = guild_result[0]['guild_id']
            guild_contribution = int(money_reward * 0.05)  # 5% guild contribution
            final_reward = money_reward - guild_contribution

            # Update guild fund
            guild_update_query = "UPDATE guild SET fund_cash = fund_cash + %s WHERE guild_id = %s"
            await execute_query_with_retry(guild_update_query, (guild_contribution, guild_id))

            if notify:
                await safe_send(ctx, f"Your guild received a contribution of {guild_contribution} <:won:1304034228966658048>.")
        else:
            final_reward = money_reward

        # Update user currency
        await self.update_user_currency(user_id, final_reward, 0, 0)

        return final_reward, guild_contribution

    async def update_user_currency(self, user_id, money_reward, losswin, case):
        if losswin == 0:
            if case == 0:
                money_reward=await self.calculate_referral_bonus(user_id,money_reward)
                update_query = "UPDATE user SET won_currency = won_currency + %s WHERE user_id = %s"
                await execute_query_with_retry(update_query, (money_reward, user_id))
            else:
                money_reward=await self.calculate_referral_bonus(user_id,money_reward)
                update_query = "UPDATE user SET gamble_currency = gamble_currency + %s WHERE user_id = %s"
                await execute_query_with_retry(update_query, (money_reward, user_id))
        else:
            # First get current won_currency value
            check_query = "SELECT won_currency FROM user WHERE user_id = %s"
            current_balance = await execute_query_with_retry(check_query, (user_id,))
            current_balance = current_balance[0]['won_currency']
            # If current balance is 0, no update needed
            if current_balance == 0:
                return
           
            # Calculate potential new balance
            # Note: money_reward is already negative in the elif case
            new_balance = current_balance + money_reward
           
            if new_balance < 0:
                # If would go negative, set to 0
                update_query = "UPDATE user SET won_currency = 0 WHERE user_id = %s"
                await execute_query_with_retry(update_query, (user_id,))
            else:
                # Safe to subtract
                money_reward=await self.calculate_referral_bonus(user_id,money_reward)
                update_query = "UPDATE user SET won_currency = won_currency + %s WHERE user_id = %s"
                await execute_query_with_retry(update_query, (money_reward, user_id))

    async def get_dungeon_items(self, dungeon_id):
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
        dungeon_items['orbs'] = await execute_query_with_retry(orb_query, (dungeon_id,))

        # Fetch fragments
        fragment_query = """
        SELECT f.id_fragment as id, f.name_fragment as name
        FROM dungeon_fragments df
        JOIN fragments f ON df.frag_id = f.id_fragment
        WHERE df.dungeon_id = %s
        """
        dungeon_items['fragments'] = await execute_query_with_retry(fragment_query, (dungeon_id,))

        # Fetch weapons
        weapon_query = """
        SELECT w.weapon_id as id, w.weapon_name as name
        FROM dungeon_weapon dw
        JOIN weapon w ON dw.weapon_id = w.weapon_id
        WHERE dw.dungeon_id = %s
        """
        dungeon_items['weapons'] = await execute_query_with_retry(weapon_query, (dungeon_id,))

        # Fetch products (xp_boost, stat_boosts, special_items)
        product_query = """
        SELECT p.product_id as id, p.product_name as name,
               p.xp_boost, p.attack_boost, p.health_boost, p.speed_boost
        FROM dungeon_product dp
        JOIN product p ON dp.product_id = p.product_id
        WHERE dp.dungeon_id = %s
        """
        products = await execute_query_with_retry(product_query, (dungeon_id,))

        for product in products:
            if product['xp_boost'] != 0 and product['attack_boost'] == 0 and product['health_boost'] == 0 and product['speed_boost'] == 0:
                dungeon_items['xp_boost'].append(product)
            elif product['xp_boost'] == 0 and (product['attack_boost'] != 0 or product['health_boost'] != 0 or product['speed_boost'] != 0):
                dungeon_items['stat_boosts'].append(product)
            else:
                dungeon_items['special_items'].append(product)

        return dungeon_items

    async def update_inventory(self, inventory_id, item_id, quantity, item_type):
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
        result = await execute_query_with_retry(check_query, (inventory_id, item_id))

        if result:
            # Update existing item
            update_query = f"""
            UPDATE {table}
            SET quantiter = quantiter + %s
            WHERE inventory_id = %s AND {id_column} = %s
            """
            await execute_query_with_retry(update_query, (quantity, inventory_id, item_id))
        else:
            # Insert new item
            insert_query = f"""
            INSERT INTO {table} (inventory_id, {id_column}, quantiter)
            VALUES (%s, %s, %s)
            """
            await execute_query_with_retry(insert_query, (inventory_id, item_id, quantity))

    async def apply_charm_effects(self, user_id, team_cards):
        for card in team_cards:
            charm_updates = []
            stat_updates = []

            if card['charm_attack'] == 1:
                charm_updates.append("attack_count = CASE WHEN attack_count > 1 THEN attack_count - 1 ELSE 0 END")
                charm_updates.append("charm_attack = CASE WHEN attack_count > 1 THEN 1 ELSE 0 END")
                card['attack_stat'] = round(card['attack_stat'] * 1.2)

            if card['charm_defence'] == 1:
                charm_updates.append("defence_count = CASE WHEN defence_count > 1 THEN defence_count - 1 ELSE 0 END")
                charm_updates.append("charm_defence = CASE WHEN defence_count > 1 THEN 1 ELSE 0 END")
                card['defence_stat'] = round(card['defence_stat'] * 1.2)

            if card['charm_speed'] == 1:
                charm_updates.append("speed_count = CASE WHEN speed_count > 1 THEN speed_count - 1 ELSE 0 END")
                charm_updates.append("charm_speed = CASE WHEN speed_count > 1 THEN 1 ELSE 0 END")
                card['speed_stat'] = round(card['speed_stat'] * 1.2)

            if card['charm_health'] == 1:
                charm_updates.append("health_count = CASE WHEN health_count > 1 THEN health_count - 1 ELSE 0 END")
                charm_updates.append("charm_health = CASE WHEN health_count > 1 THEN 1 ELSE 0 END")
                card['health_stat'] = round(card['health_stat'] * 1.2)

            if charm_updates:
                update_query = "UPDATE user_cards SET "
                update_query += ", ".join(charm_updates)
                update_query += " WHERE user_id = %s AND card_id = %s"

                await execute_query_with_retry(update_query, (user_id, card['card_id']))

        return team_cards
       
    @commands.command(name="dungeonreset", hidden=True)
    async def dungeon_reset(self, ctx, discord_id: int):
        """
        Admin command to reset a user's dungeon state.
        Usage: sl dungeonreset <discord_id>
        """
        try:
            # Check if the command invoker is authorized
            authorized_users = ["1159962065432236162","818132161534230559", "1076303866854899762", "1204879494901407805","261887460114563072", "1045687344277246022", "411676136037416970","1231148282181587037","1263108476066992252"]
            
            if str(ctx.author.id) not in authorized_users:
                return  # Silently ignore if not authorized
            
            # Check if restricted users are in the correct server
            if str(ctx.author.id) in ["1045687344277246022", "411676136037416970","261887460114563072","1231148282181587037","1263108476066992252"]:
                if ctx.guild is None or ctx.guild.id != 1400127635765465240:
                    return  # Silently ignore if not in correct server
            
            # Check if the user exists in the database
            user_id = await self.get_user_id(discord_id)
            
            if not user_id:
                await safe_send(ctx, f"❌ User with Discord ID {discord_id} not found in the database.")
                return
                        
            # Check if user is in dungeons_in_progress before trying to remove
            if discord_id in self.dungeons_in_progress:
                self.dungeons_in_progress.discard(discord_id)
                        
            # Reset dungeon state in database
            update_dungeon_current_query = "UPDATE user SET dungeon_current = 0 WHERE user_id = %s"
            await execute_query_with_retry(update_dungeon_current_query, (user_id,))
                        
            # Also remove from fled_dungeons if present
            if discord_id in self.fled_dungeons:
                self.fled_dungeons.discard(discord_id)
                        
            await safe_send(ctx, f"✅ Successfully reset dungeon state for user with Discord ID {discord_id}.")
            
            # Send log embed to monitoring channel
            log_channel = self.bot.get_channel(1400128088603623525)
            if log_channel:
                embed = discord.Embed(
                    title="Dungeon Reset Command Used",
                    color=discord.Color.dark_red(),
                    timestamp=datetime.utcnow()
                )
                embed.add_field(name="Admin", value=f"<@{ctx.author.id}>", inline=True)
                embed.add_field(name="Target User", value=str(discord_id), inline=True)
                embed.add_field(name="Action", value="Dungeon State Reset", inline=True)
                embed.add_field(name="Channel", value=f"<#{ctx.channel.id}>", inline=True)
                embed.add_field(name="Server", value=ctx.guild.name if ctx.guild else "DM", inline=True)
                await safe_send(log_channel, embed=embed)
                    
        except Exception as e:
            logging.error(f"Error in dungeonreset command: {str(e)}")
            await safe_send(ctx, f"❌ An error occurred: {str(e)}")
            
    @commands.command(name="dungeondbstats")
    @commands.has_permissions(administrator=True)
    async def trade_db_stats(self, ctx):
        """Display database connection statistics for trading system"""
        metrics = get_metrics()
       
        embed = discord.Embed(
            title="Trading System Database Metrics",
            color=discord.Color.blue()
        )
       
        embed.add_field(name="Active Connections", value=str(metrics["active_connections"]), inline=True)
        embed.add_field(name="Peak Connections", value=str(metrics["peak_connections"]), inline=True)
        embed.add_field(name="Connection Errors", value=str(metrics["connection_errors"]), inline=True)
       
        embed.add_field(name="Total Queries", value=str(metrics["total_queries"]), inline=True)
        embed.add_field(name="Slow Queries", value=f"{metrics['slow_queries']} ({metrics['slow_query_percentage']:.1f}%)", inline=True)
        embed.add_field(name="Avg Query Time", value=f"{metrics['avg_query_time']:.2f}ms", inline=True)
       
        if metrics["last_error"]:
            embed.add_field(
                name="Last Error",
                value=f"{metrics['last_error']}\nTime: {metrics['last_error_time'].strftime('%Y-%m-%d %H:%M:%S')}",
                inline=False
            )
           
        await safe_send(ctx, embed=embed)
    # Add these methods to the DungeonCommands class

    def create_shield_bar(self, current_shield, max_shield):
        """Create a shield bar for S-Rank boss"""
        if max_shield == 0:
            return ""
        shield_percentage = min(100, (current_shield / max_shield) * 100)
        # keep the compact style (symbols only; language-neutral)
        return f"🛡️{max(0, current_shield)}/{max_shield}[**{shield_percentage:.0f}%**]"


    # --- Boss specials / turns / thresholds -------------------------------------

    async def apply_boss_special_effects(self, monster, dungeon_id, current_round, round_log, multilanguage: int = 0):
        """Apply special effects when encountering round 4 bosses"""
        special_messages = []
        if current_round == 4:
            if dungeon_id == 2 and monster['card_id'] == 63:  # C-Rank Buryura
                if 'buryura_buffed' not in monster:
                    monster['speed_stat'] = int(monster['speed_stat'] * 1.2)
                    monster['dodge_bonus'] = 30
                    monster['buryura_buffed'] = True
                    special_messages.append(_tr(
                        multilanguage,
                        "🌪️ **Buryura activates its agility enhancement!** Speed increased by 20% and dodge chance increased by 30%!",
                        "🌪️ **بوريوﻳرا تُفعِّل تعزيز الرشاقة!** زادت السرعة 20% وزادت فرصة التملّص 30%!",
                        "🌪️ **Buryura active son amélioration d’agilité !** Vitesse +20% et esquive +30% !",
                    ))
            elif dungeon_id == 3 and monster['card_id'] == 19:  # S-Rank Kargalgan
                if 'shield_health' not in monster:
                    shield_amount = int(monster['health_stat'] * 0.5)
                    monster['shield_health'] = shield_amount
                    monster['max_shield'] = shield_amount
                    special_messages.append(_tr(
                        multilanguage,
                        "🛡️ **Hymn of Protection!**",
                        "🛡️ **ترنيمة الحماية!**",
                        "🛡️ **Hymne de protection !**",
                    ))
        return special_messages


    async def handle_boss_turn_effects(self, monster, dungeon_id, current_round, monster_turn_count, round_log, multilanguage: int = 0):
        """Handle special effects that trigger on boss turns"""
        special_messages = []
        if current_round == 4:
            if dungeon_id == 6 and monster['card_id'] == 127:  # A-Rank
                if monster_turn_count % 3 == 0:
                    hp_pct = (monster['current_health'] / monster['health_stat']) * 100
                    if hp_pct <= 20 and not hasattr(monster, 'soldiers_redemption_applied'):
                        monster['attack_stat'] = int(monster['attack_stat'] * 1.2)
                        monster['soldiers_redemption_applied'] = True
                        special_messages.append(_tr(
                            multilanguage,
                            "⚔️ **Soldier's Redemption activated!** Attack power increased by 20%!",
                            "⚔️ **خلاص الجندي مُفعّل!** زادت قوة الهجوم 20%!",
                            "⚔️ **Rédemption du soldat activée !** Attaque +20% !",
                        ))
                    special_messages.append(_tr(
                        multilanguage,
                        "🔥 **White Flare!** The boss unleashes a devastating attack!",
                        "🔥 **الوهج الأبيض!** الوحش يطلق هجوماً مدمراً!",
                        "🔥 **Flamboiement blanc !** Le boss déclenche une attaque dévastatrice !",
                    ))
                    return special_messages, 1.7
            elif dungeon_id == 3 and monster['card_id'] == 19:  # S-Rank
                if not hasattr(monster, 'shield_health') or monster['shield_health'] <= 0:
                    if monster_turn_count % 1 == 0:
                        special_messages.append(_tr(
                            multilanguage,
                            "🔥 **Hymn of Blazing Fire!**",
                            "🔥 **ترنيمة النار المتأججة!**",
                            "🔥 **Hymne du feu flamboyant !**",
                        ))
                        return special_messages, 1.7
        return special_messages, 1.0


    async def handle_user_turn_effects(self, user_card, monster, dungeon_id, current_round, user_turn_count, round_log, multilanguage: int = 0):
        """Handle special effects that trigger on user turns"""
        special_messages = []
        card_defeated_or_skip = False
        if current_round == 4:
            if dungeon_id == 4 and monster['card_id'] == 65:  # D-Rank Kasaka
                health_loss = int(user_card['health_stat'] * 0.05)
                user_card['current_health'] = max(0, user_card['current_health'] - health_loss)
                special_messages.append(_tr(
                    multilanguage,
                    f"💀 Toxic aura drains {user_card['card_name'][:15]} for {health_loss} damage!",
                    f"💀 الهالة السامة تستنزف {user_card['card_name'][:15]} بـ {health_loss} ضرر!",
                    f"💀 L’aura toxique draine {user_card['card_name'][:15]} de {health_loss} PV !",
                ))
                if user_card['current_health'] <= 0:
                    special_messages.append(_tr(
                        multilanguage,
                        f"💀 {user_card['card_name'][:15]} defeated by toxic aura!",
                        f"💀 {user_card['card_name'][:15]} سقط بسبب الهالة السامة!",
                        f"💀 {user_card['card_name'][:15]} est vaincu(e) par l’aura toxique !",
                    ))
                    card_defeated_or_skip = True
            elif dungeon_id == 5 and monster['card_id'] == 54:  # B-Rank (freeze)
                if user_turn_count % 5 == 0:
                    special_messages.append(_tr(
                        multilanguage,
                        f"❄️ {user_card['card_name'][:15]} is frozen and cannot act!",
                        f"❄️ {user_card['card_name'][:15]} مُجمّد ولا يمكنه التحرك!",
                        f"❄️ {user_card['card_name'][:15]} est gelé(e) et ne peut pas agir !",
                    ))
                    card_defeated_or_skip = True
        return special_messages, card_defeated_or_skip


    def handle_boss_health_threshold(self, monster, dungeon_id, current_round, round_log, multilanguage: int = 0):
        """Handle special effects when boss health drops below 20%"""
        special_messages = []
        if current_round == 4:
            hp_pct = (monster['current_health'] / monster['health_stat']) * 100
            if hp_pct <= 20:
                if dungeon_id == 1 and monster['card_id'] == 116:
                    if 'last_attempt_activated' not in monster:
                        monster['attack_stat'] = int(monster['attack_stat'] * 1.2)
                        monster['last_attempt_activated'] = True
                        special_messages.append(_tr(
                            multilanguage,
                            "⚡ **Hobgoblin activates LAST ATTEMPT!** Attack power increased by 20%!",
                            "⚡ **الهوبغوبلن يُفعّل آخر محاولة!** زادت قوة الهجوم 20%!",
                            "⚡ **Le Hobgobelin active DERNIÈRE TENTATIVE !** Attaque +20% !",
                        ))

                elif dungeon_id == 6 and monster['card_id'] == 127:
                    if 'soldiers_redemption_applied' not in monster:
                        monster['attack_stat'] = int(monster['attack_stat'] * 1.2)
                        monster['soldiers_redemption_applied'] = True
                        special_messages.append(_tr(
                            multilanguage,
                            "⚔️ **Soldier's Redemption activated!** Attack power increased by 20%!",
                            "⚔️ **خلاص الجندي مُفعّل!** زادت قوة الهجوم 20%!",
                            "⚔️ **Rédemption du soldat activée !** Attaque +20% !",
                        ))

                elif dungeon_id == 3 and monster['card_id'] == 19:
                    if 'hymn_strengthening_applied' not in monster:
                        monster['speed_stat'] = int(monster['speed_stat'] * 1.3)
                        monster['defence_stat'] = int(monster['defence_stat'] * 1.3)
                        monster['attack_stat'] = int(monster['attack_stat'] * 1.3)
                        monster['hymn_strengthening_applied'] = True
                        special_messages.append(_tr(
                            multilanguage,
                            "💪 **Hymn of Strengthening!**",
                            "💪 **ترنيمة التقوية!**",
                            "💪 **Hymne de renforcement !**",
                        ))
        return special_messages


    def handle_shield_damage(self, monster, damage, multilanguage: int = 0):
        """Handle damage to S-Rank boss shield"""
        if hasattr(monster, 'shield_health') and monster['shield_health'] > 0:
            shield_damage = min(damage, monster['shield_health'])
            monster['shield_health'] -= shield_damage
            remaining = damage - shield_damage
            monster['shield_damage_taken'] = shield_damage
            if monster['shield_health'] <= 0:
                monster['current_health'] = max(0, monster['current_health'] - remaining)
                return remaining, _tr(
                    multilanguage,
                    "🛡️ **Shield Broken!** Kargalgan's protection has been shattered!",
                    "🛡️ **تحطّم الدرع!** حماية كارغالغان تحطّمت!",
                    "🛡️ **Bouclier brisé !** La protection de Kargalgan est anéantie !",
                )
            else:
                return 0, _tr(
                    multilanguage,
                    f"🛡️ Shield absorbed {shield_damage} damage!",
                    f"🛡️ الدرع امتصّ {shield_damage} ضررًا!",
                    f"🛡️ Le bouclier a absorbé {shield_damage} dégâts !",
                )
        return damage, ""


    # --- Loot / skip -------------------------------------------------------------


    async def handle_rare_weapon_drop(self, ctx, dungeon_id, user_id=None, inventory_id=None, notify: bool = True):
        """
        Handle rare weapon drops with improved randomization and tracking.
        Uses secrets module for cryptographically secure randomness.
        """
        try:
            if user_id is None:
                user_id = await self.get_user_id(ctx.author.id)
            if inventory_id is None:
                inventory_id = await self.get_inventory_id(user_id)
            
            secure_random = secrets.SystemRandom()
            drop_chance = 0.01  # 1% chance
            random_roll = secure_random.random()
            
            logging.info(f"[RARE DROP] User {ctx.author.id} rolled {random_roll:.4f} (need < {drop_chance}) for dungeon_id {dungeon_id}")
            
            if random_roll < drop_chance:
                weapon_rows = await execute_query_with_retry(
                    """
                    SELECT weapon_id
                    FROM dungeon_weapon
                    WHERE dungeon_id = %s
                    """,
                    (dungeon_id,)
                )
                if not weapon_rows:
                    return None

                weapon_id = secure_random.choice([row["weapon_id"] for row in weapon_rows])
                await add_inventory_quantity("inventory_weapon", inventory_id, weapon_id, 1)
                logging.info(f"[RARE DROP SUCCESS] User {ctx.author.id} got weapon_id {weapon_id}")

                weapon_details = await execute_query_with_retry(
                    "SELECT weapon_name, weapon_rank FROM weapon WHERE weapon_id = %s",
                    (weapon_id,)
                )
                
                if weapon_details:
                    weapon_drop = {
                        "weapon_id": weapon_id,
                        "weapon_name": weapon_details[0].get("weapon_name", "Rare Weapon"),
                        "weapon_rarity": weapon_details[0].get("weapon_rank", "Unknown"),
                    }
                else:
                    weapon_drop = {
                        "weapon_id": weapon_id,
                        "weapon_name": "Rare Weapon",
                        "weapon_rarity": "Unknown",
                    }

                if notify:
                    embed = discord.Embed(
                        title="✨ Rare Weapon Drop!",
                        description=f"You obtained a rare weapon: **{weapon_drop['weapon_name']}** ({weapon_drop['weapon_rarity']})!",
                        color=0xFFD700
                    )
                    await safe_send(ctx, embed=embed)
                return weapon_drop
            else:
                logging.debug(f"[RARE DROP FAIL] User {ctx.author.id} rolled {random_roll:.4f} (no drop)")
                return None
                    
        except Exception as e:
            logging.error(f"Error in handle_rare_weapon_drop: {str(e)}")
            import traceback
            traceback.print_exc()
            if not notify:
                raise
            return None


    # --- Skills: fetch / apply / triggers ---------------------------------------

    async def apply_buff_skill(self, card, skill_details, skill_messages, multilanguage: int = 0):
        """Apply buff/debuff effects to card stats"""
        skill_name = card['skill_state']['skill_info']['skill_name']
        if card['skill_state'].get('buff_applied', False):
            return

        def add(delta, key, up_emo, down_emo, stat_label_en, stat_label_ar, stat_label_fr):
            if delta != 0:
                # apply
                card[key] = int(card[key] * (1 + delta / 100))
                # message
                if delta > 0:
                    skill_messages.append(_tr(
                        multilanguage,
                        f"{up_emo} **{skill_name}** activated! {card['card_name'][:15]}'s {stat_label_en} increased by {delta}%!",
                        f"{up_emo} **{skill_name}** تم تفعيله! {stat_label_ar} {card['card_name'][:15]} زادت بنسبة {delta}%!",
                        f"{up_emo} **{skill_name}** activée ! {stat_label_fr} de {card['card_name'][:15]} augmentée de {delta}% !",
                    ))
                else:
                    skill_messages.append(_tr(
                        multilanguage,
                        f"💀 **{skill_name}** activated! {card['card_name'][:15]}'s {stat_label_en} decreased by {abs(delta)}%!",
                        f"💀 **{skill_name}** تم تفعيله! {stat_label_ar} {card['card_name'][:15]} انخفضت بنسبة {abs(delta)}%!",
                        f"💀 **{skill_name}** activée ! {stat_label_fr} de {card['card_name'][:15]} diminuée de {abs(delta)}% !",
                    ))

        add(skill_details['attack_stat'], 'attack_stat', "⚡", "💀", "attack", "هجوم", "l’attaque")
        add(skill_details['defense_stat'], 'defence_stat', "🛡️", "💀", "defense", "دفاع", "la défense")
        add(skill_details['speed_stat'], 'speed_stat', "💨", "💀", "speed", "سرعة", "la vitesse")

        if skill_details['health_stat'] != 0:
            delta = skill_details['health_stat']
            old_max = card['health_stat']
            card['health_stat'] = int(card['health_stat'] * (1 + delta / 100))
            if delta > 0:
                card['current_health'] = min(max(0, card.get('current_health', 0)), card['health_stat'])
                skill_messages.append(_tr(
                    multilanguage,
                    f"❤️ **{skill_name}** activated! {card['card_name'][:15]}'s health increased by {delta}%!",
                    f"❤️ **{skill_name}** تم تفعيله! صحة {card['card_name'][:15]} زادت بنسبة {delta}%!",
                    f"❤️ **{skill_name}** activée ! La santé de {card['card_name'][:15]} a augmenté de {delta}% !",
                ))
            else:
                # cap current HP to new max
                card['current_health'] = min(max(0, card.get('current_health', 0)), card['health_stat'])
                skill_messages.append(_tr(
                    multilanguage,
                    f"💀 **{skill_name}** activated! {card['card_name'][:15]}'s health decreased by {abs(delta)}%!",
                    f"💀 **{skill_name}** تم تفعيله! صحة {card['card_name'][:15]} انخفضت بنسبة {abs(delta)}%!",
                    f"💀 **{skill_name}** activée ! La santé de {card['card_name'][:15]} a diminué de {abs(delta)}% !",
                ))

        card['skill_state']['buff_applied'] = True


    async def handle_turn_based_skills(self, card, is_user_turn, round_log, multilanguage: int = 0):
        """Handle skills that activate based on turn counters"""
        skill_messages = []
        bonus_damage = 0
        if 'skill_state' not in card:
            return skill_messages, bonus_damage

        skill_info = card['skill_state']['skill_info']
        skill_details = card['skill_state']['skill_details']
        skill_name = skill_info['skill_name']
        sc = skill_info['skill_category_id']
        if sc not in [1, 4, 5] or 'turn' not in skill_details:
            return skill_messages, bonus_damage

        card['skill_state']['turn_counter'] += 1
        current_turn = card['skill_state']['turn_counter']

        if sc == 1:
            await self.apply_buff_skill(card, skill_details, skill_messages, multilanguage)
            card['skill_state']['turn_counter'] = 0
        elif sc == 4 and current_turn >= skill_details['turn']:
            bonus_damage = skill_details['damage']
            skill_messages.append(_tr(
                multilanguage,
                f"⚔️ **{skill_name}** triggers! Additional {bonus_damage} flat damage!",
                f"⚔️ **{skill_name}** يتفعّل! ضرر ثابت إضافي {bonus_damage}!",
                f"⚔️ **{skill_name}** se déclenche ! {bonus_damage} dégâts plats supplémentaires !",
            ))
            card['skill_state']['turn_counter'] = 0
        elif sc == 5 and current_turn >= skill_details['turn']:
            base_attack = card['attack_stat']
            if skill_details['attack_stat'] != 0:
                bonus_damage = int(base_attack * (skill_details['attack_stat'] / 100))
                skill_messages.append(_tr(
                    multilanguage,
                    f"💥 **{skill_name}** triggers! {skill_details['attack_stat']}% attack bonus ({bonus_damage} damage)!",
                    f"💥 **{skill_name}** يتفعّل! {skill_details['attack_stat']}% من الهجوم ({bonus_damage} ضرر)!",
                    f"💥 **{skill_name}** se déclenche ! Bonus d’attaque {skill_details['attack_stat']}% ({bonus_damage} dégâts) !",
                ))
                card['skill_state']['turn_counter'] = 0

        return skill_messages, bonus_damage


    async def handle_defensive_skills(self, defender, attacker, round_log, multilanguage: int = 0):
        """Handle defensive skills like dodge and paralysis"""
        skill_messages, dodge_success, paralysis_success = [], False, False
        if 'skill_state' not in defender or 'skill_info' not in defender['skill_state']:
            return skill_messages, dodge_success, paralysis_success

        skill_info = defender['skill_state']['skill_info']
        skill_details = defender['skill_state']['skill_details']
        skill_name = skill_info['skill_name']

        if skill_info['skill_category_id'] == 3:
            chance = skill_details['chance']
            if random.random() * 100 < chance:
                skill_messages.append(_tr(
                    multilanguage,
                    f"🌪️ **{skill_name}** activates! {defender['card_name'][:15]} dodges with {chance}% chance!",
                    f"🌪️ **{skill_name}** يتفعّل! {defender['card_name'][:15]} يتفادى بنسبة {chance}%!",
                    f"🌪️ **{skill_name}** s’active ! {defender['card_name'][:15]} esquive avec {chance}% de chance !",
                ))
                dodge_success = True

        if skill_info['skill_category_id'] == 2:
            chance = skill_details['chance']
            if random.random() * 100 < chance:
                skill_messages.append(_tr(
                    multilanguage,
                    f"⚡ **{skill_name}** activates! {attacker['card_name'][:15]} is paralyzed ({chance}% chance)!",
                    f"⚡ **{skill_name}** يتفعّل! {attacker['card_name'][:15]} مُشلّ ({chance}% احتمالية)!",
                    f"⚡ **{skill_name}** s’active ! {attacker['card_name'][:15]} est paralysé(e) ({chance}% de chance) !",
                ))
                paralysis_success = True

        return skill_messages, dodge_success, paralysis_success


    async def handle_poison_skill(self, target_card, poison_skill_details, multilanguage: int = 0):
        """Handle poison DoT with stacking; poison does NOT damage shields. 
        It starts damaging HP only after the shield is broken."""
        skill_messages = []
        if target_card.get('skill_state', {}).get('poison_active', False):
            # Initialize stacking if missing
            if 'current_poison_percent' not in target_card['skill_state']:
                target_card['skill_state']['current_poison_percent'] = 2.0

            max_poison_percent = poison_skill_details['percent_damage']
            current_poison_percent = target_card['skill_state']['current_poison_percent']

            # ✅ If a shield is up, DO NOT deal any poison damage; only stack the poison.
            if target_card.get('shield_health', 0) > 0:
                # Still grow the stack up to the cap while shield persists
                if current_poison_percent < max_poison_percent:
                    target_card['skill_state']['current_poison_percent'] = min(
                        current_poison_percent + 2.0, max_poison_percent
                    )
                skill_messages.append(_tr(
                    multilanguage,
                    "☠️ The poison can’t penetrate the shield. It will start damaging once the shield breaks.",
                    "☠️ السم لا يخترق الدرع. سيبدأ بإلحاق الضرر عند تحطم الدرع.",
                    "☠️ Le poison ne traverse pas le bouclier. Il commencera à infliger des dégâts une fois le bouclier brisé."
                ))
                return skill_messages

            # No shield → apply poison damage to HP
            poison_damage = int(target_card['health_stat'] * (current_poison_percent / 100))
            target_card['current_health'] = max(0, target_card['current_health'] - poison_damage)
            skill_messages.append(_tr(
                multilanguage,
                f"☠️ **Poison** deals {poison_damage} damage ({int(current_poison_percent)}%) to {target_card['card_name'][:15]}!",
                f"☠️ **السم** يُلحق {poison_damage} ضررًا ({int(current_poison_percent)}%) بـ {target_card['card_name'][:15]}!",
                f"☠️ **Poison** inflige {poison_damage} dégâts ({int(current_poison_percent)}%) à {target_card['card_name'][:15]} !",
            ))

            # Increase stack after tick (up to cap)
            if current_poison_percent < max_poison_percent:
                target_card['skill_state']['current_poison_percent'] = min(
                    current_poison_percent + 2.0, max_poison_percent
                )

        return skill_messages



    async def apply_shield_skill(self, card, skill_details, skill_messages, multilanguage: int = 0):
        """Apply shield effects to card"""
        if 'skill_info' not in card['skill_state']:
            return
        skill_name = card['skill_state']['skill_info']['skill_name']
        if card['skill_state'].get('shield_applied', False):
            return
        shield_percentage = skill_details.get('shield_amount', 0)
        shield_amount = int((card['health_stat'] * shield_percentage) / 100)
        card['shield_health'] = shield_amount
        card['max_shield'] = shield_amount
        skill_messages.append(_tr(
            multilanguage,
            f"🛡️ **{skill_name}** activated! {card['card_name'][:15]} gains {shield_amount} shield!",
            f"🛡️ **{skill_name}** تم تفعيله! {card['card_name'][:15]} يحصل على درع بقيمة {shield_amount}!",
            f"🛡️ **{skill_name}** activée ! {card['card_name'][:15]} gagne {shield_amount} de bouclier !",
        ))
        card['skill_state']['shield_applied'] = True


    async def handle_summon_skills(self, card, user_id, round_log, multilanguage: int = 0):
        """Handle summon skill activation"""
        skill_messages = []
        if 'skill_state' not in card:
            return skill_messages, []
        skill_info = card['skill_state']['skill_info']
        skill_details = card['skill_state']['skill_details']
        skill_name = skill_info['skill_name']

        if skill_info['skill_category_id'] == 7 and not card['skill_state'].get('summons_created', False):
            summons, quantity = [], skill_details['quantity']
            for i in range(quantity):
                summon = {
                    'card_name': f"Summon {i+1}",
                    'card_id': f"summon_{card['card_id']}_{i}",
                    'is_summon': True,
                    'parent_card_id': card['card_id'],
                    'card_image': card.get('card_image', '')
                }
                if skill_details.get('health_percentage', 0) > 0:
                    summon['health_stat'] = int(card['health_stat'] * (skill_details['health_percentage'] / 100))
                else:
                    summon['health_stat'] = skill_details.get('health_stat', 100)
                if skill_details.get('attack_percentage', 0) > 0:
                    summon['attack_stat'] = int(card['attack_stat'] * (skill_details['attack_percentage'] / 100))
                else:
                    summon['attack_stat'] = skill_details.get('attack_stat', 50)
                if skill_details.get('defense_percentage', 0) > 0:
                    summon['defence_stat'] = int(card['defence_stat'] * (skill_details['defense_percentage'] / 100))
                else:
                    summon['defence_stat'] = skill_details.get('defense_stat', 30)
                if skill_details.get('speed_percentage', 0) > 0:
                    summon['speed_stat'] = int(card['speed_stat'] * (skill_details['speed_percentage'] / 100))
                else:
                    summon['speed_stat'] = skill_details.get('speed_stat', 40)
                summon['current_health'] = summon['health_stat']
                summons.append(summon)

            card['skill_state']['summons'] = summons
            card['skill_state']['summons_created'] = True
            skill_messages.append(_tr(
                multilanguage,
                f"🌟 **{skill_name}** activated! {card['card_name'][:15]} summons {quantity} ally/allies!",
                f"🌟 **{skill_name}** تم تفعيله! {card['card_name'][:15]} يستدعي {quantity} من الحلفاء!",
                f"🌟 **{skill_name}** activée ! {card['card_name'][:15]} invoque {quantity} allié(s) !",
            ))
        return skill_messages, card['skill_state'].get('summons', [])

    async def initialize_round_skills(self, user_id, user_team, round_log):
        """Initialize skills at the start of each round"""
        skill_messages = []
        print("init round")
        for card in user_team:
            # Check if card has a skill
            skill_info = await self.get_user_card_skills(user_id, card['card_id'])
            if not skill_info:
                print("no skill info??")
                continue
           
            skill_details = await self.get_skill_details(skill_info['skill_id'], skill_info['skill_category_id'])
            if not skill_details:
                print("no skill details ?")
                continue
           
            # Initialize skill state for this card
            if 'skill_state' not in card:
                print("state")
                card['skill_state'] = {
                    'skill_info': skill_info,
                    'skill_details': skill_details,
                    'turn_counter': 0,
                    'active_buffs': {},
                    'active_debuffs': {},
                    'poison_active': False,
                    'shield_amount': 0,
                    'dodge_bonus': 0,
                    'paralysis_chance': 0,
                    'summons': []
                }
            print("i'm init")
            # Handle Buff/Debuff skills (category 1) - activate once per round
            if skill_info['skill_category_id'] == 1 and skill_details.get('turn', 0) == 0:
                print("false or true ")
                if not card['skill_state'].get('buff_applied_this_round', False):
                    print("category got")
                    await self.apply_buff_skill(card, skill_details, skill_messages)
                    card['skill_state']['buff_applied_this_round'] = True
       
            # Handle Shield skills (category 8) - activate once per round  
            elif skill_info['skill_category_id'] == 8 and skill_details.get('turn', 0) == 0:
                if not card['skill_state'].get('shield_applied_this_round', False):
                    await self.apply_shield_skill(card, skill_details, skill_messages)
                    card['skill_state']['shield_applied_this_round'] = True
   
        return skill_messages
    def reset_round_skill_states(self, user_team):
        """Reset skill states at the start of each round (no localization needed)"""
        for card in user_team:
            if 'skill_state' in card:
                if card['skill_state']['skill_info']['skill_category_id'] not in [1, 8]:
                    card['skill_state']['turn_counter'] = 0
                card['skill_state']['buff_applied_this_round'] = False
                card['skill_state']['shield_applied_this_round'] = False
                card['skill_state']['poison_active'] = False
    async def get_user_card_skills(self, user_id, card_id):
        """Get skill information for a specific user card"""
        try:
            skill_query = """
            SELECT uc.skill_id, s.skill_name, s.skill_category_id, sc.category_name
            FROM user_cards uc
            JOIN skill s ON uc.skill_id = s.skill_id
            JOIN skill_category sc ON s.skill_category_id = sc.skill_category_id
            WHERE uc.user_id = %s AND uc.card_id = %s AND uc.skill_id IS NOT NULL
            """
            result = await execute_query_with_retry(skill_query, (user_id, card_id))
            print("results check")
            print(result)
            return result[0] if result else None
        except Exception as e:
            logging.error(f"Error getting user card skills: {e}")
            return None

    async def get_skill_details(self, skill_id, skill_category_id):
        """Get detailed skill information based on category"""
        try:
            skill_details = {}
           
            if skill_category_id == 1:  # Buff/Debuff
                query = """
                SELECT attack_stat, health_stat, defense_stat, speed_stat, turn
                FROM skill_buff WHERE skill_id = %s
                """
            elif skill_category_id == 2:  # Paralysis
                query = """
                SELECT turn, chance FROM skill_paralysis WHERE skill_id = %s
                """
            elif skill_category_id == 3:  # Dodge
                query = """
                SELECT turn, chance FROM skill_dodge WHERE skill_id = %s
                """
            elif skill_category_id == 4:  # Flat Damage
                query = """
                SELECT damage, turn FROM skill_flat WHERE skill_id = %s
                """
            elif skill_category_id == 5:  # Percentage Damage
                query = """
                SELECT attack_stat, health_stat, defense_stat, speed_stat, turn
                FROM skill_percent WHERE skill_id = %s
                """
            elif skill_category_id == 6:  # Poison
                query = """
                SELECT percent_damage FROM skill_poison WHERE skill_id = %s
                """
            elif skill_category_id == 7:  # Summon
                query = """
                SELECT attack_stat, health_stat, defense_stat, speed_stat,
                    attack_percentage, health_percentage, defense_percentage, speed_percentage,
                    quantity FROM skill_summon WHERE skill_id = %s
                """
            elif skill_category_id == 8:  # Shield
                query = """
                SELECT *
                FROM skill_shield WHERE skill_id = %s
                """
            else:
                return None
               
            result = await execute_query_with_retry(query, (skill_id,))
            print("result skills")
            print(result)
            return result[0] if result else None
        except Exception as e:
            logging.error(f"Error getting skill details: {e}")
            return None
    async def handle_dungeon_skip_check(self, ctx, current_round):
        """Check and handle dungeon skip after round 1 completion"""
        try:
            user_id = await self.get_user_id(ctx.author.id)
           
            # Check dungeon_skip status
            skip_query = "SELECT dungeon_skip, dungeon_rounds FROM user WHERE user_id = %s"
            skip_result = await execute_query_with_retry(skip_query, (user_id,))
           
            if skip_result:
                dungeon_skip = skip_result[0]['dungeon_skip']
                dungeon_round = skip_result[0]['dungeon_rounds']
               
                if dungeon_skip == 1:
                    new_dungeon_round = dungeon_round - 1
                   
                    if new_dungeon_round <= 0:
                        # Reset both dungeon_skip and dungeon_round to 0
                        update_query = """
                        UPDATE user
                        SET dungeon_skip = 0, dungeon_rounds = 0
                        WHERE user_id = %s
                        """
                        await execute_query_with_retry(update_query, (user_id,))
                    else:
                        # Just decrease dungeon_round
                        update_query = """
                        UPDATE user
                        SET dungeon_rounds = %s
                        WHERE user_id = %s
                        """
                        await execute_query_with_retry(update_query, (new_dungeon_round, user_id,))
                   
                    return True  # Skip to boss round
           
            return False  # Continue normal progression
           
        except Exception as e:
            print(f"Error in handle_dungeon_skip_check: {e}")
            return False
async def setup(bot):
    await bot.add_cog(DungeonCommands(bot))
