import os
import logging
import base64
import json
import re
import sqlite3
from datetime import datetime, timedelta, timezone

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

from openai import OpenAI

# ================== ENV ==================
TOKEN = os.environ.get("BOT_TOKEN", "").strip()
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "").strip()

# Admin (Required for VIP management commands)
# ضع رقمك من /myid داخل Render Environment
ADMIN_USER_ID_RAW = os.environ.get("ADMIN_USER_ID", "").strip()
ADMIN_ID = int(ADMIN_USER_ID_RAW) if ADMIN_USER_ID_RAW.isdigit() else None

# SQLite DB (use Render Persistent Disk if you want it to persist across deploys)
DB_PATH = os.environ.get("VIP_DB_PATH", "vip.db")

client = OpenAI(api_key=OPENAI_API_KEY)

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO
)
logger = logging.getLogger("TradingAI")


# ================== DB (VIP with expiry) ==================
def db_connect():
    return sqlite3.connect(DB_PATH, check_same_thread=False)

def db_init():
    con = db_connect()
    cur = con.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS vip_users (
            user_id INTEGER PRIMARY KEY,
            expires_at_utc TEXT NOT NULL
        )
    """)
    con.commit()
    con.close()

def set_vip(user_id: int, days: int):
    expires = datetime.now(timezone.utc) + timedelta(days=days)
    con = db_connect()
    cur = con.cursor()
    cur.execute(
        "INSERT INTO vip_users(user_id, expires_at_utc) VALUES(?, ?) "
        "ON CONFLICT(user_id) DO UPDATE SET expires_at_utc=excluded.expires_at_utc",
        (user_id, expires.isoformat())
    )
    con.commit()
    con.close()
    return expires

def remove_vip(user_id: int):
    con = db_connect()
    cur = con.cursor()
    cur.execute("DELETE FROM vip_users WHERE user_id = ?", (user_id,))
    con.commit()
    con.close()

def get_vip_expiry(user_id: int):
    con = db_connect()
    cur = con.cursor()
    cur.execute("SELECT expires_at_utc FROM vip_users WHERE user_id = ?", (user_id,))
    row = cur.fetchone()
    con.close()
    if not row:
        return None
    try:
        return datetime.fromisoformat(row[0]).astimezone(timezone.utc)
    except Exception:
        return None

def is_vip(user_id: int) -> bool:
    exp = get_vip_expiry(user_id)
    if not exp:
        return False
    return datetime.now(timezone.utc) < exp

def list_vips(limit: int = 50):
    con = db_connect()
    cur = con.cursor()
    cur.execute("SELECT user_id, expires_at_utc FROM vip_users ORDER BY expires_at_utc DESC LIMIT ?", (limit,))
    rows = cur.fetchall()
    con.close()
    out = []
    for uid, exp in rows:
        try:
            out.append((int(uid), datetime.fromisoformat(exp).astimezone(timezone.utc)))
        except Exception:
            out.append((int(uid), None))
    return out


# ================== Formatting Helpers ==================
def _clean(s: str) -> str:
    s = (s or "").strip()
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s

def _icon_action(v: str) -> str:
    v = (v or "").upper().strip()
    if v == "BUY":
        return "🟢 BUY"
    if v == "SELL":
        return "🔴 SELL"
    return "🟡 WAIT"

def _fmt(x: str, fallback: str) -> str:
    x = (x or "").strip()
    return x if x else fallback

def _fmt_prob(x, fallback: str) -> str:
    try:
        if x is None:
            return fallback
        p = int(float(x))
        p = max(0, min(100, p))
        return str(p)
    except Exception:
        return fallback

def _fmt_tips(tips, lang: str) -> str:
    if not isinstance(tips, list):
        return ""
    tips = [str(t).strip() for t in tips if str(t).strip()]
    if not tips:
        return ""
    title = "🧩 نصائح:" if lang == "ar" else "🧩 Tips:"
    bullets = "\n".join([f"• {t}" for t in tips[:3]])
    return f"{title}\n{bullets}\n"

def format_message(ar: dict, en: dict) -> str:
    ar_action = _icon_action(ar.get("action"))
    en_action = _icon_action(en.get("action"))

    ar_symbol = _fmt(ar.get("symbol"), "غير واضح")
    en_symbol = _fmt(en.get("symbol"), "Not clear")

    ar_tf = _fmt(ar.get("timeframe"), "غير واضح")
    en_tf = _fmt(en.get("timeframe"), "Not clear")

    ar_conf = _fmt(ar.get("confidence"), "غير واضح")
    en_conf = _fmt(en.get("confidence"), "Not clear")

    ar_prob = _fmt_prob(ar.get("probability"), "غير واضح")
    en_prob = _fmt_prob(en.get("probability"), "Not clear")

    # pattern only if clear (model decides; if unclear returns "غير واضح/Not clear")
    ar_pattern = _fmt(ar.get("pattern_name"), "غير واضح")
    en_pattern = _fmt(en.get("pattern_name"), "Not clear")

    ar_bias = _fmt(ar.get("pattern_bias"), "غير واضح")
    en_bias = _fmt(en.get("pattern_bias"), "Not clear")

    ar_key = _fmt(ar.get("key_level"), "غير واضح")
    en_key = _fmt(en.get("key_level"), "Not clear")

    ar_entry = _fmt(ar.get("entry"), "غير واضح")
    en_entry = _fmt(en.get("entry"), "Not clear")

    ar_sl = _fmt(ar.get("sl"), "غير واضح")
    en_sl = _fmt(en.get("sl"), "Not clear")

    ar_tp1 = _fmt(ar.get("tp1"), "غير واضح")
    en_tp1 = _fmt(en.get("tp1"), "Not clear")

    ar_tp2 = _fmt(ar.get("tp2"), "غير واضح")
    en_tp2 = _fmt(en.get("tp2"), "Not clear")

    ar_reason = _fmt(ar.get("reason"), "غير واضح")
    en_reason = _fmt(en.get("reason"), "Not clear")

    ar_wait_reason = _fmt(ar.get("wait_reason"), "غير واضح")
    en_wait_reason = _fmt(en.get("wait_reason"), "Not clear")

    ar_warning = _fmt(
        ar.get("warning"),
        "⚠️ تنبيه: التحليل تعليمي والنسبة تقديرية وليست ضمان. المخاطرة 1–2% فقط."
    )
    en_warning = _fmt(
        en.get("warning"),
        "⚠️ Warning: Educational only. Probability is an estimate (not guaranteed). Risk max 1–2%."
    )

    tips_ar = _fmt_tips(ar.get("tips"), "ar")
    tips_en = _fmt_tips(en.get("tips"), "en")

    msg = (
        "╭──────────────╮\n"
        "   🤖 Trading AI\n"
        "╰──────────────╯\n\n"
        f"{ar_action}\n"
        f"📌 الزوج: {ar_symbol}   ⏱️ {ar_tf}\n"
        f"⭐ الثقة: {ar_conf}   📊 الاحتمال: {ar_prob}%\n"
        f"🧩 النموذج: {ar_pattern} ({ar_bias})\n"
        f"🎯 مستوى مهم: {ar_key}\n"
    )

    if (ar.get("action") or "").upper().strip() == "WAIT":
        msg += (
            f"\n⏳ الانتظار لأن: {ar_wait_reason}\n"
            f"🧠 ملخص: {ar_reason}\n"
        )
    else:
        msg += (
            f"\n🎯 دخول: {ar_entry}\n"
            f"🛑 SL: {ar_sl}\n"
            f"✅ TP1: {ar_tp1}\n"
            f"✅ TP2: {ar_tp2}\n"
            f"🧠 السبب: {ar_reason}\n"
        )

    if tips_ar:
        msg += "\n" + tips_ar

    msg += "\n" + ar_warning + "\n\n" + "—" * 22 + "\n\n"

    msg += (
        f"{en_action}\n"
        f"📌 Pair: {en_symbol}   ⏱️ {en_tf}\n"
        f"⭐ Confidence: {en_conf}   📊 Probability: {en_prob}%\n"
        f"🧩 Pattern: {en_pattern} ({en_bias})\n"
        f"🎯 Key level: {en_key}\n"
    )

    if (en.get("action") or "").upper().strip() == "WAIT":
        msg += (
            f"\n⏳ Wait because: {en_wait_reason}\n"
            f"🧠 Summary: {en_reason}\n"
        )
    else:
        msg += (
            f"\n🎯 Entry: {en_entry}\n"
            f"🛑 SL: {en_sl}\n"
            f"✅ TP1: {en_tp1}\n"
            f"✅ TP2: {en_tp2}\n"
            f"🧠 Reason: {en_reason}\n"
        )

    if tips_en:
        msg += "\n" + tips_en

    msg += "\n" + en_warning
    return _clean(msg)


# ================== AI Prompts ==================
IMAGE_PROMPT = """
You are a conservative trading analyst focused on accuracy.

