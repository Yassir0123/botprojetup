import discord
import os
from discord.ext import commands
import random
import math
from datetime import datetime, timedelta
import asyncio
import time
import re
from utils.database import execute_query_with_retry, get_metrics
from utils.db_atomic import advisory_lock_for_context, db_transaction
import functools

# NOTE:
# Assumes these helpers exist in your project:
#   - _tr(lang, en_text, ar_text, fr_text)
#   - getlang(user_or_author_id: int) -> int
#   - get_user_multilanguage(user_or_author_id: int)  # available if needed
def _tr(lang: int, en: str, ar: str, fr: str) -> str:
    if lang == 1:
        return ar
    if lang == 3:
        return fr
    return en
LANG_NAMES = {
    0: "English",
    1: "Arabic",
    3: "French",
}
async def get_user_multilanguage(user_authorid: int) -> int:
    """Fetch user's language code from DB (0=en, 1=ar, 3=fr)."""
    rows = await execute_query_with_retry(
        "SELECT multilanguage FROM user WHERE user_authorid = %s",
        (str(user_authorid),)
    )
    if rows and rows[0].get("multilanguage") is not None:
        return int(rows[0]["multilanguage"])
    return 0

# Alias as requested
async def getlang(user_authorid: int) -> int:
    return await get_user_multilanguage(user_authorid)
class SkillCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.image_path = os.path.join(os.path.dirname(__file__), '..', 'images')
        self.user_cooldowns = {}  # Store user cooldowns
        self.COOLDOWN_TIME = 2  # Cooldown time in seconds

    @commands.command(name='sequip', aliases=["seq","ske"])
    @advisory_lock_for_context("slbot:skill")
    async def skill_equip(self, ctx, *, args=None):
        """Equip a skill to a card (multilanguage)"""
        lang = await getlang(ctx.author.id)

        if not args or ',' not in args:
            await ctx.send(_tr(
                lang,
                "❌ Please use the format: `sl sequip skill_name, card_name`",
                "❌ يرجى استخدام الصيغة: `sl sequip skill_name, card_name`",
                "❌ Veuillez utiliser le format : `sl sequip skill_name, card_name`"
            ))
            return
        
        try:
            skill_name, card_name = [arg.strip() for arg in args.split(',', 1)]
        except ValueError:
            await ctx.send(_tr(
                lang,
                "❌ Please use the format: `sl sequip skill_name, card_name`",
                "❌ يرجى استخدام الصيغة: `sl sequip skill_name, card_name`",
                "❌ Veuillez utiliser le format : `sl sequip skill_name, card_name`"
            ))
            return

        # Get user info
        user_query = """
        SELECT user_id, fight_current, dungeon_current 
        FROM user 
        WHERE user_authorid = %s
        """
        user_result = await execute_query_with_retry(user_query, (str(ctx.author.id),))
        
        if not user_result:
            await ctx.send(_tr(
                lang,
                "❌ You are not registered! Please type `sl register` to get started.",
                "❌ لست مسجلاً! اكتب `sl register` للبدء.",
                "❌ Vous n'êtes pas enregistré ! Tapez `sl register` pour commencer."
            ))
            return
        user_id = user_result[0]["user_id"]
        fight_current = user_result[0]["fight_current"]
        dungeon_current = user_result[0]["dungeon_current"]
        
        # Check if user is in fight or dungeon
        if fight_current == 1 or dungeon_current == 1:
            await ctx.send(_tr(
                lang,
                "❌ You cannot equip skills while in a fight or dungeon!",
                "❌ لا يمكنك تجهيز المهارات أثناء القتال أو في الزنزانة!",
                "❌ Vous ne pouvez pas équiper de compétences pendant un combat ou dans un donjon !"
            ))
            return
        
        # Get inventory_id
        inventory_query = "SELECT inventory_id FROM inventory WHERE user_id = %s"
        inventory_result = await execute_query_with_retry(inventory_query, (user_id,))
        
        if not inventory_result:
            await ctx.send(_tr(
                lang,
                "❌ Inventory not found!",
                "❌ لم يتم العثور على المخزون!",
                "❌ Inventaire introuvable !"
            ))
            return
        
        inventory_id = inventory_result[0]["inventory_id"]
        
        # Find skill by name
        skill_query = """
        SELECT skill_id, skill_name, card_class 
        FROM skill 
        WHERE skill_name LIKE %s
        """
        skill_results = await execute_query_with_retry(skill_query, (f"%{skill_name}%",))
        
        if not skill_results:
            await ctx.send(_tr(
                lang,
                f"❌ No skill found with name containing '{skill_name}'",
                f"❌ لم يتم العثور على مهارة تحتوي على الاسم '{skill_name}'",
                f"❌ Aucune compétence trouvée contenant le nom '{skill_name}'"
            ))
            return
        
        if len(skill_results) > 1:
            embed = discord.Embed(
                title=_tr(lang, "Multiple Skills Found", "تم العثور على عدة مهارات", "Plusieurs compétences trouvées"),
                description=_tr(
                    lang,
                    "I found multiple skills matching your search. Please specify which one you'd like to use:",
                    "وجدتُ عدة مهارات تطابق بحثك. يرجى تحديد أيّها تريد استخدامه:",
                    "J'ai trouvé plusieurs compétences correspondant à votre recherche. Veuillez préciser laquelle utiliser :"
                ),
                color=discord.Color.blue()
            )
            skill_list = "\n".join([f"• {skill['skill_name']}" for skill in skill_results])
            embed.add_field(name=_tr(lang, "Skills:", "المهارات:", "Compétences :"), value=skill_list, inline=False)
            embed.set_footer(text=_tr(
                lang,
                "Please use the command again with the exact name of the skill you want to use.",
                "يرجى إعادة استخدام الأمر مع الاسم الدقيق للمهارة التي تريد استخدامها.",
                "Veuillez réutiliser la commande avec le nom exact de la compétence."
            ))
            await ctx.send(embed=embed)
            return
        skill_results = skill_results[0]
        skill_id = skill_results["skill_id"]
        found_skill_name = skill_results["skill_name"]
        skill_class = skill_results["card_class"]
        
        # Find user's card by name
        card_query = """
        SELECT uc.card_id, uc.skill_id, uc.name
        FROM user_cards uc
        WHERE uc.user_id = %s AND uc.name LIKE %s
        """
        card_results = await execute_query_with_retry(card_query, (user_id, f"%{card_name}%"))
        
        if not card_results:
            await ctx.send(_tr(
                lang,
                f"❌ You don't have any card with name containing '{card_name}'",
                f"❌ ليس لديك أي بطاقة يحتوي اسمها على '{card_name}'",
                f"❌ Vous n'avez aucune carte dont le nom contient '{card_name}'"
            ))
            return
        
        if len(card_results) > 1:
            embed = discord.Embed(
                title=_tr(lang, "Multiple Cards Found", "تم العثور على عدة بطاقات", "Plusieurs cartes trouvées"),
                description=_tr(
                    lang,
                    "I found multiple cards matching your search. Please specify which one you'd like to use:",
                    "وجدتُ عدة بطاقات تطابق بحثك. يرجى تحديد أيّها تريد استخدامها:",
                    "J'ai trouvé plusieurs cartes correspondant à votre recherche. Veuillez préciser laquelle utiliser :"
                ),
                color=discord.Color.blue()
            )
            card_list = "\n".join([f"• {card['name']}" for card in card_results])
            embed.add_field(name=_tr(lang, "Your Cards:", "بطاقاتك:", "Vos cartes :"), value=card_list, inline=False)
            embed.set_footer(text=_tr(
                lang,
                "Please use the command again with the exact name of the card you want to use.",
                "يرجى إعادة استخدام الأمر مع الاسم الدقيق للبطاقة التي تريد استخدامها.",
                "Veuillez réutiliser la commande avec le nom exact de la carte."
            ))
            await ctx.send(embed=embed)
            return
        card_results = card_results[0]
        user_card_id = card_results["card_id"]
        current_skill_id = card_results["skill_id"]
        found_card_name = card_results["name"]
        
        # Get card class from card table
        card_class_query = "SELECT card_class_id FROM card WHERE card_id = %s"
        card_class_result = await execute_query_with_retry(card_class_query, (user_card_id,))
        
        if not card_class_result:
            await ctx.send(_tr(
                lang,
                "❌ Card information not found!",
                "❌ لم يتم العثور على معلومات البطاقة!",
                "❌ Informations de la carte introuvables !"
            ))
            return
        
        card_class_id = card_class_result[0]["card_class_id"]
        
        # Check if skill class matches card class
        if skill_class != card_class_id:
            await ctx.send(_tr(
                lang,
                "❌ Cannot equip this skill! The skill class doesn't match your card's class.",
                "❌ لا يمكن تجهيز هذه المهارة! فئة المهارة لا تطابق فئة بطاقتك.",
                "❌ Impossible d'équiper cette compétence ! La classe de la compétence ne correspond pas à celle de votre carte."
            ))
            return
        
        # Check if card already has a skill
        if current_skill_id != 0:
            await ctx.send(_tr(
                lang,
                "❌ Your card already has a skill equipped! Please unequip it first.",
                "❌ بطاقتك لديها مهارة مجهزة بالفعل! يرجى إزالة تجهيزها أولاً.",
                "❌ Votre carte a déjà une compétence équipée ! Veuillez d'abord la déséquiper."
            ))
            return
        
        # Check if user has the skill in inventory
        inventory_skill_query = """
        SELECT quantiter 
        FROM inventory_skills 
        WHERE inventory_id = %s AND skill_id = %s
        """
        inventory_skill_result = await execute_query_with_retry(inventory_skill_query, (inventory_id, skill_id))
        
        if not inventory_skill_result:
            await ctx.send(_tr(
                lang,
                f"❌ You don't have the skill '{found_skill_name}' in your inventory!",
                f"❌ لا تملك المهارة '{found_skill_name}' في مخزونك!",
                f"❌ Vous n'avez pas la compétence '{found_skill_name}' dans votre inventaire !"
            ))
            return
        
        current_quantity = inventory_skill_result[0]["quantiter"]
        
        async with db_transaction():
            locked_card = await execute_query_with_retry(
                """
                SELECT skill_id
                FROM user_cards
                WHERE user_id = %s AND card_id = %s
                FOR UPDATE
                """,
                (user_id, user_card_id)
            )
            if not locked_card or locked_card[0]["skill_id"] != 0:
                await ctx.send(_tr(
                    lang,
                    "❌ Your card already has a skill equipped! Please unequip it first.",
                    "❌ بطاقتك لديها مهارة مجهزة بالفعل! يرجى إزالة تجهيزها أولاً.",
                    "❌ Votre carte a déjà une compétence équipée ! Veuillez d'abord la déséquiper."
                ))
                return

            locked_skill = await execute_query_with_retry(
                """
                SELECT quantiter
                FROM inventory_skills
                WHERE inventory_id = %s AND skill_id = %s
                FOR UPDATE
                """,
                (inventory_id, skill_id)
            )
            if not locked_skill or locked_skill[0]["quantiter"] <= 0:
                await ctx.send(_tr(
                    lang,
                    f"❌ You don't have the skill '{found_skill_name}' in your inventory!",
                    f"❌ لا تملك المهارة '{found_skill_name}' في مخزونك!",
                    f"❌ Vous n'avez pas la compétence '{found_skill_name}' dans votre inventaire !"
                ))
                return

            await execute_query_with_retry(
                """
                UPDATE user_cards
                SET skill_id = %s
                WHERE user_id = %s AND card_id = %s
                """,
                (skill_id, user_id, user_card_id)
            )

            if locked_skill[0]["quantiter"] == 1:
                await execute_query_with_retry(
                    """
                    DELETE FROM inventory_skills
                    WHERE inventory_id = %s AND skill_id = %s
                    """,
                    (inventory_id, skill_id)
                )
            else:
                await execute_query_with_retry(
                    """
                    UPDATE inventory_skills
                    SET quantiter = quantiter - 1
                    WHERE inventory_id = %s AND skill_id = %s
                    """,
                    (inventory_id, skill_id)
                )

        await ctx.send(_tr(
            lang,
            f"✅ The skill '{found_skill_name}' has been equipped to your card!",
            f"✅ تم تجهيز المهارة '{found_skill_name}' على بطاقتك!",
            f"✅ La compétence '{found_skill_name}' a été équipée sur votre carte !"
        ))

    @commands.command(name='sunequip', aliases=["sq","sku"])
    @advisory_lock_for_context("slbot:skill")
    async def skill_unequip(self, ctx, *, args=None):
        """Unequip a skill from a card (multilanguage)"""
        lang = await getlang(ctx.author.id)

        if not args or ',' not in args:
            await ctx.send(_tr(
                lang,
                "❌ Please use the format: `sl sunequip skill_name, card_name`",
                "❌ يرجى استخدام الصيغة: `sl sunequip skill_name, card_name`",
                "❌ Veuillez utiliser le format : `sl sunequip skill_name, card_name`"
            ))
            return
        
        try:
            skill_name, card_name = [arg.strip() for arg in args.split(',', 1)]
        except ValueError:
            await ctx.send(_tr(
                lang,
                "❌ Please use the format: `sl sunequip skill_name, card_name`",
                "❌ يرجى استخدام الصيغة: `sl sunequip skill_name, card_name`",
                "❌ Veuillez utiliser le format : `sl sunequip skill_name, card_name`"
            ))
            return

        # Get user info
        user_query = """
        SELECT user_id, fight_current, dungeon_current 
        FROM user 
        WHERE user_authorid = %s
        """
        user_result = await execute_query_with_retry(user_query, (str(ctx.author.id),))
        
        if not user_result:
            await ctx.send(_tr(
                lang,
                "❌ You are not registered! Please type `sl register` to get started.",
                "❌ لست مسجلاً! اكتب `sl register` للبدء.",
                "❌ Vous n'êtes pas enregistré ! Tapez `sl register` pour commencer."
            ))
            return

        user_id = user_result[0]["user_id"]
        fight_current = user_result[0]["fight_current"]
        dungeon_current = user_result[0]["dungeon_current"]

        # Check if user is in fight or dungeon
        if fight_current == 1 or dungeon_current == 1:
            await ctx.send(_tr(
                lang,
                "❌ You cannot unequip skills while in a fight or dungeon!",
                "❌ لا يمكنك إزالة تجهيز المهارات أثناء القتال أو في الزنزانة!",
                "❌ Vous ne pouvez pas déséquiper de compétences pendant un combat ou dans un donjon !"
            ))
            return
        
        # Get inventory_id
        inventory_query = "SELECT inventory_id FROM inventory WHERE user_id = %s"
        inventory_result = await execute_query_with_retry(inventory_query, (user_id,))
        
        if not inventory_result:
            await ctx.send(_tr(
                lang,
                "❌ Inventory not found!",
                "❌ لم يتم العثور على المخزون!",
                "❌ Inventaire introuvable !"
            ))
            return
        
        inventory_id = inventory_result[0]["inventory_id"]
        
        # Find skill by name
        skill_query = """
        SELECT skill_id, skill_name 
        FROM skill 
        WHERE skill_name LIKE %s
        """
        skill_results = await execute_query_with_retry(skill_query, (f"%{skill_name}%",))
        
        if not skill_results:
            await ctx.send(_tr(
                lang,
                f"❌ No skill found with name containing '{skill_name}'",
                f"❌ لم يتم العثور على مهارة تحتوي على الاسم '{skill_name}'",
                f"❌ Aucune compétence trouvée contenant le nom '{skill_name}'"
            ))
            return
        
        if len(skill_results) > 1:
            embed = discord.Embed(
                title=_tr(lang, "Multiple Skills Found", "تم العثور على عدة مهارات", "Plusieurs compétences trouvées"),
                description=_tr(
                    lang,
                    "I found multiple skills matching your search. Please specify which one you'd like to use:",
                    "وجدتُ عدة مهارات تطابق بحثك. يرجى تحديد أيّها تريد استخدامه:",
                    "J'ai trouvé plusieurs compétences correspondant à votre recherche. Veuillez préciser laquelle utiliser :"
                ),
                color=discord.Color.blue()
            )
            skill_list = "\n".join([f"• {skill['skill_name']}" for skill in skill_results])
            embed.add_field(name=_tr(lang, "Skills:", "المهارات:", "Compétences :"), value=skill_list, inline=False)
            embed.set_footer(text=_tr(
                lang,
                "Please use the command again with the exact name of the skill you want to use.",
                "يرجى إعادة استخدام الأمر مع الاسم الدقيق للمهارة التي تريد استخدامها.",
                "Veuillez réutiliser la commande avec le nom exact de la compétence."
            ))
            await ctx.send(embed=embed)
            return

        skill_results = skill_results[0]
        skill_id = skill_results["skill_id"]
        found_skill_name = skill_results["skill_name"]

        # Find user's card by name
        card_query = """
        SELECT uc.card_id, uc.skill_id, uc.name
        FROM user_cards uc
        WHERE uc.user_id = %s AND uc.name LIKE %s
        """
        card_results = await execute_query_with_retry(card_query, (user_id, f"%{card_name}%"))
        
        if not card_results:
            await ctx.send(_tr(
                lang,
                f"❌ You don't have any card with name containing '{card_name}'",
                f"❌ ليس لديك أي بطاقة يحتوي اسمها على '{card_name}'",
                f"❌ Vous n'avez aucune carte dont le nom contient '{card_name}'"
            ))
            return
        
        if len(card_results) > 1:
            embed = discord.Embed(
                title=_tr(lang, "Multiple Cards Found", "تم العثور على عدة بطاقات", "Plusieurs cartes trouvées"),
                description=_tr(
                    lang,
                    "I found multiple cards matching your search. Please specify which one you'd like to use:",
                    "وجدتُ عدة بطاقات تطابق بحثك. يرجى تحديد أيّها تريد استخدامها:",
                    "J'ai trouvé plusieurs cartes correspondant à votre recherche. Veuillez préciser laquelle utiliser :"
                ),
                color=discord.Color.blue()
            )
            card_list = "\n".join([f"• {card['name']}" for card in card_results])
            embed.add_field(name=_tr(lang, "Your Cards:", "بطاقاتك:", "Vos cartes :"), value=card_list, inline=False)
            embed.set_footer(text=_tr(
                lang,
                "Please use the command again with the exact name of the card you want to use.",
                "يرجى إعادة استخدام الأمر مع الاسم الدقيق للبطاقة التي تريد استخدامها.",
                "Veuillez réutiliser la commande avec le nom exact de la carte."
            ))
            await ctx.send(embed=embed)
            return

        card_results = card_results[0]
        user_card_id = card_results["card_id"]
        current_skill_id = card_results["skill_id"]
        found_card_name = card_results["name"]

        # Check if the card has the specified skill equipped
        if current_skill_id != skill_id:
            await ctx.send(_tr(
                lang,
                f"❌ Your card doesn't have the skill '{found_skill_name}' equipped!",
                f"❌ بطاقتك لا تحتوي على المهارة '{found_skill_name}' مجهزة!",
                f"❌ Votre carte n'a pas la compétence '{found_skill_name}' équipée !"
            ))
            return
        
        async with db_transaction():
            locked_card = await execute_query_with_retry(
                """
                SELECT skill_id
                FROM user_cards
                WHERE user_id = %s AND card_id = %s
                FOR UPDATE
                """,
                (user_id, user_card_id)
            )
            if not locked_card or locked_card[0]["skill_id"] != skill_id:
                await ctx.send(_tr(
                    lang,
                    f"❌ Your card doesn't have the skill '{found_skill_name}' equipped!",
                    f"❌ بطاقتك لا تحتوي على المهارة '{found_skill_name}' مجهزة!",
                    f"❌ Votre carte n'a pas la compétence '{found_skill_name}' équipée !"
                ))
                return

            inventory_skill_result = await execute_query_with_retry(
                """
                SELECT quantiter
                FROM inventory_skills
                WHERE inventory_id = %s AND skill_id = %s
                FOR UPDATE
                """,
                (inventory_id, skill_id)
            )

            if not inventory_skill_result:
                await execute_query_with_retry(
                    """
                    INSERT INTO inventory_skills (inventory_id, skill_id, quantiter)
                    VALUES (%s, %s, 1)
                    """,
                    (inventory_id, skill_id)
                )
            else:
                await execute_query_with_retry(
                    """
                    UPDATE inventory_skills
                    SET quantiter = quantiter + 1
                    WHERE inventory_id = %s AND skill_id = %s
                    """,
                    (inventory_id, skill_id)
                )

            await execute_query_with_retry(
                """
                UPDATE user_cards
                SET skill_id = 0
                WHERE user_id = %s AND card_id = %s
                """,
                (user_id, user_card_id)
            )

        await ctx.send(_tr(
            lang,
            f"✅ The skill '{found_skill_name}' has been unequipped from your card!",
            f"✅ تمت إزالة تجهيز المهارة '{found_skill_name}' من بطاقتك!",
            f"✅ La compétence '{found_skill_name}' a été déséquipée de votre carte !"
        ))
    @commands.command(name="setl", aliases=["stl", "lang"])
    async def set_language(self, ctx, lang: int = None):
        """
        Set your language preference.
        Usage: sl setl <0|1|3>
          0 = English, 1 = Arabic, 3 = French
        """
        # Validate input
        if lang not in (0, 1, 3):
            await ctx.send(
                "❌ Invalid value. Use `0` (English), `1` (Arabic), or `3` (French).\n"
                "Example: `sl setl 0`"
            )
            return

        try:
            # Ensure the user exists
            user = await execute_query_with_retry(
                "SELECT user_id, multilanguage FROM user WHERE user_authorid = %s",
                (str(ctx.author.id),),
            )
            if not user:
                await ctx.send("❌ You need to register first with `sl register`.")
                return

            # Update multilanguage using user_authorid as string
            await execute_query_with_retry(
                "UPDATE user SET multilanguage = %s WHERE user_authorid = %s",
                (lang, str(ctx.author.id)),
            )

            # Confirmation
            pretty = LANG_NAMES.get(lang, str(lang))
            await ctx.send(f"✅ Language updated to **{pretty}** (code `{lang}`).")

        except Exception as e:
            await ctx.send(f"⚠️ Failed to update language: {e}")
async def setup(bot):
    await bot.add_cog(SkillCommands(bot))
