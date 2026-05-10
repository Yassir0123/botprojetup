# captcha.py
import asyncio, io, random, string, hashlib, datetime, collections
from PIL import Image, ImageDraw, ImageFont, ImageFilter
import discord
from discord.ext import commands
from utils.database import execute_query_with_retry, get_metrics, get_connection, release_connection
# === tune these ===
CAPTCHA_LEN = 6
CAPTCHA_TTL_SECONDS = 120
VERIFIED_WINDOW_MINUTES = 60
MAX_ATTEMPTS = 3
ACTIVITY_THRESHOLD = 15     # commands
WINDOW_MINUTES = 20         # rolling window
RANDOM_PROBABILITY = 0.03   # 3% chance

# in-memory stores (swap to Redis/DB if multi-process)
_sessions = {}       # user_id -> {hash, expires: dt, attempts: int}
_activity = {}       # user_id -> deque[datetime]

# ====== You already have these ======
# from your project:
#   - execute_query_with_retry(query, params)
#   - get_user_multilanguage(user_author_id)
#   - _tr(lang, en, ar, fr)
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
# ---------------------------
# Simple translation helpers
# ---------------------------
def _tr(lang: int, en: str, ar: str, fr: str) -> str:
    """Return a localized string based on lang (0=en, 1=ar, 3=fr)."""
    if lang == 1:
        return ar
    if lang == 3:
        return fr
    return en  # default EN
def _now(): return datetime.datetime.utcnow()

def _random_code(n=CAPTCHA_LEN):
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "".join(random.choice(alphabet) for _ in range(n))

def _render_png(text: str) -> bytes:
    W, H = 280, 100
    img = Image.new("RGB", (W, H), (245, 247, 250))
    d = ImageDraw.Draw(img)
    try: font = ImageFont.truetype("arial.ttf", 48)
    except: font = ImageFont.load_default()
    for _ in range(6):
        d.line(((random.randint(0,W),random.randint(0,H)),
                (random.randint(0,W),random.randint(0,H))), fill=(200,200,200), width=1)
    x = 20
    for ch in text:
        d.text((x, random.randint(10,35)), ch, font=font, fill=(20,20,20))
        x += random.randint(35,45)
    img = img.filter(ImageFilter.GaussianBlur(0.6))
    buf = io.BytesIO(); img.save(buf, "PNG")
    return buf.getvalue()

