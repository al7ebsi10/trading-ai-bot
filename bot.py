import os
import logging
import base64
import re
import json

from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters
from openai import OpenAI

# ================== ENV ==================
TOKEN = os.environ.get("BOT_TOKEN", "").strip()
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "").strip()
client = OpenAI(api_key=OPENAI_API_KEY)

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO
)
logger = logging.getLogger("TradingAI")


# ================== Helpers ==================
def _clean(s: str) -> str:
    s = s.strip()
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s

def _icon_action(v: str) -> str:
    v = (v or "").upper().strip()
    if v == "BUY":
        return "🟢 BUY"
    if v == "SELL":
        return "🔴 SELL"
    return "🟡 WAIT"

def _fmt_num(x: str, fallback: str) -> str:
    x = (x or "").strip()
    return x if x else fallback

def format_message(ar: dict, en: dict) -> str:
    # Arabic card (no flags, no "Arabic" title)
    ar_action = _icon_action(ar.get("action"))
    en_action = _icon_action(en.get("action"))

    # Optional fields
    ar_conf = _fmt_num(ar.get("confidence"), "غير واضح")
    en_conf = _fmt_num(en.get("confidence"), "Not clear")

    ar_symbol = _fmt_num(ar.get("symbol"), "غير واضح")
    en_symbol = _fmt_num(en.get("symbol"), "Not clear")

    ar_tf = _fmt_num(ar.get("timeframe"), "غير واضح")
    en_tf = _fmt_num(en.get("timeframe"), "Not clear")

    ar_entry = _fmt_num(ar.get("entry"), "غير واضح")
    en_entry = _fmt_num(en.get("entry"), "Not clear")

    ar_sl = _fmt_num(ar.get("sl"), "غير واضح")
    en_sl = _fmt_num(en.get("sl"), "Not clear")

    ar_tp1 = _fmt_num(ar.get("tp1"), "غير واضح")
    en_tp1 = _fmt_num(en.get("tp1"), "Not clear")

    ar_tp2 = _fmt_num(ar.get("tp2"), "غير واضح")
    en_tp2 = _fmt_num(en.get("tp2"), "Not clear")

    ar_wait_reason = (ar.get("wait_reason") or "غير واضح").strip()
    en_wait_reason = (en.get("wait_reason") or "Not clear").strip()

    ar_reason = (ar.get("reason") or "غير واضح").strip()
    en_reason = (en.get("reason") or "Not clear").strip()

    # Warning lines
    ar_warning = (ar.get("warning") or
                  "⚠️ تنبيه: التحليل تعليمي وليس توصية مالية. إدارة رأس المال ضرورية (مخاطرة 1–2% كحد أقصى).").strip()
    en_warning = (en.get("warning") or
                  "⚠️ Warning: Educational analysis, not financial advice. Use strict risk management (max 1–2%).").strip()

    # Build message (clean, short, icons, bilingual)
    msg = (
        "╭──────────────╮\n"
        "   🤖 Trading AI\n"
        "╰──────────────╯\n\n"
        f"{ar_action}\n"
        f"📌 الزوج: {ar_symbol}   ⏱️ {ar_tf}\n"
        f"⭐ الثقة: {ar_conf}\n"
    )

    # If WAIT, show wait reason instead of full trade plan
    if (ar.get("action") or "").upper().strip() == "WAIT":
        msg += (
            f"⏳ الانتظار لأن: {ar_wait_reason}\n"
            f"🧠 ملخص: {ar_reason}\n"
        )
    else:
        msg += (
            f"🎯 دخول: {ar_entry}\n"
            f"🛑 SL: {ar_sl}\n"
            f"✅ TP1: {ar_tp1}\n"
            f"✅ TP2: {ar_tp2}\n"
            f"🧠 السبب: {ar_reason}\n"
        )

    msg += "\n" + ar_warning + "\n\n" + "—" * 22 + "\n\n"

    msg += (
        f"{en_action}\n"
        f"📌 Pair: {en_symbol}   ⏱️ {en_tf}\n"
        f"⭐ Confidence: {en_conf}\n"
    )

    if (en.get("action") or "").upper().strip() == "WAIT":
        msg += (
            f"⏳ Wait because: {en_wait_reason}\n"
            f"🧠 Summary: {en_reason}\n"
        )
    else:
        msg += (
            f"🎯 Entry: {en_entry}\n"
            f"🛑 SL: {en_sl}\n"
            f"✅ TP1: {en_tp1}\n"
            f"✅ TP2: {en_tp2}\n"
            f"🧠 Reason: {en_reason}\n"
        )

    msg += "\n" + en_warning
    return _clean(msg)


def analyze_with_ai(image_bytes: bytes) -> str:
    if not OPENAI_API_KEY:
        return "❌ OPENAI_API_KEY غير موجود في Render."

    b64 = base64.b64encode(image_bytes).decode("utf-8")

    # Prompt focused on accuracy + WAIT when unclear
    prompt = """
You are a professional, conservative trading analyst.
Your goal is ACCURACY over activity:
- If the setup is not clear OR the image is not readable, return WAIT.
- Do not guess prices if not visible. Use "Not clear/غير واضح" and set action=WAIT.
- Keep reasons short, based on RSI, Stoch RSI, and price action.
- Output MUST be VALID JSON only (no markdown, no extra text).

Return exactly this JSON schema:

{
  "ar": {
    "symbol": "e.g., XAUUSD (or غير واضح)",
    "timeframe": "e.g., M5 (or غير واضح)",
    "action": "BUY or SELL or WAIT",
    "confidence": "High/Medium/Low (or غير واضح)",
    "entry": "price/zone (or غير واضح)",
    "sl": "price/zone (or غير واضح)",
    "tp1": "price/zone (or غير واضح)",
    "tp2": "price/zone (or غير واضح)",
    "reason": "Arabic short reason (max 2 lines). Mention RSI, Stoch RSI, price action.",
    "wait_reason": "Arabic short (only if action=WAIT).",
    "warning": "Arabic risk warning in one line."
  },
  "en": {
    "symbol": "e.g., XAUUSD (or Not clear)",
    "timeframe": "e.g., M5 (or Not clear)",
    "action": "BUY or SELL or WAIT",
    "confidence": "High/Medium/Low (or Not clear)",
    "entry": "price/zone (or Not clear)",
    "sl": "price/zone (or Not clear)",
    "tp1": "price/zone (or Not clear)",
    "tp2": "price/zone (or Not clear)",
    "reason": "English short reason (max 2 lines). Mention RSI, Stoch RSI, price action.",
    "wait_reason": "English short (only if action=WAIT).",
    "warning": "English risk warning in one line."
  }
}
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
        # fallback
        return _clean("⚠️ AI رجّع رد غير منظم. هذا النص كما هو:\n\n" + raw)


# ================== Commands ==================
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(
        "✅ البوت شغال\n"
        "📸 أرسل صورة الشارت الآن\n"
        "🟢/🔴 يعطي BUY/SELL إذا الإشارة واضحة\n"
        "🟡 يعطي WAIT إذا ما في فرصة مؤكدة (للدقة)"
    )

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(
        "📌 الاستخدام:\n"
        "- أرسل صورة شارت واضحة\n"
        "- الأفضل تكون فيها RSI و Stoch RSI\n"
        "- البوت بيرجع تحليل مرتب عربي + إنجليزي مع BUY/SELL/WAIT وتحذير مخاطرة"
    )


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
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))

    logger.info("🤖 Trading AI Bot is running...")
    app.run_polling()

if __name__ == "__main__":
    main()
