import discord
from discord.ext import commands
import asyncio
from typing import Dict, Any, List, Optional, Tuple, Union

# If you already have a helper like this in your project, reuse it.
# Must return: 0 = English, 1 = Arabic, 3 = French
from utils.database import execute_query_with_retry

# ---------------------------
# Localization helpers
# ---------------------------
def _tr(lang: int, en: str, ar: str, fr: str) -> str:
    if lang == 1:
        return ar
    if lang == 3:
        return fr
    return en

async def _get_lang(discord_user_id: int) -> int:
    try:
        return await get_user_multilanguage(int(discord_user_id))
    except Exception:
        return 0
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
        # Do not log per your request (no logging changes); silently fall back
        pass
    return 0
def pick(lang: int, d: Dict[str, str]) -> str:
    """Pick a string from a dict with keys 'en','ar','fr'."""
    if lang == 1:
        return d.get("ar") or d.get("en", "")
    if lang == 3:
        return d.get("fr") or d.get("en", "")
    return d.get("en", "")

class HelpCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

        # Categories & commands with localized titles/descriptions.
        # NOTE: Command 'name' and 'shortcuts' are kept as-is; only descriptions/UI are localized.
        self.categories: Dict[str, Dict[str, Any]] = {
            "User Commands": {
                "title": {"en": "User Commands", "ar": "أوامر المستخدم", "fr": "Commandes utilisateur"},
                "description": {
                    "en": "Manage your hunter profile and track progress.",
                    "ar": "أدِر ملفك كصيّاد وتابع تقدمك.",
                    "fr": "Gérez votre profil de chasseur et suivez votre progression."
                },
                "emoji": "👤",
                "commands": [
                    {"name": "daily ", "shortcuts": ["`dl`", "`dy`"],
                     "desc": {"en": "Check and claim daily rewards", "ar": "تحقق واحصل على المكافآت اليومية", "fr": "Vérifiez et réclamez vos récompenses quotidiennes"},
                     "example": "sl daily"},
                    {"name": "bal ", "shortcuts": ["`bal`", "`b`"],
                     "desc": {"en": "Check your hunter's balance", "ar": "تحقق من رصيد الصياد الخاص بك", "fr": "Consultez le solde de votre chasseur"},
                     "example": "sl bal"},
                    {"name": "guide ", "shortcuts": ["`gd`", "`help`"],
                     "desc": {"en": "View the hunter's guide", "ar": "اعرض دليل الصيّاد", "fr": "Consulter le guide du chasseur"},
                     "example": "sl guide"},
                    {"name": "profile ", "shortcuts": ["`prof`", "`pf`"],
                     "desc": {"en": "View your hunter profile", "ar": "اعرض ملفك كصيّاد", "fr": "Afficher votre profil de chasseur"},
                     "example": "sl profile"},
                    {"name": "register ", "shortcuts": ["`reg`", "`rg`"],
                     "desc": {"en": "Register as a hunter", "ar": "سجّل كصيّاد", "fr": "S’enregistrer en tant que chasseur"},
                     "example": "sl register"},
                    {"name": "cd ", "shortcuts": ["`cdt`", "`cooldown`"],
                     "desc": {"en": "View your timers", "ar": "اعرض المؤقتات الخاصة بك", "fr": "Voir vos temporisateurs"},
                     "example": "sl cd"},
                    {"name": "quest ", "shortcuts": ["`qst`", "`qs`"],
                     "desc": {"en": "View and manage quests", "ar": "اعرض وأدر المهام", "fr": "Voir et gérer les quêtes"},
                     "example": "sl quest"}
                ]
            },
            "Language": {
            "title": { "en": "Language Settings", "ar": "إعدادات اللغة", "fr": "Paramètres de langue" },
            "description": {
                "en": "Change the bot's UI language for your messages.",
                "ar": "غيّر لغة واجهة البوت لرسائلك.",
                "fr": "Changez la langue de l’interface du bot pour vos messages."
            },
            "emoji": "🌐",
            "commands": [
                {
                "name": "setlang <code|name>",
                "shortcuts": ["`lang`", "`language`"],
                "desc": {
                    "en": "Set your language using `en/english`, `ar/arabic`, or `fr/french` (or `0/1/3`).",
                    "ar": "اضبط لغتك باستخدام `en/english` أو `ar/arabic` أو `fr/french` (أو `0/1/3`).",
                    "fr": "Définissez votre langue avec `en/english`, `ar/arabic` ou `fr/french` (ou `0/1/3`)."
                },
                "example": "sl setlang en"
                }
            ]
            },

            "Mission/Dungeon Commands": {
                "title": {"en": "Mission/Dungeon Commands", "ar": "أوامر المهمات/الزنزانات", "fr": "Commandes Mission/Donjon"},
                "description": {
                    "en": "Complete missions and explore dangerous dungeons.",
                    "ar": "أكمل المهمات واستكشف الزنزانات الخطرة.",
                    "fr": "Terminez des missions et explorez des donjons dangereux."
                },
                "emoji": "🏹",
                "commands": [
                    {"name": "mission ", "shortcuts": ["`ms`", "`mis`"],
                     "desc": {"en": "View and start missions", "ar": "اعرض وابدأ المهمات", "fr": "Voir et démarrer des missions"},
                     "example": "sl mission"},
                    {"name": "dungeon ", "shortcuts": ["`dg`", "`dgn`"],
                     "desc": {"en": "Enter and challenge dungeons for rewards", "ar": "ادخل الزنزانات وتحدَّها للحصول على مكافآت", "fr": "Entrer et défier des donjons pour des récompenses"},
                     "example": "sl dungeon / sl dungeon s/a/b/c/d/e"}
                ]
            },
            "Gambling Commands": {
                "title": {"en": "Gambling Commands", "ar": "أوامر المقامرة", "fr": "Commandes de jeu"},
                "description": {
                    "en": "Test your luck and win big with various gambling games!",
                    "ar": "اختبر حظك واربح كثيرًا مع ألعاب المقامرة المتنوعة!",
                    "fr": "Testez votre chance et gagnez gros avec divers jeux d’argent !"
                },
                "emoji": "🎲",
                "commands": [
                    {"name": "coinflip ", "shortcuts": ["`cf`", "`coin`"],
                     "desc": {"en": "Bet on heads or tails (optional bet amount)", "ar": "راهن على الصورة أو الكتابة (قيمة الرهان اختيارية)", "fr": "Pariez sur pile ou face (mise facultative)"},
                     "example": "sl coinflip heads 500 / sl cf 1000"},
                    {"name": "duel ", "shortcuts": ["`dll`", "`du`"],
                     "desc": {"en": "Challenge another hunter to a winner-takes-all duel", "ar": "تحدَّ صيادًا آخر في مبارزة الرابح يحصل على كل شيء", "fr": "Défiez un autre chasseur dans un duel où le gagnant rafle tout"},
                     "example": "sl duel @username 1000"},
                    {"name": "race ", "shortcuts": ["`sr`", "`rcs`"],
                     "desc": {"en": "Race against another hunter using snails (optional bet)", "ar": "تسابق ضد صياد آخر باستخدام القواقع (رهان اختياري)", "fr": "Faites la course contre un autre chasseur avec des escargots (mise facultative)"},
                     "example": "sl race @username 500"}
                ]
            },
            "Mining Commands": {
                "title": {"en": "Mining Commands", "ar": "أوامر التعدين", "fr": "Commandes de minage"},
                "description": {
                    "en": "Dig for resources and upgrade your mining equipment.",
                    "ar": "احفر عن الموارد وطوّر معدات التعدين الخاصة بك.",
                    "fr": "Extrayez des ressources et améliorez votre équipement de minage."
                },
                "emoji": "⛏️",
                "commands": [
                    {"name": "mine ", "shortcuts": ["`mn`", "`mining`"],
                     "desc": {"en": "Use your equipped pickaxe to mine for resources and earn Won", "ar": "استخدم معولك المجهز للتنقيب عن الموارد وكسب الـ Won", "fr": "Utilisez votre pioche équipée pour extraire des ressources et gagner du Won"},
                     "example": "sl mine"},
                    {"name": "pickadd ", "shortcuts": ["`pa`", "`pc`"],
                     "desc": {"en": "Equip a specific pickaxe for mining", "ar": "جهّز معولًا معينًا للتعدين", "fr": "Équipez une pioche spécifique pour le minage"},
                     "example": "sl pickadd iron_pickaxe"}
                ]
            },
            "Fight Commands": {
                "title": {"en": "Fight Commands", "ar": "أوامر القتال", "fr": "Commandes de combat"},
                "description": {
                    "en": "Engage in epic battles and challenge other hunters.",
                    "ar": "اخُض معارك ملحمية وتحدَّ صيادين آخرين.",
                    "fr": "Engagez des combats épiques et défiez d’autres chasseurs."
                },
                "emoji": "⚔️",
                "commands": [
                    {"name": "challenge ", "shortcuts": ["`ch`", "`chl`"],
                     "desc": {"en": "Challenge other hunters", "ar": "تحدَّ صيادين آخرين", "fr": "Défiez d’autres chasseurs"},
                     "example": "sl challenge @username"},
                    {"name": "fight ", "shortcuts": ["`f`", "`fg`"],
                     "desc": {"en": "Start a hunter battle", "ar": "ابدأ معركة صياد", "fr": "Démarrer un combat de chasseur"},
                     "example": "sl fight"}
                ]
            },
            "Referral Commands": {
                "title": {"en": "Referral Commands", "ar": "أوامر الإحالة", "fr": "Commandes de parrainage"},
                "description": {
                    "en": "Build your network and earn rewards through referrals.",
                    "ar": "ابنِ شبكتك واحصل على مكافآت عبر الإحالات.",
                    "fr": "Développez votre réseau et gagnez des récompenses via les parrainages."
                },
                "emoji": "🔗",
                "commands": [
                    {"name": "refactivate ", "shortcuts": ["`ra`", "`rfa`"],
                     "desc": {"en": "Activate your referral system to start referring others", "ar": "فعّل نظام الإحالة لديك لبدء إحالة الآخرين", "fr": "Activez votre système de parrainage pour commencer à inviter"},
                     "example": "sl refactivate"},
                    {"name": "referralstat ", "shortcuts": ["`rft`", "`rfs`"],
                     "desc": {"en": "View your referral status and statistics", "ar": "اعرض حالة الإحالة والإحصاءات الخاصة بك", "fr": "Voir votre statut et vos statistiques de parrainage"},
                     "example": "sl referralstat"},
                    {"name": "ref ", "shortcuts": ["`re`", "`rf`"],
                     "desc": {"en": "Refer another hunter to join under you", "ar": "أحِل صيادًا آخر لينضم تحتك", "fr": "Parrainez un autre chasseur pour qu’il vous rejoigne"},
                     "example": "sl ref @username"}
                ]
            },
            "Inventory Commands": {
                "title": {"en": "Inventory Commands", "ar": "أوامر الجرد", "fr": "Commandes d’inventaire"},
                "description": {
                    "en": "Manage your hunter's equipment, items, and resources.",
                    "ar": "أدِر معدات الصياد والعناصر والموارد الخاصة بك.",
                    "fr": "Gérez l’équipement, les objets et les ressources de votre chasseur."
                },
                "emoji": "🎒",
                "commands": [
                    {"name": "capsule ", "shortcuts": ["`cap`", "`cp`"],
                     "desc": {"en": "Open item capsules", "ar": "افتح كبسولات العناصر", "fr": "Ouvrir des capsules d’objets"},
                     "example": "sl capsule acap/dcap/scap/hcap character name"},
                    {"name": "skip_dungeon ", "shortcuts": ["`sd`", "`skd`"],
                     "desc": {"en": "Skips to the dungeon's boss after you beat the first round", "ar": "يتجاوز إلى زعيم الزنزانة بعد الفوز بالجولة الأولى", "fr": "Passe directement au boss du donjon après la première manche"},
                     "example": "sl skip_dungeon/sd/skd"},
                    {"name": "charm ", "shortcuts": ["`chm`", "`chr`"],
                     "desc": {"en": "Enhance your character's chosen stat by 20%", "ar": "عزّز الإحصائية المختارة لشخصيتك بنسبة 20%", "fr": "Améliore la statistique choisie de votre personnage de 20 %"},
                     "example": "sl charm attack/health/defence/speed character name"},
                    {"name": "enhance ", "shortcuts": ["`enh`", "`en`"],
                     "desc": {"en": "Enhance your character's XP & Affinity gains by 20%", "ar": "عزّز مكاسب الخبرة لشخصيتك بنسبة 20%", "fr": "Augmente de 20 % les gains d’XP de votre personnage"},
                     "example": "sl enhance character"},
                    {"name": "equip ", "shortcuts": ["`eq`", "`ep`"],
                     "desc": {"en": "Equip weapons from characters", "ar": "جهّز الأسلحة للشخصيات", "fr": "Équiper des armes aux personnages"},
                     "example": "sl equip legendary_sword, character_name"},
                    {"name": "inv ", "shortcuts": ["`inv`", "`i`"],
                     "desc": {"en": "View your inventory", "ar": "اعرض جردك", "fr": "Voir votre inventaire"},
                     "example": "sl inv"},
                    {"name": "invbooster ", "shortcuts": ["`ib`", "`ibt`"],
                     "desc": {"en": "View your boosters", "ar": "اعرض المعززات الخاصة بك", "fr": "Voir vos boosters"},
                     "example": "sl invbooster"},
                    {"name": "invchest ", "shortcuts": ["`ic`", "`ich`"],
                     "desc": {"en": "View your chests", "ar": "اعرض صدورك", "fr": "Voir vos coffres"},
                     "example": "sl invchest"},
                    {"name": "invkey ", "shortcuts": ["`ik`", "`ikey`"],
                     "desc": {"en": "View your keys", "ar": "اعرض مفاتيحك", "fr": "Voir vos clés"},
                     "example": "sl invkey"},
                    {"name": "invstone ", "shortcuts": ["`is`", "`ist`"],
                     "desc": {"en": "View your stones", "ar": "اعرض أحجارك", "fr": "Voir vos pierres"},
                     "example": "sl invstone / sl invstone s/a/b/c/d/e"},
                    {"name": "invweapon ", "shortcuts": ["`iw`", "`iwp`"],
                     "desc": {"en": "View your weapons", "ar": "اعرض أسلحتك", "fr": "Voir vos armes"},
                     "example": "sl invweapon"},
                    {"name": "loot ", "shortcuts": ["`lt`", "`lo`"],
                     "desc": {"en": "View and collect loot", "ar": "اعرض الغنائم واجمعها", "fr": "Voir et récupérer le butin"},
                     "example": "sl loot chest_name 3"},
                    {"name": "reset ", "shortcuts": ["`rst`", "`rt`"],
                     "desc": {"en": "Reset timers", "ar": "إعادة تعيين المؤقتات", "fr": "Réinitialiser les minuteries"},
                     "example": "sl reset mission/dungeon/fight/rolls"},
                    {"name": "rstat ", "shortcuts": ["`rs`", "`rts`"],
                     "desc": {"en": "Resets your character's manually assigned stat points", "ar": "يعيد تعيين نقاط الإحصائيات المضافة يدويًا لشخصيتك", "fr": "Réinitialise les points de statistiques attribués manuellement"},
                     "example": "sl rstat character"},
                    {"name": "sell ", "shortcuts": ["`sl`", "`se`"],
                     "desc": {"en": "Sell Essence Stones from inventory", "ar": "بع أحجار الجوهر من الجرد", "fr": "Vendre des pierres d’essence depuis l’inventaire"},
                     "example": "sl sell Izawa"},
                    {"name": "viewlocked ", "shortcuts": ["`vl`", "`vlk`"],
                    "desc": {"en": "View your locked fragments", "ar": "اعرض الشظايا المقفلة لديك", "fr": "Afficher vos fragments verrouillés"},
                    "example": "sl viewlocked"},

                    {"name": "lockfrag ", "shortcuts": ["`lf`", "`lfg`"],
                    "desc": {"en": "Lock a fragment to prevent accidental selling", "ar": "اقفل شظية لمنع بيعها بالخطأ", "fr": "Verrouiller un fragment pour éviter une vente accidentelle"},
                    "example": "sl lockfrag fragment_name"},

                    {"name": "unlockfrag ", "shortcuts": ["`uf`", "`ulk`"],
                    "desc": {"en": "Unlock a fragment so it can be sold", "ar": "افتح قفل شظية للسماح ببيعها", "fr": "Déverrouiller un fragment pour qu’il puisse être vendu"},
                    "example": "sl unlockfrag fragment_name"},
                    {"name": "unequip ", "shortcuts": ["`ue`", "`uq`"],
                     "desc": {"en": "Unequip weapons from characters", "ar": "أزل تجهيز الأسلحة من الشخصيات", "fr": "Retirer les armes des personnages"},
                     "example": "sl unequip weapon, character_name"},
                    {"name": "xpboost ", "shortcuts": ["`xb`", "`xp`"],
                     "desc": {"en": "Boosts your character's xp", "ar": "يعزز خبرة شخصيتك", "fr": "Augmente l’XP de votre personnage"},
                     "example": "sl xpboost character name quantity(Optional)"}
                ]
            },
            
            "Halloween Commands": {
                "title": {"en": "Halloween Commands", "ar": "أوامر الهالوين", "fr": "Commandes d’Halloween"},
                "description": {
                "en": "Limited-time Halloween actions for event boosts, capsules, skins, and inventory.",
                "ar": "إجراءات موسمية خاصة بالهالوين للمعززات والكبسولات والسكنات والجرد.",
                "fr": "Actions saisonnières d’Halloween pour boosts, capsules, skins et inventaire."
                },
                "emoji": "🎃",
                "commands": [
                {"name": "hcapsule ", "shortcuts": ["`hcap`", "`hcp`"],
                "desc": {"en": "Open item capsules (+2 per use)", "ar": "افتح كبسولات العناصر (+2 في كل استخدام)", "fr": "Ouvrir des capsules d’objets (+2 par utilisation)"},
                "example": "sl hcapsule acap/dcap/scap/hcap character_name"},

                {"name": "hcharm ", "shortcuts": ["`hchm`", "`hchr`"],
                "desc": {"en": "Enhance chosen stat by 35% for three fights", "ar": "عزّز الإحصائية المختارة بنسبة 35% لثلاث معارك", "fr": "Améliore la statistique choisie de 35 % pendant trois combats"},
                "example": "sl hcharm attack/health/defence/speed character_name"},

                {"name": "henhance ", "shortcuts": ["`henh`", "`hen`"],
                "desc": {"en": "Increase XP & Affinity gains by 20% for three fights", "ar": "زيادة مكاسب الخبرة والألفة بنسبة 20% لثلاث معارك", "fr": "Augmente les gains d’XP et d’affinité de 20 % pendant trois combats"},
                "example": "sl henhance character_name"},

                {"name": "hxpboost ", "shortcuts": ["`hxb`", "`hxp`"],
                "desc": {"en": "Grants +350 XP to the selected card", "ar": "يمنح +350 خبرة للبطاقة المحددة", "fr": "Accorde +350 XP à la carte sélectionnée"},
                "example": "sl hxpboost character_name quantity(Optional)"},
                {"name": "hequip ", "shortcuts": ["`heq`", "`hep`"],
                "desc": {
                    "en": "Equip a Halloween weapon to your character",
                    "ar": "جهّز سلاح هالوين لشخصيتك",
                    "fr": "Équiper une arme d’Halloween à votre personnage"
                },
                "example": "sl hequip weapon_name, character_name"},

                {"name": "hunequip ", "shortcuts": ["`huq`", "`hue`"],
                "desc": {
                    "en": "Unequip your currently equipped Halloween weapon",
                    "ar": "أزل سلاح الهالوين المجهّز حاليًا",
                    "fr": "Retirer l’arme d’Halloween actuellement équipée"
                },
                "example": "sl hunequip character_name"},
                {"name": "skinequip ", "shortcuts": ["`skequip`", "`sek`"],
                "desc": {"en": "Equip a skin (card art) to a character/card", "ar": "جهّز سكنًا (صورة البطاقة) لشخصية/بطاقة", "fr": "Équiper un skin (illustration de carte) à un personnage/une carte"},
                "example": "sl skinequip skin_name, character_name"},

                {"name": "skinunequip ", "shortcuts": ["`skunequip`", "`suk`"],
                "desc": {"en": "Unequip the currently equipped skin", "ar": "أزل السكن المجهّز حاليًا", "fr": "Retirer le skin actuellement équipé"},
                "example": "sl skinunequip character_name"},

                {"name": "invskins ", "shortcuts": ["`ikn`", "`iskins`"],
                "desc": {"en": "View your owned skins", "ar": "اعرض السكنات التي تملكها", "fr": "Voir vos skins possédés"},
                "example": "sl invskins"}
                ]
            },
            "Shop Commands": {
                "title": {"en": "Shop Commands", "ar": "أوامر المتجر", "fr": "Commandes de boutique"},
                "description": {
                    "en": "Purchase items and gear to enhance your hunter abilities.",
                    "ar": "اشترِ العناصر والعتاد لتعزيز قدراتك كصيّاد.",
                    "fr": "Achetez des objets et de l’équipement pour améliorer vos capacités."
                },
                "emoji": "🛒",
                "commands": [
                    {"name": "buy ", "shortcuts": ["`by`", "`bu`"],
                     "desc": {"en": "Buy items from the shop", "ar": "اشترِ عناصر من المتجر", "fr": "Acheter des objets à la boutique"},
                     "example": "sl buy legendary_sword quantity"},
                    {"name": "shop ", "shortcuts": ["`sh`", "`sp`"],
                     "desc": {"en": "View shop inventory", "ar": "اعرض مخزون المتجر", "fr": "Voir l’inventaire de la boutique"},
                     "example": "sl shop"}
                ]
            },
            "Spawn Commands": {
                "title": {"en": "Spawn Commands", "ar": "أوامر الاستدعاء", "fr": "Commandes d’invocation"},
                "description": {
                    "en": "Summon and manage your hunter cards and weapons.",
                    "ar": "استدعِ وأدر بطاقات الصياد والأسلحة.",
                    "fr": "Invoquez et gérez vos cartes et armes de chasseur."
                },
                "emoji": "🃏",
                "commands": [
                    {"name": "arise ", "shortcuts": ["`ars`", "`ar`"],
                     "desc": {"en": "Summon new hunter cards", "ar": "استدعِ بطاقات صياد جديدة", "fr": "Invoquer de nouvelles cartes de chasseur"},
                     "example": "sl arise"},
                    {"name": "cardinfo ", "shortcuts": ["`ci`", "`cif`"],
                     "desc": {"en": "View detailed card information", "ar": "عرض معلومات البطاقة التفصيلية", "fr": "Voir les informations détaillées d’une carte"},
                     "example": "sl carddetails legendary_hunter"},
                    {"name": "cardlist ", "shortcuts": ["`cl`", "`clt`"],
                     "desc": {"en": "View all available cards", "ar": "عرض جميع البطاقات المتاحة", "fr": "Voir toutes les cartes disponibles"},
                     "example": "sl cardlist/ sl cardlist rank s / sl cardlist offence"},
                    {"name": "cardview ", "shortcuts": ["`cv`", "`cvw`"],
                     "desc": {"en": "View specific card details", "ar": "عرض تفاصيل بطاقة معينة", "fr": "Voir les détails d’une carte spécifique"},
                     "example": "sl cardview shadow_monarch"},
                    {"name": "mycards ", "shortcuts": ["`mc`", "`myc`"],
                     "desc": {"en": "Display your card collection", "ar": "اعرض مجموعة بطاقاتك", "fr": "Afficher votre collection de cartes"},
                     "example": "sl mycards/ sl mycards rank s / sl mycards attack / sl mycards support"},
                    {"name": "myweapons ", "shortcuts": ["`mw`", "`myw`"],
                     "desc": {"en": "Show your weapons", "ar": "اعرض أسلحتك", "fr": "Afficher vos armes"},
                     "example": "sl myweapons"},
                    {"name": "weaponlist ", "shortcuts": ["`wl`", "`wlt`"],
                     "desc": {"en": "View all available weapons", "ar": "عرض جميع الأسلحة المتاحة", "fr": "Voir toutes les armes disponibles"},
                     "example": "sl weaponlist"},
                    {"name": "myhweapons ", "shortcuts": ["`mhw`", "`myhw`"],
                        "desc": {"en": "Show your Halloween event weapons", "ar": "اعرض أسلحة الهالوين الخاصة بك", "fr": "Afficher vos armes d’Halloween"},
                        "example": "sl myhweapons"},
                    {"name": "weaponview ", "shortcuts": ["`wv`", "`wvw`"],
                     "desc": {"en": "View specific weapon details", "ar": "عرض تفاصيل سلاح معين", "fr": "Voir les détails d’une arme spécifique"},
                     "example": "sl weaponview shadow_blade"}
                ]
            },
            "Team Commands": {
                "title": {"en": "Team Commands", "ar": "أوامر الفريق", "fr": "Commandes d’équipe"},
                "description": {
                    "en": "Create and manage your hunter team.",
                    "ar": "أنشئ فريقك وأدره.",
                    "fr": "Créez et gérez votre équipe de chasseurs."
                },
                "emoji": "👥",
                "commands": [
                    {"name": "add ", "shortcuts": ["`ad`", "`tad`"],
                     "desc": {"en": "Add a member to your team", "ar": "أضف عضوًا إلى فريقك", "fr": "Ajouter un membre à votre équipe"},
                     "example": "sl add hunter_name"},
                    {"name": "remove ", "shortcuts": ["`rm`", "`trm`"],
                     "desc": {"en": "Remove a team member", "ar": "أزل عضوًا من فريقك", "fr": "Retirer un membre de l’équipe"},
                     "example": "sl remove hunter_name"},
                    {"name": "swap ", "shortcuts": ["`sw`", "`tsw`"],
                     "desc": {"en": "Swap team member positions", "ar": "بدّل مواقع أعضاء الفريق", "fr": "Échanger les positions des membres"},
                     "example": "sl swap first_hunter,second_hunter"},
                    {"name": "team ", "shortcuts": ["`tv`", "`tvw`"],
                     "desc": {"en": "View your current team", "ar": "اعرض فريقك الحالي", "fr": "Voir votre équipe actuelle"},
                     "example": "sl team/sl team team_id(optional)"},
                    {"name": "myteams ", "shortcuts": ["`mts`", "`teams`"],
                    "desc": {"en": "View your saved team presets", "ar": "اعرض تشكيلات الفرق المحفوظة لديك", "fr": "Voir vos compositions d’équipe enregistrées"},
                    "example": "sl myteams"},

                    {"name": "cteam ", "shortcuts": ["`ct`", "`createteam`"],
                    "desc": {"en": "Create/save a team preset from your current team", "ar": "أنشئ/احفظ تشكيل فريق من فريقك الحالي", "fr": "Créer/enregistrer une composition à partir de votre équipe actuelle"},
                    "example": "sl cteam team_name"},

                    {"name": "steam ", "shortcuts": ["`sts`", "`setteam`"],
                    "desc": {"en": "Set your active team to a saved preset", "ar": "عيّن فريقك النشط إلى تشكيل محفوظ", "fr": "Définir votre équipe active sur une composition enregistrée"},
                    "example": "sl steam team_name"}
                ]
            },
            "Tutorial Commands": {
                "title": {"en": "Tutorial Commands", "ar": "أوامر الشرح والتدريب", "fr": "Commandes de tutoriel"},
                "description": {
                    "en": "Start the guided tutorial to learn the bot systems.",
                    "ar": "ابدأ الشرح التوجيهي لتعلّم أنظمة البوت.",
                    "fr": "Démarrez le tutoriel guidé pour apprendre les systèmes du bot."
                },
                "emoji": "📘",
                "commands": [
                    {"name": "tutorial ", "shortcuts": ["`tt`", "`ttl`"],
                    "desc": {
                        "en": "View tutorial information and available steps",
                        "ar": "اعرض معلومات الشرح والخطوات المتاحة",
                        "fr": "Voir les informations du tutoriel et les étapes disponibles"
                    },
                    "example": "sl tutorial"},

                    {"name": "tutostart ", "shortcuts": ["`tst`", "`tts`"],
                    "desc": {
                        "en": "Start the interactive tutorial",
                        "ar": "ابدأ الشرح التفاعلي",
                        "fr": "Démarrer le tutoriel interactif"
                    },
                    "example": "sl tutostart"}
                ]
            },

            "Premium Commands": {
                "title": {"en": "Premium Commands", "ar": "أوامر بريميوم", "fr": "Commandes Premium"},
                "description": {
                    "en": "Exclusive features for premium members and tier subscribers.",
                    "ar": "مزايا حصرية لأعضاء البريميوم ومشتركي المستويات.",
                    "fr": "Fonctionnalités exclusives pour les membres premium et abonnés."
                },
                "emoji": "👑",
                "commands": [
                    {"name": "iedit ", "shortcuts": ["`ied`", "`ie`"],
                     "desc": {"en": "Edit card images using anime cards from inventory", "ar": "حرّر صور البطاقات باستخدام بطاقات الأنمي من الجرد", "fr": "Modifier les images des cartes avec des cartes anime de l’inventaire"},
                     "example": "sl iedit card_name, anime_card_name"},
                    {"name": "imremove ", "shortcuts": ["`imr`", "`ire`"],
                     "desc": {"en": "Remove custom image from a card", "ar": "أزل الصورة المخصصة من بطاقة", "fr": "Supprimer l’image personnalisée d’une carte"},
                     "example": "sl imremove card_name"},
                    {"name": "nedit ", "shortcuts": ["`ned`", "`ne`"],
                     "desc": {"en": "Edit card names with custom pseudo names", "ar": "حرّر أسماء البطاقات بأسماء مستعارة مخصصة", "fr": "Modifier les noms de cartes avec des pseudonymes personnalisés"},
                     "example": "sl nedit card_name, new_name"},
                    {"name": "nremove ", "shortcuts": ["`nrm`", "`nr`"],
                     "desc": {"en": "Remove custom name from a card", "ar": "أزل الاسم المخصص من بطاقة", "fr": "Supprimer le nom personnalisé d’une carte"},
                     "example": "sl nremove card_name"},
                    {"name": "emedit ", "shortcuts": ["`eme`", "`ee`"],
                     "desc": {"en": "Edit card emotes for different stats", "ar": "حرّر إيموجيات البطاقة للإحصائيات المختلفة", "fr": "Modifier les émojis de carte pour différentes stats"},
                     "example": "sl emedit card_name attack :custom_emote:"},
                    {"name": "creset ", "shortcuts": ["`cr`", "`cre`"],
                     "desc": {"en": "Reset card emotes to default", "ar": "أعد تعيين إيموجيات البطاقة للوضع الافتراضي", "fr": "Réinitialiser les émojis de carte par défaut"},
                     "example": "sl remote card_name all"},
                    {"name": "store ", "shortcuts": ["`str`", "`st`"],
                     "desc": {"en": "Access the premium anime card store", "ar": "ادخل متجر بطاقات الأنمي البريميوم", "fr": "Accéder à la boutique premium de cartes anime"},
                     "example": "sl store"},
                    {"name": "purchase ", "shortcuts": ["`pur`", "`pr`"],
                     "desc": {"en": "Purchase anime cards from the store", "ar": "اشترِ بطاقات الأنمي من المتجر", "fr": "Acheter des cartes anime depuis la boutique"},
                     "example": "sl purchase card_name 3"},
                    {"name": "invcards ", "shortcuts": ["`inc`", "`icd`"],
                     "desc": {"en": "View your anime card inventory", "ar": "اعرض جرد بطاقات الأنمي لديك", "fr": "Voir votre inventaire de cartes anime"},
                     "example": "sl invcards"},
                    {"name": "mp ", "shortcuts": ["`mpa`"],
                     "desc": {"en": "Arise multiple cards at once", "ar": "استدعِ عدة بطاقات دفعة واحدة", "fr": "Invoquer plusieurs cartes à la fois"},
                     "example": "sl mp"}
                ]
            },
            "Trade Commands": {
                "title": {"en": "Trade Commands", "ar": "أوامر التداول", "fr": "Commandes d’échange"},
                "description": {
                    "en": "Trade items and weapons with other hunters.",
                    "ar": "تاجر بالعناصر والأسلحة مع الصيادين الآخرين.",
                    "fr": "Échangez des objets et des armes avec d’autres chasseurs."
                },
                "emoji": "🤝",
                "commands": [
                    {"name": "iadd ", "shortcuts": ["`ia`", "`ida`"],
                     "desc": {"en": "Add items to trade", "ar": "أضف عناصر إلى التداول", "fr": "Ajouter des objets à l’échange"},
                     "example": "sl iadd item_name quantity"},
                    {"name": "iremove ", "shortcuts": ["`ir`", "`irm`"],
                     "desc": {"en": "Remove items from trade", "ar": "أزل عناصر من التداول", "fr": "Retirer des objets de l’échange"},
                     "example": "sl iremove item_name"},
                    {"name": "trade ", "shortcuts": ["`tr`", "`td`"],
                     "desc": {"en": "Initiate a trade", "ar": "ابدأ عملية تداول", "fr": "Initier un échange"},
                     "example": "sl trade @username"},
                    {"name": "wadd ", "shortcuts": ["`wa`", "`wda`"],
                     "desc": {"en": "Add Won Currency to trade", "ar": "أضف عملة الـ Won إلى التداول", "fr": "Ajouter de la monnaie Won à l’échange"},
                     "example": "sl wadd amount"},
                    {"name": "wremove ", "shortcuts": ["`wr`", "`wrm`"],
                     "desc": {"en": "Remove Won Currency from trade", "ar": "أزل عملة الـ Won من التداول", "fr": "Retirer de la monnaie Won de l’échange"},
                     "example": "sl wremove amount"}
                ]
            },
            "Adventure Commands": {
                "title": {"en": "Adventure Commands", "ar": "أوامر المغامرة", "fr": "Commandes d’aventure"},
                "description": {
                    "en": "Take on special challenges and explore new adventures.",
                    "ar": "واجه تحديات خاصة واستكشف مغامرات جديدة.",
                    "fr": "Relevez des défis spéciaux et explorez de nouvelles aventures."
                },
                "emoji": "🌍",
                "commands": [
                    {"name": "adventure ", "shortcuts": ["`adv`", "`advr`"],
                     "desc": {"en": "Start a new adventure and explore new challenges", "ar": "ابدأ مغامرة جديدة واستكشف تحديات جديدة", "fr": "Commencez une nouvelle aventure et explorez de nouveaux défis"},
                     "example": "sl adventure"}
                ]
            },
            "Market Commands": {
                "title": {"en": "Market Commands", "ar": "أوامر السوق", "fr": "Commandes du marché"},
                "description": {
                    "en": "Buy, sell, and manage marketplace listings with other hunters.",
                    "ar": "اشترِ وبِع وأدر قوائم السوق مع الصيادين الآخرين.",
                    "fr": "Achetez, vendez et gérez des annonces sur le marché."
                },
                "emoji": "🏪",
                "commands": [
                    {"name": "madd ", "shortcuts": ["`mad`", "`ma`"],
                     "desc": {"en": "Add item to current market listing", "ar": "أضف عنصرًا إلى قائمة السوق الحالية", "fr": "Ajouter un objet à votre annonce actuelle"},
                     "example": "sl madd legendary_sword,5"},
                    {"name": "mremove ", "shortcuts": ["`mrm`", "`mr`"],
                     "desc": {"en": "Remove item from current market listing", "ar": "أزل عنصرًا من قائمة السوق الحالية", "fr": "Retirer un objet de votre annonce"},
                     "example": "sl mremove legendary_sword"},
                    {"name": "market ", "shortcuts": ["`mk`", "`mkt`"],
                     "desc": {"en": "View the market", "ar": "اعرض السوق", "fr": "Voir le marché"},
                     "example": "sl market"},
                    {"name": "mbuy ", "shortcuts": ["`mby`", "`mb`"],
                     "desc": {"en": "Buy a listing using the new ID format (e.g., a12, b5, c3, d4)", "ar": "اشترِ قائمة باستخدام صيغة المعرّف الجديدة (مثل a12، b5، c3، d4)", "fr": "Acheter une annonce via le nouvel ID (ex : a12, b5, c3, d4)"},
                     "example": "sl mbuy a12"},
                    {"name": "mcreate ", "shortcuts": ["`mcr`", "`mkr`"],
                     "desc": {"en": "Create a new market listing", "ar": "أنشئ قائمة سوق جديدة", "fr": "Créer une nouvelle annonce sur le marché"},
                     "example": "sl mcreate"},
                    {"name": "mdelist ", "shortcuts": ["`mdl`", "`mds`"],
                     "desc": {"en": "Delist a listing using the new ID format (e.g., a12, b5, c3, d4)", "ar": "ألغِ إدراج قائمة باستخدام صيغة المعرّف الجديدة", "fr": "Retirer une annonce via le nouvel ID"},
                     "example": "sl mdelist a12"},
                    {"name": "mlist ", "shortcuts": ["`mlt`", "`mls`"],
                     "desc": {"en": "Search and list all market listings for a specific item", "ar": "ابحث واعرض كل قوائم السوق لعنصر محدد", "fr": "Rechercher et lister toutes les annonces pour un objet spécifique"},
                     "example": "sl mlist legendary_sword"},
                    {"name": "mylist ", "shortcuts": ["`myl`", "`myt`"],
                     "desc": {"en": "List user's listings by category", "ar": "اعرض قوائم المستخدم بحسب الفئة", "fr": "Lister vos annonces par catégorie"},
                     "example": "sl mylist weapon"}
                ]
            },
            "Skill Commands": {
                "title": {"en": "Skill Commands", "ar": "أوامر المهارات", "fr": "Commandes de compétences"},
                "description": {
                    "en": "Equip and manage skills for your hunter cards.",
                    "ar": "جهّز وأدر المهارات لبطاقات الصياد لديك.",
                    "fr": "Équipez et gérez des compétences pour vos cartes."
                },
                "emoji": "⚡",
                "commands": [
                    {"name": "sequip ", "shortcuts": ["`seq`", "`ske`"],
                     "desc": {"en": "Equip a skill to a card", "ar": "جهّز مهارة لبطاقة", "fr": "Équiper une compétence à une carte"},
                     "example": "sl sequip fireball,shadow_monarch"},
                    {"name": "sunequip ", "shortcuts": ["`sq`", "`sku`"],
                     "desc": {"en": "Unequip a skill from a card", "ar": "أزل مهارة من بطاقة", "fr": "Retirer une compétence d’une carte"},
                     "example": "sl sunequip fireball, shadow_monarch"}
                ]
            },
            "Ascend Commands": {
                "title": {"en": "Ascend Commands", "ar": "أوامر الترقي", "fr": "Commandes d’ascension"},
                "description": {
                    "en": "Upgrade your cards beyond their normal limits.",
                    "ar": "طوّر بطاقاتك إلى ما بعد حدودها المعتادة.",
                    "fr": "Améliorez vos cartes au-delà de leurs limites."
                },
                "emoji": "🌟",
                "commands": [
                    {"name": "ascend ", "shortcuts": ["`asc`", "`as`"],
                     "desc": {"en": "Upgrade your card past its limits", "ar": "طوّر بطاقتك متجاوزًا حدودها", "fr": "Améliorer votre carte au-delà de ses limites"},
                     "example": "sl ascend shadow_monarch"}
                ]
            }
        }

    @commands.command(name='help', aliases=['h', 'commands'])
    async def help_command(self, ctx, *args):
        """
        Displays the help menu or details for a specific command (localized).
        """
        lang = await _get_lang(ctx.author.id)

        # If a specific command is requested
        if args:
            cmd_name = args[0].lower()
            for _, data in self.categories.items():
                for cmd in data['commands']:
                    if (cmd_name == cmd['name'].strip().lower()
                        or any(cmd_name == s.strip('`').lower() for s in cmd['shortcuts'])):
                        embed = discord.Embed(
                            title=_tr(lang, "📘 Command Details", "📘 تفاصيل الأمر", "📘 Détails de la commande"),
                            color=discord.Color.blue()
                        )
                        embed.add_field(name=_tr(lang, "Name", "الاسم", "Nom"),
                                        value=cmd['name'], inline=False)
                        embed.add_field(name=_tr(lang, "Shortcuts", "الاختصارات", "Raccourcis"),
                                        value=' | '.join(cmd['shortcuts']), inline=False)
                        embed.add_field(name=_tr(lang, "Description", "الوصف", "Description"),
                                        value=pick(lang, cmd['desc']), inline=False)
                        embed.add_field(name=_tr(lang, "Example Usage", "مثال الاستخدام", "Exemple d’utilisation"),
                                        value=cmd['example'], inline=False)
                        await ctx.send(embed=embed)
                        return

            await ctx.send(_tr(lang,
                               "❌ No command found with the name or shortcut specified",
                               "❌ لم يتم العثور على أمر بالاسم أو الاختصار المحدد",
                               "❌ Aucune commande trouvée avec ce nom ou ce raccourci"))
            return

        # Default help menu
        help_view = SoloLevelingHelpView(self.categories, ctx.author, lang)
        help_view.message = await ctx.send(embed=help_view.get_initial_embed(), view=help_view)