def _hash(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()

async def get_verified_until_from_user(user_authorid: int):
    rows = await execute_query_with_retry(
        "SELECT verified_until FROM user WHERE user_authorid = %s",
        (str(user_authorid),)
    )
    return rows[0]['verified_until'] if rows and rows[0]['verified_until'] else None

async def set_verified_until_on_user(user_authorid: int, until_dt: datetime.datetime):
    await execute_query_with_retry(
        "UPDATE user SET verified_until = %s WHERE user_authorid = %s",
        (until_dt, str(user_authorid))
    )

def _log_activity(user_id: int):
    dq = _activity.setdefault(user_id, collections.deque())
    now = _now()
    dq.append(now)
    # evict old
    cutoff = now - datetime.timedelta(minutes=WINDOW_MINUTES)
    while dq and dq[0] < cutoff:
        dq.popleft()

def _recent_activity_count(user_id: int) -> int:
    dq = _activity.get(user_id) or ()
    cutoff = _now() - datetime.timedelta(minutes=WINDOW_MINUTES)
    return sum(1 for t in dq if t >= cutoff)

def _new_session(code: str):
    return {"hash": _hash(code), "expires": _now() + datetime.timedelta(seconds=CAPTCHA_TTL_SECONDS), "attempts": MAX_ATTEMPTS}

def _set_session(user_id: int, code: str):
    _sessions[user_id] = _new_session(code)

def _clear_session(user_id: int):
    _sessions.pop(user_id, None)

def _check_session(user_id: int, submission: str) -> str:
    s = _sessions.get(user_id)
    if not s: return "no-session"
    if _now() > s["expires"]:
        _clear_session(user_id); return "expired"
    if _hash(submission) == s["hash"]:
        _clear_session(user_id); return "ok"
    s["attempts"] -= 1
    if s["attempts"] <= 0:
        _clear_session(user_id); return "locked"
    return "wrong"

class CaptchaView(discord.ui.View):
    def __init__(self, lang: int, owner_id: int, regen_cb):
        super().__init__(timeout=CAPTCHA_TTL_SECONDS)
        self.lang = lang; self.owner_id = owner_id; self.regen_cb = regen_cb

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message(
                _tr(self.lang, "This captcha isn’t for you.", "هذا الاختبار ليس لك.", "Ce captcha n’est pas pour vous."),
                ephemeral=True
            ); return False
        return True

    @discord.ui.button(label="Refresh", style=discord.ButtonStyle.secondary)
    async def refresh(self, interaction: discord.Interaction, button: discord.ui.Button):
        code, file = await self.regen_cb()
        _set_session(self.owner_id, code)
        embed = discord.Embed(
            title=_tr(self.lang, "Human Check", "تحقق من الإنسان", "Vérification humaine"),
            description=_tr(self.lang, "Type the code shown in the image below.", "اكتب الرمز الظاهر في الصورة أدناه.", "Tapez le code affiché sur l’image ci-dessous."),
            color=discord.Color.orange()
        )
        await interaction.response.edit_message(embed=embed, attachments=[file], view=self)

def requires_human(activity_threshold: int = ACTIVITY_THRESHOLD,
                   random_probability: float = RANDOM_PROBABILITY,
                   verified_minutes: int = VERIFIED_WINDOW_MINUTES,
                   force: bool = False):
    print("verif")
    def deco(func):
        async def wrapper(self, ctx: commands.Context, *args, **kwargs):
            lang = await get_user_multilanguage(ctx.author.id)

            # log + check activity
            _log_activity(ctx.author.id)
            recent = _recent_activity_count(ctx.author.id)
            threshold_hit = recent >= activity_threshold
            random_hit = (random.random() < random_probability)

            verified_until = await get_verified_until_from_user(ctx.author.id)
            still_verified = bool(verified_until and verified_until > _now())

            trigger = force or (not still_verified) or threshold_hit or random_hit
            if not trigger:
                return await func(self, ctx, *args, **kwargs)

            async def make_code_and_file():
                code = _random_code()
                png = _render_png(code)
                return code, discord.File(io.BytesIO(png), filename="captcha.png")

            code, file = await make_code_and_file()
            _set_session(ctx.author.id, code)

            embed = discord.Embed(
                title=_tr(lang, "Human Check", "تحقق من الإنسان", "Vérification humaine"),
                description=_tr(
                    lang,
                    f"Type the **{CAPTCHA_LEN}**-character code. You have **{MAX_ATTEMPTS}** attempts and **{CAPTCHA_TTL_SECONDS}** seconds.",
                    f"اكتب الرمز المكوّن من **{CAPTCHA_LEN}** أحرف. لديك **{MAX_ATTEMPTS}** محاولات و**{CAPTCHA_TTL_SECONDS}** ثانية.",
                    f"Saisissez le code de **{CAPTCHA_LEN}** caractères. **{MAX_ATTEMPTS}** essais, **{CAPTCHA_TTL_SECONDS}** secondes."
                ),
                color=discord.Color.orange()
            )
            view = CaptchaView(lang, ctx.author.id, regen_cb=make_code_and_file)
            msg = await ctx.send(embed=embed, file=file, view=view)

            def check(m: discord.Message):
                return m.author.id == ctx.author.id and m.channel.id == ctx.channel.id

            try:
                while True:
                    reply: discord.Message = await self.bot.wait_for("message", timeout=CAPTCHA_TTL_SECONDS, check=check)
                    verdict = _check_session(ctx.author.id, reply.content.strip().upper())
                    if verdict == "ok":
                        await set_verified_until_on_user(ctx.author.id, _now() + datetime.timedelta(minutes=verified_minutes))
                        try: await msg.delete()
                        except: pass
                        await ctx.send(_tr(lang, "✅ Verification passed. Continuing…", "✅ تم التحقق بنجاح. جارٍ المتابعة…", "✅ Vérification réussie. On continue…"))
                        return await func(self, ctx, *args, **kwargs)
                    elif verdict == "wrong":
                        left = _sessions.get(ctx.author.id, {}).get("attempts", 0)
                        await ctx.send(_tr(lang, f"Incorrect. Attempts left: {left}", f"غير صحيح. المحاولات المتبقية: {left}", f"Incorrect. Tentatives restantes : {left}"))
                    elif verdict == "locked":
                        await ctx.send(_tr(lang, "❌ Verification failed. Run the command again.", "❌ فشل التحقق. أعد تنفيذ الأمر.", "❌ Échec de vérification. Relancez la commande."))
                        return
                    else:  # expired/no-session
                        await ctx.send(_tr(lang, "Captcha expired. Run the command again.", "انتهت صلاحية الكابتشا. أعد تنفيذ الأمر.", "Captcha expiré. Relancez la commande."))
                        return
            except asyncio.TimeoutError:
                _clear_session(ctx.author.id)
                await ctx.send(_tr(lang, "⏰ Time’s up. Run the command again to verify.", "⏰ انتهى الوقت. أعد تنفيذ الأمر للتحقق.", "⏰ Temps écoulé. Relancez la commande pour vérifier."))
                return
        return wrapper
    return deco
