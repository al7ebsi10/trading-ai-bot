import os
import logging
import base64
import json
import re

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

# VIP list: comma-separated Telegram user IDs
# مثال: "123456789,987654321"
VIP_USER_IDS_RAW = os.environ.get("VIP_USER_IDS", "").strip()

# Optional: admin can always use /signal
ADMIN_USER_ID_RAW = os.environ.get("ADMIN_USER_ID", "").strip()

client = OpenAI(api_key=OPENAI_API_KEY)

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO
)
logger = logging.getLogger("TradingAI")


# ================== VIP Helpers ==================
def _parse_ids(raw: str) -> set[int]:
    if not raw:
        return set()
    out = set()
    for part in raw.split(","):
        part = part.strip()
        if part.isdigit():
            out.add(int(part))
    return out

VIP_IDS = _parse_ids(VIP_USER_IDS_RAW)
ADMIN_ID = int(ADMIN_USER_ID_RAW) if ADMIN_USER_ID_RAW.isdigit() else None

def is_vip(user_id: int) -> bool:
    if ADMIN_ID and user_id == ADMIN_ID:
        return True
    return user_id in VIP_IDS


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


# ================== AI (Image Analysis) ==================
def analyze_with_ai(image_bytes: bytes) -> str:
    if not OPENAI_API_KEY:
        return "❌ OPENAI_API_KEY غير موجود في Render."

    b64 = base64.b64encode(image_bytes).decode("utf-8")

    prompt = """
You are a conservative trading analyst focused on accuracy.

Key rules:
- Do NOT mention a chart pattern unless it is clearly visible. If unclear, set pattern_name="Not clear/غير واضح".
- Even if pattern is unclear, you MUST still provide practical tips (confirmation, key levels, what to wait for).
- Provide a PROBABILITY estimate as a subjective confidence score (0–100). It is NOT guaranteed.
- If prices/levels are not readable, do NOT invent numbers: use "Not clear/غير واضح" and set action="WAIT".
- Use RSI + Stoch RSI as confirmation/timing, not the only reason.
- Prefer WAIT when confirmation is missing.

Output VALID JSON ONLY with ar/en blocks and fields:
symbol, timeframe, action, probability, confidence,
pattern_name, pattern_bias, key_level,
entry, sl, tp1, tp2,
reason, wait_reason, tips (list), warning.
"""

    resp = client.responses.create(
        model="gpt-4.1-mini",
        input=[{
            "role": "user",
            "content": [
                {"type": "input_text", "text": prompt},
                {"type": "input_image", "image_url": f"data:image/jpeg;base64,{b64}"}
            ]
        }]
    )

    raw = (resp.output_text or "").strip()
    try:
        data = json.loads(raw)
        ar = data.get("ar", {}) if isinstance(data, dict) else {}
        en = data.get("en", {}) if isinstance(data, dict) else {}
        return format_message(ar, en)
    except Exception:
        return _clean("⚠️ AI رجّع رد غير منظم. هذا النص كما هو:\n\n" + raw)