class SoloLevelingHelpView(discord.ui.View):
    def __init__(self, categories: Dict[str, Dict[str, Any]], author: discord.User, lang: int):
        super().__init__(timeout=180)  # 3 minutes timeout
        self.categories = categories
        self.current_category_key: Optional[str] = None
        self.current_page = 0
        self.message: Optional[discord.Message] = None
        self.author = author
        self.lang = lang

        # Color palette
        self.colors = {
            "primary": discord.Color.from_rgb(87, 99, 255),      # Soft blue
            "secondary": discord.Color.from_rgb(120, 130, 230),  # Lighter blue
            "background": discord.Color.from_rgb(240, 242, 255)  # Very light blue
        }

        # Enhanced navigation
        self.add_item(self.create_category_select())
        self.create_navigation_buttons()

    def create_navigation_buttons(self):
        self.prev_button = discord.ui.Button(
            label=_tr(self.lang, "◀️ Previous", "◀️ السابق", "◀️ Précédent"),
            style=discord.ButtonStyle.gray,
            custom_id="prev"
        )
        self.next_button = discord.ui.Button(
            label=_tr(self.lang, "Next ▶️", "التالي ▶️", "Suivant ▶️"),
            style=discord.ButtonStyle.gray,
            custom_id="next"
        )
        self.back_button = discord.ui.Button(
            label=_tr(self.lang, "↩️ Categories", "↩️ الفئات", "↩️ Catégories"),
            style=discord.ButtonStyle.blurple,
            custom_id="back"
        )

        self.prev_button.callback = self.previous_callback
        self.next_button.callback = self.next_callback
        self.back_button.callback = self.back_callback

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user != self.author:
            await interaction.response.send_message(
                _tr(self.lang,
                    f"🚫 Only {self.author.mention} can interact with this help menu.",
                    f"🚫 فقط {self.author.mention} يمكنه التفاعل مع قائمة المساعدة هذه.",
                    f"🚫 Seul {self.author.mention} peut interagir avec ce menu d’aide."),
                ephemeral=True
            )
            return False
        return True

    def get_initial_embed(self):
        embed = discord.Embed(
            title=_tr(self.lang,
                      "Solo Leveling Bot - Hunter Command Central",
                      "بوت Solo Leveling - مركز أوامر الصيّاد",
                      "Solo Leveling Bot - Centre des commandes du chasseur"),
            description=_tr(
                self.lang,
                (
                    "**Welcome, Emerging Hunter!**\n\n"
                    "Explore your abilities and master the command system:\n\n"
                    "• 🔍 Select a category from the dropdown\n\n"
                    "• 🔼 Navigate through commands\n\n"
                    "• 📚 Learn command details and examples\n\n"
                    "**Pro Tip:** Use `sl help <command>` for specific command details!"
                ),
                (
                    "**مرحبًا أيها الصيّاد الصاعد!**\n\n"
                    "استكشف قدراتك وأتقن نظام الأوامر:\n\n"
                    "• 🔍 اختر فئة من القائمة المنسدلة\n\n"
                    "• 🔼 تنقّل بين الأوامر\n\n"
                    "• 📚 اطّلع على التفاصيل والأمثلة\n\n"
                    "**نصيحة:** استخدم `sl help <command>` لتفاصيل أمر محدد!"
                ),
                (
                    "**Bienvenue, chasseur émergent !**\n\n"
                    "Explorez vos capacités et maîtrisez le système de commandes :\n\n"
                    "• 🔍 Sélectionnez une catégorie dans la liste\n\n"
                    "• 🔼 Naviguez entre les commandes\n\n"
                    "• 📚 Découvrez les détails et des exemples\n\n"
                    "**Astuce :** Utilisez `sl help <command>` pour un détail précis !"
                )
            ),
            color=self.colors["primary"]
        )
        embed.set_footer(
            text=_tr(self.lang,
                     "Your journey begins with knowledge. Choose wisely, Hunter!",
                     "رحلتك تبدأ بالمعرفة. اختر بحكمة أيها الصيّاد!",
                     "Votre voyage commence par la connaissance. Choisissez bien, chasseur !"),
        )
        return embed

    def create_category_select(self):
        options = []
        for key, cat in self.categories.items():
            options.append(
                discord.SelectOption(
                    label=pick(self.lang, cat["title"]),
                    value=key,
                    description=pick(self.lang, cat["description"])[:100],
                    emoji=cat.get("emoji", "📋")
                )
            )

        select = discord.ui.Select(
            placeholder=_tr(self.lang, "🔍 Select a Command Category", "🔍 اختر فئة الأوامر", "🔍 Sélectionnez une catégorie"),
            options=options,
            custom_id="category_select"
        )
        select.callback = self.category_select_callback
        return select

    def create_category_embed(self):
        if not self.current_category_key:
            return self.get_initial_embed()

        cat = self.categories[self.current_category_key]
        commands = cat['commands']
        description = pick(self.lang, cat['description'])
        title = f"{cat.get('emoji', '📋')} {pick(self.lang, cat['title'])}"

        commands_per_page = 5
        total_pages = (len(commands) - 1) // commands_per_page + 1

        start_index = self.current_page * commands_per_page
        end_index = start_index + commands_per_page
        page_commands = commands[start_index:end_index]

        # Localized field labels
        shortcuts_label = _tr(self.lang, "Shortcuts", "الاختصارات", "Raccourcis")
        description_label = _tr(self.lang, "Description", "الوصف", "Description")
        example_label = _tr(self.lang, "Example", "مثال", "Exemple")

        body = "\n\n".join([
            f"**`sl {cmd['name']}`**\n"
            f"**{shortcuts_label}:** {' | '.join(cmd['shortcuts'])}\n"
            f"📍 {pick(self.lang, cmd['desc'])}\n"
            f"🔹 {example_label}: **{cmd['example']}**"
            for cmd in page_commands
        ])

        embed = discord.Embed(
            title=title,
            description=f"*{description}*\n\n{body}",
            color=self.colors["secondary"]
        )
        embed.set_footer(
            text=_tr(self.lang, "Page", "الصفحة", "Page") + f" {self.current_page + 1}/{total_pages} • "
                 + _tr(self.lang, "Navigate with buttons", "تنقّل عبر الأزرار", "Naviguez avec les boutons")
        )
        return embed

    async def update_view(self, interaction: discord.Interaction):
        self.clear_items()

        if self.current_category_key:
            total_commands = len(self.categories[self.current_category_key]['commands'])
            commands_per_page = 5
            total_pages = (total_commands - 1) // commands_per_page + 1

            if total_pages > 1:
                self.add_item(self.prev_button)
                self.add_item(self.next_button)
            self.add_item(self.back_button)
        else:
            self.add_item(self.create_category_select())

        await interaction.response.edit_message(embed=self.create_category_embed(), view=self)

    async def category_select_callback(self, interaction: discord.Interaction):
        self.current_category_key = interaction.data['values'][0]
        self.current_page = 0
        await self.update_view(interaction)

    async def previous_callback(self, interaction: discord.Interaction):
        if self.current_page > 0:
            self.current_page -= 1
        await self.update_view(interaction)

    async def next_callback(self, interaction: discord.Interaction):
        total_commands = len(self.categories[self.current_category_key]['commands'])
        total_pages = (total_commands - 1) // 5 + 1

        if self.current_page < total_pages - 1:
            self.current_page += 1
        await self.update_view(interaction)

    async def back_callback(self, interaction: discord.Interaction):
        self.current_category_key = None
        self.current_page = 0
        await self.update_view(interaction)

async def setup(bot):
    await bot.add_cog(HelpCommands(bot))
