import os
import logging
import base64

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

client = OpenAI(api_key=OPENAI_API_KEY)

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO
)
logger = logging.getLogger("TradingAI")

# ================== Commands ==================
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(
        "🤖 Trading AI Bot\n\n"
        "✅ البوت شغال\n"
        "📸 أرسل صورة الشارت الآن\n\n"
        "The bot supports Arabic & English analysis automatically."
    )

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(
        "📌 الاستخدام | How to use:\n\n"
        "1️⃣ أرسل صورة شارت واضحة\n"
        "2️⃣ يفضل وجود RSI و Stoch RSI\n"
        "3️⃣ ستحصل على تحليل بالعربي والإنجليزي\n\n"
        "Send a clear chart screenshot with RSI & Stoch RSI if possible."
    )

# ================== AI Analysis ==================
def analyze_with_ai(image_bytes: bytes) -> str:
    if not OPENAI_API_KEY:
        return "❌ OPENAI_API_KEY غير موجود في Render."

    b64 = base64.b64encode(image_bytes).decode("utf-8")

    prompt = (
        "You are a professional scalping and day trader.\n"
        "Analyze the trading chart image.\n\n"
        "Return the result in TWO sections:\n"
        "SECTION 1: Arabic 🇸🇦\n"
        "SECTION 2: English 🇬🇧\n\n"
        "For EACH section include:\n"
        "- Symbol / Pair (if visible)\n"
        "- Timeframe (if visible)\n"
        "- Trend (Bullish / Bearish / Range)\n"
        "- Entry zone (price or area)\n"
        "- Stop Loss (SL)\n"
        "- Take Profit (TP1 / TP2)\n"
        "- Reasoning based on RSI, Stochastic RSI, and price action\n\n"
        "If any information is not visible, say 'غير واضح' in Arabic "
        "and 'Not clear' in English.\n\n"
        "Keep the analysis concise, professional, and well structured with emojis."
    )

    response = client.responses.create(
        model="gpt-4.1-mini",
        input=[{
            "role": "user",
            "content": [
                {"type": "input_text", "text": prompt},
                {"type": "input_image", "image_url": f"data:image/jpeg;base64,{b64}"}
            ]
        }]
    )

    return response.output_text.strip()

# ================== Photo Handler ==================
async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    await msg.reply_text("📸 Image received…\n⏳ AI analyzing chart...")

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
        logger.warning("⚠️ OPENAI_API_KEY غير موجود - التحليل لن يعمل.")

    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))

    logger.info("🤖 Trading AI Bot is running...")
    app.run_polling()

if __name__ == "__main__":
    main()