Rules:
- Do NOT mention a chart pattern unless it is clearly visible. If unclear, set pattern_name="Not clear/غير واضح".
- Even if pattern is unclear, you MUST still provide practical tips (confirmation, key levels, what to wait for).
- Provide a PROBABILITY estimate as a subjective confidence score (0–100). It is NOT guaranteed.
- If prices/levels are not readable, do NOT invent numbers: use "Not clear/غير واضح" and set action="WAIT".
- Use RSI + Stoch RSI as confirmation/timing, not the only reason.
- Prefer WAIT when confirmation is missing.
- Keep reason max 2 lines.

Output VALID JSON ONLY:

{
  "ar": {
    "symbol": "… or غير واضح",
    "timeframe": "… or غير واضح",
    "action": "BUY or SELL or WAIT",
    "probability": 0,
    "confidence": "High/Medium/Low or غير واضح",
    "pattern_name": "اسم النموذج أو غير واضح",
    "pattern_bias": "Bullish/Bearish/Neutral or غير واضح",
    "key_level": "أهم مستوى (دعم/مقاومة/عنق) أو غير واضح",
    "entry": "… or غير واضح",
    "sl": "… or غير واضح",
    "tp1": "… or غير واضح",
    "tp2": "… or غير واضح",
    "reason": "سبب مختصر جداً (سطرين max)",
    "wait_reason": "اذا WAIT فقط (سطر واحد)",
    "tips": ["3 نصائح عملية قصيرة"],
    "warning": "⚠️ تنبيه: التحليل تعليمي والنسبة تقديرية وليست ضمان. المخاطرة 1–2% فقط."
  },
  "en": {
    "symbol": "… or Not clear",
    "timeframe": "… or Not clear",
    "action": "BUY or SELL or WAIT",
    "probability": 0,
    "confidence": "High/Medium/Low or Not clear",
    "pattern_name": "Pattern name or Not clear",
    "pattern_bias": "Bullish/Bearish/Neutral or Not clear",
    "key_level": "Key level or Not clear",
    "entry": "… or Not clear",
    "sl": "… or Not clear",
    "tp1": "… or Not clear",
    "tp2": "… or Not clear",
    "reason": "Very short reason (max 2 lines)",
    "wait_reason": "Only if WAIT (one line)",
    "tips": ["3 short practical tips"],
    "warning": "⚠️ Warning: Educational only. Probability is an estimate (not guaranteed). Risk max 1–2%."
  }
}
"""

def analyze_with_ai(image_bytes: bytes) -> str:
    b64 = base64.b64encode(image_bytes).decode("utf-8")
    resp = client.responses.create(
        model="gpt-4.1-mini",
        input=[{
            "role": "user",
            "content": [
                {"type": "input_text", "text": IMAGE_PROMPT},
                {"type": "input_image", "image_url": f"data:image/jpeg;base64,{b64}"}
            ]
        }]
    )
    raw = (resp.output_text or "").strip()
    data = json.loads(raw)
    ar = data.get("ar", {}) if isinstance(data, dict) else {}
    en = data.get("en", {}) if isinstance(data, dict) else {}
    return format_message(ar, en)


def generate_signal(symbol: str, timeframe: str) -> str:
    symbol = (symbol or "XAUUSD").upper().strip()
    timeframe = (timeframe or "M5").upper().strip()

    prompt = f"""