# ================== AI (/signal) ==================
def generate_signal(symbol: str, timeframe: str) -> str:
    if not OPENAI_API_KEY:
        return "❌ OPENAI_API_KEY غير موجود في Render."

    symbol = (symbol or "XAUUSD").upper().strip()
    timeframe = (timeframe or "M5").upper().strip()

    prompt = f"""
You are a conservative scalping/day-trading signal provider.
Goal: accuracy over frequency.

Create a signal for:
Symbol: {symbol}
Timeframe: {timeframe}

Rules:
- Output MUST be VALID JSON only.
- Use BUY/SELL/WAIT.
- If you are not confident, return WAIT.
- Provide probability 0-100 as an estimate (not guaranteed).
- Do NOT mention a chart pattern unless you are confident it fits typical structure; otherwise set pattern_name="Not clear/غير واضح".
- Give practical tips ALWAYS (even if WAIT).

Return JSON exactly:
{{
  "ar": {{
    "symbol": "{symbol}",
    "timeframe": "{timeframe}",
    "action": "BUY or SELL or WAIT",
    "probability": 0,
    "confidence": "High/Medium/Low",
    "pattern_name": "اسم النموذج أو غير واضح",
    "pattern_bias": "Bullish/Bearish/Neutral",
    "key_level": "أهم مستوى (دعم/مقاومة/عنق) أو غير واضح",
    "entry": "سعر/منطقة أو غير واضح",
    "sl": "سعر أو غير واضح",
    "tp1": "سعر أو غير واضح",
    "tp2": "سعر أو غير واضح",
    "reason": "سبب مختصر جداً (سطرين max)",
    "wait_reason": "اذا WAIT فقط (سطر واحد)",
    "tips": ["3 نصائح عملية قصيرة"],
    "warning": "⚠️ تنبيه: التحليل تعليمي والنسبة تقديرية وليست ضمان. المخاطرة 1–2% فقط."
  }},
  "en": {{
    "symbol": "{symbol}",
    "timeframe": "{timeframe}",
    "action": "BUY or SELL or WAIT",
    "probability": 0,
    "confidence": "High/Medium/Low",
    "pattern_name": "Pattern name or Not clear",
    "pattern_bias": "Bullish/Bearish/Neutral",
    "key_level": "Key level or Not clear",
    "entry": "price/zone or Not clear",
    "sl": "price or Not clear",
    "tp1": "price or Not clear",
    "tp2": "price or Not clear",
    "reason": "Very short reason (max 2 lines)",
    "wait_reason": "Only if WAIT (one line)",
    "tips": ["3 short practical tips"],
    "warning": "⚠️ Warning: Educational only. Probability is an estimate (not guaranteed). Risk max 1–2%."
  }}
}}
"""

    resp = client.responses.create(
        model="gpt-4.1-mini",
        input=prompt
    )

    raw = (resp.output_text or "").strip()
    try:
        data = json.loads(raw)
        ar = data.get("ar", {}) if isinstance(data, dict) else {}
        en = data.get("en", {}) if isinstance(data, dict) else {}
        return format_message(ar, en)
    except Exception:
        return _clean("⚠️ AI returned unstructured signal:\n\n" + raw)


# ================== Commands ==================
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(
        "✅ البوت شغال\n"
        "📸 أرسل صورة الشارت للتحليل\n"
        "🔔 /signal (VIP فقط)\n"
        "ℹ️ لمعرفة رقمك: /myid"
    )

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(
        "📌 الاستخدام:\n"
        "- أرسل صورة شارت واضحة للتحليل\n"
        "- /signal يعطي إشارة بدون صورة (VIP فقط)\n"
        "- /myid يطلع رقمك لإضافتك VIP\n\n"
        "How to use:\n"
        "- Send a clear chart screenshot\n"
        "- /signal gives a signal (VIP only)\n"
        "- /myid shows your Telegram ID"
    )

async def myid_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    await update.effective_message.reply_text(
        f"🆔 Your Telegram ID: {uid}\n"
        f"🆔 رقمك في تيليجرام: {uid}"
    )

async def signal_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_vip(uid):
        await update.effective_message.reply_text(
            "🔒 هذا الأمر VIP فقط.\n"
            "للاشتراك وإضافتك للقائمة أرسل /myid للمشرف.\n\n"
            "🔒 VIP only.\n"
            "To get access, send /myid to the admin."
        )
        return

    # optional: /signal XAUUSD M5
    symbol = context.args[0] if len(context.args) >= 1 else "XAUUSD"
    timeframe = context.args[1] if len(context.args) >= 2 else "M5"

    await update.effective_message.reply_text("⏳ جاري توليد إشارة VIP...")

    try:
        msg = generate_signal(symbol, timeframe)
        await update.effective_message.reply_text(msg)
    except Exception as e:
        logger.exception("SIGNAL_ERROR")
        await update.effective_message.reply_text(f"❌ Error | خطأ:\n{type(e).__name__}\n{e}")


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

    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("myid", myid_cmd))
    app.add_handler(CommandHandler("signal", signal_cmd))

    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))

    logger.info("🤖 Trading AI Bot is running...")
    app.run_polling()


if __name__ == "__main__":
    main()
