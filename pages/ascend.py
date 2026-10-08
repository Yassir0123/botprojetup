import discord
from discord.ext import commands
import asyncio
from PIL import Image, ImageDraw, ImageFont
import random
from utils.database import execute_query_with_retry
from utils.activity_state import block_during_user_activity
from utils.db_atomic import db_advisory_lock, db_transaction
import io
import os
import time
import traceback

def _tr(lang: int, en: str, ar: str, fr: str) -> str:
    """Simple i18n: 0=en, 1=ar, 3=fr"""
    if lang == 1:
        return ar
    if lang == 3:
        return fr
    return en

async def _get_lang(discord_user_id: int) -> int:
    """Fetch multilanguage flag (0=en,1=ar,3=fr). Fallback to 0."""
    try:
        q = "SELECT multilanguage FROM user WHERE user_authorid = %s"
        res = await execute_query_with_retry(q, (str(discord_user_id),))
        if res and 'multilanguage' in res[0] and res[0]['multilanguage'] is not None:
            return int(res[0]['multilanguage'])
    except Exception:
        pass
    return 0

class AscendCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.image_path = os.path.join(os.path.dirname(__file__), '..', 'images')

    def get_image_path(self, image_name):
        if not image_name:
            return None
        return os.path.join(self.image_path, image_name)

    def calculate_cap_increases(self, current_rank, new_rank, current_evo):
        """
        Calculate cap increases based on rank progression and evolution level.
        Returns dict with attack, health, speed, defence cap increases.
        """
        # Rank mappings: card_rank values to rank names
        rank_mapping = {
            2: 'S', 3: 'A', 4: 'B', 5: 'C', 6: 'D', 7: 'E', 10: 'SS', 11: 'SSS'
        }
        
        # Cap increases for 1st ascension (evo 0 -> 1)
        first_ascension_caps = {
            'SS': {'attack': 15000, 'health': 18000, 'speed': 15000, 'defence': 15000},
            'S': {'attack': 10000, 'health': 12000, 'speed': 10000, 'defence': 10000},
            'A': {'attack': 8000, 'health': 9600, 'speed': 8000, 'defence': 8000},
            'B': {'attack': 5000, 'health': 6000, 'speed': 5000, 'defence': 5000},
            'C': {'attack': 3000, 'health': 3600, 'speed': 3000, 'defence': 3000},
            'D': {'attack': 1000, 'health': 1200, 'speed': 1000, 'defence': 1000}
        }
        
        # Cap increases for 2nd ascension (evo 1 -> 2)
        second_ascension_caps = {
            'SSS': {'attack': 30000, 'health': 36000, 'speed': 30000, 'defence': 30000},
            'SS': {'attack': 20000, 'health': 24000, 'speed': 20000, 'defence': 20000},
            'S': {'attack': 15000, 'health': 18000, 'speed': 15000, 'defence': 15000},
            'A': {'attack': 10000, 'health': 12000, 'speed': 10000, 'defence': 10000},
            'B': {'attack': 7000, 'health': 8400, 'speed': 7000, 'defence': 7000},
            'C': {'attack': 4000, 'health': 4800, 'speed': 4000, 'defence': 4000}
        }
        
        current_rank_name = rank_mapping.get(current_rank, 'E')
        new_rank_name = rank_mapping.get(new_rank, 'E')
        
        # Determine which cap table to use based on current evolution
        if current_evo == 1:  # First ascension
            cap_table = first_ascension_caps
        else:  # Second ascension
            cap_table = second_ascension_caps
        
        # If going to same rank, use 80% of next higher rank's cap increase
        if current_rank == new_rank:
            # Find next higher rank
            rank_hierarchy = ['E', 'D', 'C', 'B', 'A', 'S', 'SS', 'SSS']
            try:
                current_index = rank_hierarchy.index(current_rank_name)
                if current_index < len(rank_hierarchy) - 1:
                    next_rank = rank_hierarchy[current_index + 1]
                    if next_rank in cap_table:
                        base_caps = cap_table[next_rank]
                        return {
                            'attack': int(base_caps['attack'] * 0.8),
                            'health': int(base_caps['health'] * 0.8),
                            'speed': int(base_caps['speed'] * 0.8),
                            'defence': int(base_caps['defence'] * 0.8)
                        }
            except ValueError:
                pass
            # Fallback to 0 if can't find next rank
            return {'attack': 0, 'health': 0, 'speed': 0, 'defence': 0}
        else:
            # Going to different rank, use full cap increase for new rank
            if new_rank_name in cap_table:
                return cap_table[new_rank_name]
            else:
                return {'attack': 0, 'health': 0, 'speed': 0, 'defence': 0}

    @commands.command(aliases=["asc"])
    @block_during_user_activity(_get_lang)
    async def ascend(self, ctx, *, card_name=None):
        lang = await _get_lang(ctx.author.id)

        if not card_name:
            await ctx.send(_tr(
                lang,
                "Please specify a card name. Usage: `sl ascend card_name`",
                "يرجى تحديد اسم البطاقة. الاستخدام: `sl ascend card_name`",
                "Veuillez indiquer un nom de carte. Utilisation : `sl ascend card_name`"
            ))
            return

        try:
            # Get user_id from Discord ID
            user_query = "SELECT user_id FROM user WHERE user_authorid = %s"
            user_result = await execute_query_with_retry(user_query, (str(ctx.author.id),))

            if not user_result:
                await ctx.send(_tr(
                    lang,
                    "Error: You need to register to the bot first.",
                    "خطأ: يجب أن تسجل في البوت أولاً.",
                    "Erreur : vous devez d’abord vous inscrire au bot."
                ))
                return

            user_id = user_result[0]['user_id']

            # Check for trade operations
            trade_query = """
            SELECT *
            FROM trade_status
            WHERE (initiator_id = %s OR target_id = %s)
            """
            trade_result = await execute_query_with_retry(trade_query, (user_id, user_id))
            
            if trade_result:
                await ctx.send(_tr(
                    lang,
                    "❌ You cannot ascend cards while in a trade operation. Please complete or cancel your current trade first.",
                    "❌ لا يمكنك ترقية البطاقات أثناء عملية تبادل. الرجاء إنهاء أو إلغاء التبادل الحالي أولاً.",
                    "❌ Vous ne pouvez pas faire d’ascension pendant un échange. Terminez ou annulez l’échange en cours d’abord."
                ))
                return

            # Check for sell operations
            sell_query = "SELECT * FROM sell_status WHERE user_id = %s"
            sell_result = await execute_query_with_retry(sell_query, (user_id,))
            
            if sell_result:
                await ctx.send(_tr(
                    lang,
                    "❌ You cannot ascend cards while in a sell operation. Please complete or cancel your current sell first.",
                    "❌ لا يمكنك ترقية البطاقات أثناء عملية بيع. الرجاء إنهاء أو إلغاء البيع الحالي أولاً.",
                    "❌ Vous ne pouvez pas faire d’ascension pendant une vente. Terminez ou annulez la vente en cours d’abord."
                ))
                return

            # Check for shop operations
            shop_query = "SELECT * FROM shop_status WHERE user_id = %s"
            shop_result = await execute_query_with_retry(shop_query, (user_id,))
            
            if shop_result:
                await ctx.send(_tr(
                    lang,
                    "❌ You cannot ascend cards while in a shop operation. Please complete or cancel your current shop interaction first.",
                    "❌ لا يمكنك ترقية البطاقات أثناء استخدام المتجر. الرجاء إنهاء أو إلغاء تفاعلك الحالي مع المتجر أولاً.",
                    "❌ Vous ne pouvez pas faire d’ascension pendant une interaction de boutique. Terminez ou annulez l’interaction en cours d’abord."
                ))
                return

            market_check_query = "SELECT * FROM market_session WHERE user_id = %s"
            market_result = await execute_query_with_retry(market_check_query, (user_id,))
            
            if market_result:
                await ctx.send(_tr(
                    lang,
                    "❌ You cannot ascend cards while in a market operation. Please complete or cancel your current market interaction first.",
                    "❌ لا يمكنك ترقية البطاقات أثناء عملية السوق. الرجاء إنهاء أو إلغاء تفاعلك الحالي في السوق أولاً.",
                    "❌ Vous ne pouvez pas faire d’ascension pendant une opération de marché. Terminez ou annulez l’interaction en cours d’abord."
                ))
                return

            ascending_check_query = "SELECT * FROM ascending_state WHERE user_id = %s"
            ascending_result = await execute_query_with_retry(ascending_check_query, (user_id,))
            
            if ascending_result:
                await ctx.send(_tr(
                    lang,
                    "⏳ You are already performing an ascension. Please wait for it to complete before starting another one.",
                    "⏳ أنت تقوم بالفعل بعملية ترقية. يرجى الانتظار حتى تكتمل قبل بدء أخرى.",
                    "⏳ Vous effectuez déjà une ascension. Veuillez attendre la fin avant d’en lancer une autre."
                ))
                return

            insert_ascending_query = "INSERT INTO ascending_state (user_id) VALUES (%s)"
            await execute_query_with_retry(insert_ascending_query, (user_id,))

            # Get inventory_id
            inventory_query = "SELECT inventory_id FROM inventory WHERE user_id = %s"
            inventory_result = await execute_query_with_retry(inventory_query, (user_id,))

            if not inventory_result:
                await execute_query_with_retry("DELETE FROM ascending_state WHERE user_id = %s", (user_id,))
                await ctx.send(_tr(
                    lang,
                    "Error: You don't have an inventory.",
                    "خطأ: ليس لديك مخزون.",
                    "Erreur : vous n’avez pas d’inventaire."
                ))
                return

            inventory_id = inventory_result[0]['inventory_id']

            # Find the card by name
            card_query = """
            SELECT c.*, cr.cardrank_name, cc.card_class_name
            FROM card c
            JOIN card_rank cr ON c.card_rank = cr.cardrank_id
            JOIN card_class cc ON c.card_class_id = cc.card_class_id
            WHERE c.card_name LIKE %s
            """
            search_pattern = f"%{card_name}%"
            card_results = await execute_query_with_retry(card_query, (search_pattern,))

            if not card_results:
                await execute_query_with_retry("DELETE FROM ascending_state WHERE user_id = %s", (user_id,))
                await ctx.send(_tr(
                    lang,
                    f"No cards matching '{card_name}' found in the database.",
                    f"لا توجد بطاقات مطابقة لـ '{card_name}' في قاعدة البيانات.",
                    f"Aucune carte correspondant à « {card_name} » n’a été trouvée dans la base de données."
                ))
                return

            if len(card_results) > 1:
                await execute_query_with_retry("DELETE FROM ascending_state WHERE user_id = %s", (user_id,))
                
                # Create an embed to show multiple matches
                embed = discord.Embed(
                    title=_tr(lang, "Multiple Cards Found", "تم العثور على عدة بطاقات", "Plusieurs cartes trouvées"),
                    description=_tr(
                        lang,
                        "I found multiple cards matching your search. Please specify which one you'd like to ascend:",
                        "لقد وجدت عدة بطاقات تطابق بحثك. يرجى تحديد البطاقة التي تريد ترقيتها:",
                        "J’ai trouvé plusieurs cartes correspondant à votre recherche. Veuillez préciser laquelle vous voulez faire évoluer :"
                    ),
                    color=discord.Color.blue()
                )

                # List all matching cards
                card_list = "\n".join([f"• {card['card_name']}" for card in card_results])
                embed.add_field(name=_tr(lang, "Matching Cards", "البطاقات المطابقة", "Cartes correspondantes"), value=card_list, inline=False)
                embed.set_footer(text=_tr(lang, "Please use the command again with the exact name of the card you want to ascend.", "يرجى إعادة استخدام الأمر مع الاسم الدقيق للبطاقة التي تريد ترقيتها.", "Veuillez réutiliser la commande avec le nom exact de la carte que vous voulez faire évoluer."))
                
                await ctx.send(embed=embed)
                return

            # We found exactly one card
            card = card_results[0]
            card_id = card['card_id']

            # Check if there's already an entry in stat_status table
            stat_status_query = "SELECT * FROM stat_status WHERE user_id = %s AND card_id = %s"
            stat_status_result = await execute_query_with_retry(stat_status_query, (user_id, card_id))
                            
            if stat_status_result:
                await execute_query_with_retry("DELETE FROM ascending_state WHERE user_id = %s", (user_id,))
                await ctx.send(_tr(
                    lang,
                    "This card is currently being adjusted. Please wait until the process is completed.",
                    "هذه البطاقة قيد التعديل حالياً. يرجى الانتظار حتى اكتمال العملية.",
                    "Cette carte est en cours d’ajustement. Veuillez attendre la fin du processus."
                ))
                return

            # Check if the user has this card
            user_card_query = """
            SELECT * FROM user_cards 
            WHERE user_id = %s AND card_id = %s
            """
            user_card_result = await execute_query_with_retry(user_card_query, (user_id, card_id))

            if not user_card_result:
                await execute_query_with_retry("DELETE FROM ascending_state WHERE user_id = %s", (user_id,))
                await ctx.send(_tr(
                    lang,
                    f"You don't have the card '{card['card_name']}' in your collection.",
                    f"ليست لديك البطاقة '{card['card_name']}' في مجموعتك.",
                    f"Vous n’avez pas la carte « {card['card_name']} » dans votre collection."
                ))
                return

            user_card = user_card_result[0]

            # Get current evolution level
            current_evo = user_card.get('evo', 0)

            # Get upgrade requirements from card_upgrades table
            upgrade_query = """
            SELECT cu.*, cr.cardrank_image
            FROM card_upgrades cu
            JOIN card_rank cr ON cu.card_rank = cr.cardrank_id
            WHERE cu.card_id = %s AND cu.evo = %s
            """

            upgrade_result = await execute_query_with_retry(upgrade_query, (card_id, current_evo))
            if not upgrade_result:
                await execute_query_with_retry("DELETE FROM ascending_state WHERE user_id = %s", (user_id,))
                await ctx.send(_tr(
                    lang,
                    f"No evolution data found for '{card['card_name']}' at evolution level {current_evo}.",
                    f"لا توجد بيانات تطور لـ '{card['card_name']}' عند مستوى التطور {current_evo}.",
                    f"Aucune donnée d’évolution trouvée pour « {card['card_name']} » au niveau d’évolution {current_evo}."
                ))
                return

            upgrade = upgrade_result[0]

            # Extract upgrade requirements
            level_cap = upgrade['level_cap']
            affinity_cap = upgrade['affinity_cap']
            dungeonorb_id = upgrade['dungeonorb_id']
            dungeon_orb_cap = upgrade['dungeon_orb_cap']
            fragment_id = upgrade['fragment_id']
            fragment_cap = upgrade['fragment_cap']
            won_currency_cap = upgrade['won_currency']
            new_card_name = upgrade['card_name']
            new_card_image = upgrade['card_image']
            new_card_rank = upgrade['card_rank']
            
            # Get orb and fragment names for display
            orb_name_query = "SELECT orb_name, orb_icon FROM leveling_orbs WHERE orb_id = %s"
            orb_name_result = await execute_query_with_retry(orb_name_query, (dungeonorb_id,))
            orb_name = orb_name_result[0]['orb_name'] if orb_name_result else f"Orb #{dungeonorb_id}"
            orb_icon = orb_name_result[0].get('orb_icon', '🔮') if orb_name_result else '🔮'

            frag_name_query = "SELECT name_fragment, fragments_icon FROM fragments WHERE id_fragment = %s"
            frag_name_result = await execute_query_with_retry(frag_name_query, (fragment_id,))
            frag_name = frag_name_result[0]['name_fragment'] if frag_name_result else f"Fragment #{fragment_id}"
            frag_icon = frag_name_result[0].get('fragments_icon', '🧩') if frag_name_result else '🧩'

            # Check all requirements and collect status
            requirements_met = True
            requirement_status = []

            # Level requirement
            current_level = user_card['current_level']
            level_met = current_level >= level_cap
            if not level_met:
                requirements_met = False
            requirement_status.append({
                'name': _tr(lang, 'Level', 'المستوى', 'Niveau'),
                'current': current_level,
                'required': level_cap,
                'met': level_met,
                'icon': '📊'
            })

            # Orb requirement
            orb_query = """
            SELECT quantiter FROM inventory_orbs
            WHERE inventory_id = %s AND orb_id = %s
            """
            orb_result = await execute_query_with_retry(orb_query, (inventory_id, dungeonorb_id))
            current_orbs = orb_result[0]['quantiter'] if orb_result else 0
            orb_met = current_orbs >= dungeon_orb_cap
            if not orb_met:
                requirements_met = False
            requirement_status.append({
                'name': orb_name,
                'current': current_orbs,
                'required': dungeon_orb_cap,
                'met': orb_met,
                'icon': orb_icon
            })

            # Fragment requirement
            frag_query = """
            SELECT quantiter FROM inventory_frags
            WHERE inventory_id = %s AND frag_id = %s
            """
            frag_result = await execute_query_with_retry(frag_query, (inventory_id, fragment_id))
            current_frags = frag_result[0]['quantiter'] if frag_result else 0
            frag_met = current_frags >= fragment_cap
            if not frag_met:
                requirements_met = False
            requirement_status.append({
                'name': frag_name,
                'current': current_frags,
                'required': fragment_cap,
                'met': frag_met,
                'icon': frag_icon
            })

            # Affinity requirement
            current_affinity = user_card['affinity']
            affinity_met = current_affinity >= affinity_cap
            if not affinity_met:
                requirements_met = False
            requirement_status.append({
                'name': _tr(lang, 'Affinity', 'الأُلفة', 'Affinité'),
                'current': f"{current_affinity:.2f}%",
                'required': f"{affinity_cap:.2f}%",
                'met': affinity_met,
                'icon': '✨'
            })

            # Currency requirement
            currency_query = "SELECT won_currency FROM user WHERE user_id = %s"
            currency_result = await execute_query_with_retry(currency_query, (user_id,))
            current_currency = currency_result[0]['won_currency'] if currency_result else 0
            currency_met = current_currency >= won_currency_cap
            if not currency_met:
                requirements_met = False
            requirement_status.append({
                'name': _tr(lang, 'Currency', 'العملة', 'Monnaie'),
                'current': f"{current_currency:,}",
                'required': f"{won_currency_cap:,}",
                'met': currency_met,
                'icon': '💰'
            })

            # If requirements not met, show requirements embed
            if not requirements_met:
                await execute_query_with_retry("DELETE FROM ascending_state WHERE user_id = %s", (user_id,))
                
                # Create requirements embed similar to ascend embed
                if card['card_name'] == new_card_name:
                    embed = discord.Embed(
                        title=_tr(lang, f"{new_card_name} **[EVO{current_evo + 1}]**", f"{new_card_name} **[تطور {current_evo + 1}]**", f"{new_card_name} **[EVO{current_evo + 1}]**"),
                        description=_tr(
                            lang,
                            "❌ **Requirements Not Met**\nYou need to meet all requirements to ascend this card.",
                            "❌ **المتطلبات غير مستوفاة**\nتحتاج إلى استيفاء جميع المتطلبات لترقية هذه البطاقة.",
                            "❌ **Conditions non remplies**\nVous devez répondre à toutes les conditions pour faire évoluer cette carte."
                        ),
                        color=discord.Color.red()
                    )
                else:
                    embed = discord.Embed(
                        title=f"{new_card_name}",
                        description=_tr(
                            lang,
                            "❌ **Requirements Not Met**\nYou need to meet all requirements to ascend this card.",
                            "❌ **المتطلبات غير مستوفاة**\nتحتاج إلى استيفاء جميع المتطلبات لترقية هذه البطاقة.",
                            "❌ **Conditions non remplies**\nVous devez répondre à toutes les conditions pour faire évoluer cette carte."
                        ),
                        color=discord.Color.red()
                    )

                # Add requirements field with progress
                requirements_text = ""
                for req in requirement_status:
                    status_icon = "✅" if req['met'] else "❌"
                    requirements_text += f"{status_icon} {req['icon']} {req['name']}: {req['current']}/{req['required']}\n"
                
                embed.add_field(
                    name=_tr(lang, "Requirements", "المتطلبات", "Conditions"),
                    value=requirements_text,
                    inline=False
                )

                files = []
                
                current_image_path = self.get_image_path(upgrade['cardrank_image'])
                if current_image_path and os.path.exists(current_image_path):
                    current_file = discord.File(current_image_path, filename="current_rank.png")
                    files.append(current_file)
                    embed.set_thumbnail(url="attachment://current_rank.png")
                
                new_image_path = self.get_image_path(new_card_image)
                if new_image_path and os.path.exists(new_image_path):
                    new_file = discord.File(new_image_path, filename="evolution_card.png")
                    files.append(new_file)
                    embed.set_image(url="attachment://evolution_card.png")

                embed.set_footer(text=_tr(lang, "Complete the missing requirements and try again.", "أكمل المتطلبات الناقصة وحاول مرة أخرى.", "Complétez les conditions manquantes puis réessayez."))
                
                if files:
                    await ctx.send(embed=embed, files=files)
                else:
                    await ctx.send(embed=embed)
                return

            # All requirements met, proceed with confirmation embed
            if card['card_name'] == new_card_name:
                embed = discord.Embed(
                    title=_tr(lang, f"Ascend {card['card_name']} to {card['card_name']} **[EVO2] ?**", f"ترقية {card['card_name']} إلى {card['card_name']} **[تطور 2]؟**", f"Faire évoluer {card['card_name']} en {card['card_name']} **[EVO2] ?**"),
                    description=_tr(lang, "Your card will evolve to a more powerful form!", "بطاقتك ستتطور إلى شكل أقوى!", "Votre carte va évoluer vers une forme plus puissante !"),
                    color=discord.Color.purple()
                )
            else:
                embed = discord.Embed(
                    title=_tr(lang, f"Ascend {card['card_name']} to {new_card_name}?", f"ترقية {card['card_name']} إلى {new_card_name}؟", f"Faire évoluer {card['card_name']} en {new_card_name} ?"),
                    description=_tr(lang, "Your card will evolve to a more powerful form!", "بطاقتك ستتطور إلى شكل أقوى!", "Votre carte va évoluer vers une forme plus puissante !"),
                    color=discord.Color.purple()
                )

            if current_evo == 1:
                embed.add_field(
                    name=_tr(lang, "Improvements", "التحسينات", "Améliorations"),
                    value=_tr(lang, "Level Cap: 50 → 75", "حد المستوى: 50 → 75", "Limite de niveau : 50 → 75"),
                    inline=False
                )
            else:
                embed.add_field(
                    name=_tr(lang, "Improvements", "التحسينات", "Améliorations"),
                    value=_tr(lang, "Level Cap: 75 → 100", "حد المستوى: 75 → 100", "Limite de niveau : 75 → 100"),
                    inline=False
                )

            # Add resource costs
            embed.add_field(
                name=_tr(lang, "Resource Costs", "تكاليف الموارد", "Coûts en ressources"),
                value=(
                    f"{orb_icon} {orb_name} x{dungeon_orb_cap}\n"
                    f"{frag_icon} {frag_name} x{fragment_cap}\n"
                    f"💰 " + _tr(lang, "Currency", "العملة", "Monnaie") + f": {won_currency_cap:,}"
                ),
                inline=False
            )

            files = []
            
            current_image_path = self.get_image_path(upgrade['cardrank_image'])
            if current_image_path and os.path.exists(current_image_path):
                current_file = discord.File(current_image_path, filename="current_rank.png")
                files.append(current_file)
                embed.set_thumbnail(url="attachment://current_rank.png")
            
            new_image_path = self.get_image_path(new_card_image)
            if new_image_path and os.path.exists(new_image_path):
                new_file = discord.File(new_image_path, filename="evolution_card.png")
                files.append(new_file)
                embed.set_image(url="attachment://evolution_card.png")

            embed.set_footer(text=_tr(lang, "Please confirm or cancel this ascension.", "يرجى التأكيد أو إلغاء هذه الترقية.", "Veuillez confirmer ou annuler cette ascension."))

            # Create confirmation buttons
            class ConfirmView(discord.ui.View):
                def __init__(self):
                    super().__init__(timeout=60)
                    self.value = None

                @discord.ui.button(label=_tr(lang, "Confirm", "تأكيد", "Confirmer"), style=discord.ButtonStyle.green)
                async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
                    if interaction.user.id != ctx.author.id:
                        await interaction.response.send_message(_tr(lang, "You can't use these buttons.", "لا يمكنك استخدام هذه الأزرار.", "Vous ne pouvez pas utiliser ces boutons."), ephemeral=True)
                        return
                    self.value = True
                    self.stop()
                    await interaction.response.defer()

                @discord.ui.button(label=_tr(lang, "Cancel", "إلغاء", "Annuler"), style=discord.ButtonStyle.red)
                async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
                    if interaction.user.id != ctx.author.id:
                        await interaction.response.send_message(_tr(lang, "You can't use these buttons.", "لا يمكنك استخدام هذه الأزرار.", "Vous ne pouvez pas utiliser ces boutons."), ephemeral=True)
                        return
                    self.value = False
                    self.stop()
                    await interaction.response.defer()

            view = ConfirmView()
            if files:
                message = await ctx.send(embed=embed, files=files, view=view)
            else:
                message = await ctx.send(embed=embed, view=view)

            # Wait for user response
            await view.wait()

            if view.value is None:
                await execute_query_with_retry("DELETE FROM ascending_state WHERE user_id = %s", (user_id,))
                await message.edit(content=_tr(lang, "Ascension timed out.", "انتهى وقت الترقية.", "Temps écoulé pour l’ascension."), embed=None, attachments=[], view=None)
                return
            elif not view.value:
                await execute_query_with_retry("DELETE FROM ascending_state WHERE user_id = %s", (user_id,))
                await message.edit(content=_tr(lang, "Ascension cancelled.", "تم إلغاء الترقية.", "Ascension annulée."), embed=None, attachments=[], view=None)
                return

            # User confirmed, proceed with ascension
            try:
                failed_requirements = False
                async with db_advisory_lock(f"slbot:ascend:{user_id}:{card_id}", timeout=5):
                    async with db_transaction():
                        user_rows = await execute_query_with_retry(
                            "SELECT won_currency FROM user WHERE user_id = %s FOR UPDATE",
                            (user_id,)
                        )
                        card_rows = await execute_query_with_retry(
                            """
                            SELECT current_level, affinity, card_rank, evo
                            FROM user_cards
                            WHERE user_id = %s AND card_id = %s
                            FOR UPDATE
                            """,
                            (user_id, card_id)
                        )
                        frag_result = await execute_query_with_retry(
                            """
                            SELECT quantiter
                            FROM inventory_frags
                            WHERE inventory_id = %s AND frag_id = %s
                            FOR UPDATE
                            """,
                            (inventory_id, fragment_id)
                        )
                        orb_result = await execute_query_with_retry(
                            """
                            SELECT quantiter
                            FROM inventory_orbs
                            WHERE inventory_id = %s AND orb_id = %s
                            FOR UPDATE
                            """,
                            (inventory_id, dungeonorb_id)
                        )

                        has_requirements = (
                            user_rows and user_rows[0]['won_currency'] >= won_currency_cap
                            and card_rows
                            and card_rows[0]['current_level'] >= level_cap
                            and card_rows[0]['affinity'] >= affinity_cap
                            and card_rows[0]['card_rank'] == user_card['card_rank']
                            and card_rows[0]['evo'] == current_evo
                            and frag_result and frag_result[0]['quantiter'] >= fragment_cap
                            and orb_result and orb_result[0]['quantiter'] >= dungeon_orb_cap
                        )
                        if not has_requirements:
                            await execute_query_with_retry("DELETE FROM ascending_state WHERE user_id = %s", (user_id,))
                            failed_requirements = True
                        else:
                            await execute_query_with_retry(
                                "UPDATE user SET won_currency = won_currency - %s WHERE user_id = %s",
                                (won_currency_cap, user_id)
                            )

                            remaining_frags = frag_result[0]['quantiter'] - fragment_cap
                            if remaining_frags <= 0:
                                await execute_query_with_retry(
                                    "DELETE FROM inventory_frags WHERE inventory_id = %s AND frag_id = %s",
                                    (inventory_id, fragment_id)
                                )
                            else:
                                await execute_query_with_retry(
                                    "UPDATE inventory_frags SET quantiter = %s WHERE inventory_id = %s AND frag_id = %s",
                                    (remaining_frags, inventory_id, fragment_id)
                                )
                            await execute_query_with_retry(
                                "UPDATE user SET quantiter_fragment = quantiter_fragment - %s WHERE user_id = %s",
                                (fragment_cap, user_id)
                            )

                            remaining_orbs = orb_result[0]['quantiter'] - dungeon_orb_cap
                            if remaining_orbs <= 0:
                                await execute_query_with_retry(
                                    "DELETE FROM inventory_orbs WHERE inventory_id = %s AND orb_id = %s",
                                    (inventory_id, dungeonorb_id)
                                )
                            else:
                                await execute_query_with_retry(
                                    "UPDATE inventory_orbs SET quantiter = %s WHERE inventory_id = %s AND orb_id = %s",
                                    (remaining_orbs, inventory_id, dungeonorb_id)
                                )

                            cap_increases = self.calculate_cap_increases(card_rows[0]['card_rank'], new_card_rank, card_rows[0]['evo'])
                            await execute_query_with_retry(
                                """
                                UPDATE user_cards
                                SET
                                    name = %s,
                                    image = %s,
                                    evo = evo + 1,
                                    current_xp = 0,
                                    max_leveled = max_leveled + 25,
                                    card_rank = %s,
                                    current_cap_attack = current_cap_attack + %s,
                                    current_cap_health = current_cap_health + %s,
                                    current_cap_speed = current_cap_speed + %s,
                                    current_cap_defence = current_cap_defence + %s
                                WHERE user_id = %s AND card_id = %s
                                """,
                                (
                                    new_card_name, new_card_image, new_card_rank,
                                    cap_increases['attack'], cap_increases['health'],
                                    cap_increases['speed'], cap_increases['defence'],
                                    user_id, card_id
                                )
                            )
                            await execute_query_with_retry("DELETE FROM ascending_state WHERE user_id = %s", (user_id,))

                if failed_requirements:
                    await message.edit(
                        content=_tr(lang, "Ascension requirements changed. Please try again.", "Ascension requirements changed. Please try again.", "Ascension requirements changed. Please try again."),
                        embed=None,
                        attachments=[],
                        view=None
                    )
                    return

                # Create success embed
                # Create success embed
                success_embed = discord.Embed(
                    title=_tr(lang, "Ascension Successful!", "تمت الترقية بنجاح!", "Ascension réussie !"),
                    description=_tr(lang, f"Your {card['card_name']} has evolved into {new_card_name}!", f"لقد تطورت بطاقتك {card['card_name']} إلى {new_card_name}!", f"Votre {card['card_name']} a évolué en {new_card_name} !"),
                    color=discord.Color.green()
                )

                improvements_text = _tr(lang, "Level Cap increased to 75!\n", "تم رفع حد المستوى إلى 75!\n", "Limite de niveau augmentée à 75 !\n")
                if cap_increases['attack'] > 0:
                    improvements_text += _tr(lang, f"Attack Cap: +{cap_increases['attack']:,}\n", f"حد الهجوم: +{cap_increases['attack']:,}\n", f"Cap d’attaque : +{cap_increases['attack']:,}\n")
                if cap_increases['health'] > 0:
                    improvements_text += _tr(lang, f"Health Cap: +{cap_increases['health']:,}\n", f"حد الصحة: +{cap_increases['health']:,}\n", f"Cap de santé : +{cap_increases['health']:,}\n")
                if cap_increases['speed'] > 0:
                    improvements_text += _tr(lang, f"Speed Cap: +{cap_increases['speed']:,}\n", f"حد السرعة: +{cap_increases['speed']:,}\n", f"Cap de vitesse : +{cap_increases['speed']:,}\n")
                if cap_increases['defence'] > 0:
                    improvements_text += _tr(lang, f"Defence Cap: +{cap_increases['defence']:,}", f"حد الدفاع: +{cap_increases['defence']:,}", f"Cap de défense : +{cap_increases['defence']:,}")

                success_embed.add_field(
                    name=_tr(lang, "Improvements", "التحسينات", "Améliorations"),
                    value=improvements_text,
                    inline=False
                )

                new_image_path = self.get_image_path(new_card_image)
                if new_image_path and os.path.exists(new_image_path):
                    success_file = discord.File(new_image_path, filename="evolution_card.png")
                    success_embed.set_image(url="attachment://evolution_card.png")
                    await message.edit(content=None, embed=success_embed, attachments=[success_file], view=None)
                else:
                    await message.edit(content=None, embed=success_embed, attachments=[], view=None)

            except Exception as e:
                await execute_query_with_retry("DELETE FROM ascending_state WHERE user_id = %s", (user_id,))
                traceback.print_exc()
                await message.edit(content=_tr(lang, f"An error occurred during ascension: {str(e)}", f"حدث خطأ أثناء الترقية: {str(e)}", f"Une erreur s’est produite pendant l’ascension : {str(e)}"), embed=None, attachments=[], view=None)

        except Exception as e:
            try:
                await execute_query_with_retry("DELETE FROM ascending_state WHERE user_id = %s", (user_id,))
            except:
                pass  # If this fails, the user_id might not have been set yet
            traceback.print_exc()
            await ctx.send(_tr(lang, f"An error occurred: {str(e)}", f"حدث خطأ: {str(e)}", f"Une erreur s’est produite : {str(e)}"))

async def setup(bot):
    await bot.add_cog(AscendCommands(bot))