You are a conservative scalping/day-trading signal provider.
Goal: accuracy over frequency.

Create a signal for Symbol={symbol}, Timeframe={timeframe}.

Rules:
- Output MUST be VALID JSON only.
- Use BUY/SELL/WAIT. If not confident -> WAIT.
- Provide probability 0-100 as an estimate (not guaranteed).
- Do NOT mention a chart pattern unless clearly justified; otherwise pattern_name="Not clear/غير واضح".
- Always give practical tips (even if WAIT).
- Keep reason max 2 lines.

Return the same JSON structure as IMAGE_PROMPT (ar/en).
"""
    resp = client.responses.create(
        model="gpt-4.1-mini",
        input=prompt
    )
    raw = (resp.output_text or "").strip()
    data = json.loads(raw)
    ar = data.get("ar", {}) if isinstance(data, dict) else {}
    en = data.get("en", {}) if isinstance(data, dict) else {}
    return format_message(ar, en)


# ================== Commands ==================
def _is_admin(uid: int) -> bool:
    return ADMIN_ID is not None and uid == ADMIN_ID

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(
        "✅ البوت شغال\n"
        "📸 أرسل صورة الشارت للتحليل\n"
        "🔒 /signal (VIP فقط)\n"
        "ℹ️ لمعرفة رقمك: /myid"
    )

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(
        "📌 الاستخدام:\n"
        "- أرسل صورة شارت واضحة للتحليل\n"
        "- /signal يعطي إشارة بدون صورة (VIP فقط)\n"
        "- /myid يطلع رقمك + حالة VIP\n\n"
        "Admin:\n"
        "/vipadd <user_id> <days>\n"
        "/vipremove <user_id>\n"
        "/vipcheck <user_id>\n"
        "/viplist"
    )

async def myid_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    exp = get_vip_expiry(uid)
    if exp and is_vip(uid):
        exp_str = exp.strftime("%Y-%m-%d %H:%M UTC")
        vip_line = f"\n✅ VIP Active until: {exp_str}\n✅ VIP فعال حتى: {exp_str}"
    else:
        vip_line = "\n🔒 VIP: غير مفعل\n🔒 VIP: Not active"
    await update.effective_message.reply_text(
        f"🆔 Your Telegram ID: {uid}\n🆔 رقمك في تيليجرام: {uid}{vip_line}"
    )

async def signal_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_vip(uid) and not _is_admin(uid):
        await update.effective_message.reply_text(
            "🔒 هذا الأمر VIP فقط.\n"
            "للاشتراك أرسل /myid للمشرف.\n\n"
            "🔒 VIP only.\n"
            "To get access, send /myid to the admin."
        )
        return

    symbol = context.args[0] if len(context.args) >= 1 else "XAUUSD"
    timeframe = context.args[1] if len(context.args) >= 2 else "M5"

    await update.effective_message.reply_text("⏳ جاري توليد إشارة VIP...")
    try:
        msg = generate_signal(symbol, timeframe)
        await update.effective_message.reply_text(msg)
    except Exception as e:
        logger.exception("SIGNAL_ERROR")
        await update.effective_message.reply_text(f"❌ Error | خطأ:\n{type(e).__name__}\n{e}")

# ----- Admin VIP management -----
async def vipadd_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not _is_admin(uid):
        return

    if len(context.args) < 2:
        await update.effective_message.reply_text("استخدم: /vipadd <user_id> <days>\nExample: /vipadd 123456789 30")
        return

    user_id_str, days_str = context.args[0], context.args[1]
    if not user_id_str.isdigit() or not days_str.isdigit():
        await update.effective_message.reply_text("❌ تأكد أن user_id و days أرقام.")
        return

    user_id = int(user_id_str)
    days = int(days_str)
    expires = set_vip(user_id, days)
    exp_str = expires.strftime("%Y-%m-%d %H:%M UTC")
    await update.effective_message.reply_text(f"✅ تم تفعيل VIP للمستخدم {user_id} لمدة {days} يوم.\nينتهي: {exp_str}")

async def vipremove_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not _is_admin(uid):
        return
    if len(context.args) < 1 or not context.args[0].isdigit():
        await update.effective_message.reply_text("استخدم: /vipremove <user_id>")
        return
    user_id = int(context.args[0])
    remove_vip(user_id)
    await update.effective_message.reply_text(f"✅ تم حذف VIP للمستخدم {user_id}")

async def vipcheck_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not _is_admin(uid):
        return
    if len(context.args) < 1 or not context.args[0].isdigit():
        await update.effective_message.reply_text("استخدم: /vipcheck <user_id>")
        return
    user_id = int(context.args[0])
    exp = get_vip_expiry(user_id)
    if exp and datetime.now(timezone.utc) < exp:
        await update.effective_message.reply_text(f"✅ VIP فعال للمستخدم {user_id}\nينتهي: {exp.strftime('%Y-%m-%d %H:%M UTC')}")
    else:
        await update.effective_message.reply_text(f"🔒 VIP غير فعال للمستخدم {user_id}")

async def viplist_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not _is_admin(uid):
        return
    rows = list_vips(limit=50)
    if not rows:
        await update.effective_message.reply_text("لا يوجد VIP حالياً.")
        return
    lines = ["📌 VIP List (Top 50):"]
    now = datetime.now(timezone.utc)
    for u, exp in rows:
        if exp:
            status = "ACTIVE" if now < exp else "EXPIRED"
            lines.append(f"- {u} | {status} | {exp.strftime('%Y-%m-%d %H:%M UTC')}")
        else:
            lines.append(f"- {u} | (bad date)")
    await update.effective_message.reply_text("\n".join(lines))


# ================== Photo Handler ==================
async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    await msg.reply_text("📸 وصلتني الصورة ✅\n⏳ جاري التحليل...")

    try:
        photo = msg.photo[-1]
        tg_file = await context.bot.get_file(photo.file_id)
        image_bytes = await tg_file.download_as_bytearray()

        result = analyze_with_ai(bytes(image_bytes))
        await msg.reply_text(result)

    except Exception as e:
        logger.exception("PHOTO_HANDLER_ERROR")
        await msg.reply_text(f"❌ Error | خطأ:\n{type(e).__name__}\n{e}")


# ================== Run ==================
def main():
    if not TOKEN:
        raise RuntimeError("❌ BOT_TOKEN غير موجود في Render → Environment.")
    if not OPENAI_API_KEY:
        raise RuntimeError("❌ OPENAI_API_KEY غير موجود في Render → Environment.")
    if ADMIN_ID is None:
        raise RuntimeError("❌ ADMIN_USER_ID غير موجود. ضع رقمك من /myid في Render Environment.")

    db_init()

    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("myid", myid_cmd))
    app.add_handler(CommandHandler("signal", signal_cmd))

    # Admin VIP commands
    app.add_handler(CommandHandler("vipadd", vipadd_cmd))
    app.add_handler(CommandHandler("vipremove", vipremove_cmd))
    app.add_handler(CommandHandler("vipcheck", vipcheck_cmd))
    app.add_handler(CommandHandler("viplist", viplist_cmd))

    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))

    logger.info("🤖 Trading AI Bot is running...")
    app.run_polling()

if __name__ == "__main__":
    main()
