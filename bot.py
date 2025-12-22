import os
import logging
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters
)

from openai import OpenAI

# -------------------------
# Logging
# -------------------------
logging.basicConfig(level=logging.INFO)

# -------------------------
# Environment variables
# -------------------------
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

if not TELEGRAM_TOKEN:
    raise RuntimeError("TELEGRAM_TOKEN is missing")

if not OPENAI_API_KEY:
    raise RuntimeError("OPENAI_API_KEY is missing")

# -------------------------
# OpenAI Client
# -------------------------
client = OpenAI(api_key=OPENAI_API_KEY)

# -------------------------
# Load system prompt
# -------------------------
def load_system_prompt():
    with open("system_prompt.txt", "r", encoding="utf-8") as f:
        return f.read()

# -------------------------
# Commands
# -------------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "✅ TradingAI_Analysis_bot is running!\n"
        "📸 أرسل صورة الشارت وسيتم التحليل تلقائيًا."
    )

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📌 طريقة الاستخدام:\n"
        "1️⃣ أرسل صورة الشارت (أي زوج / أي فريم)\n"
        "2️⃣ البوت يحلل RSI / Stoch RSI / النماذج\n"
        "3️⃣ يعطيك توصية احترافية كاملة"
    )

# -------------------------
# Image Analysis
# -------------------------
async def analyze_image(image_url: str) -> str:
    system_prompt = load_system_prompt()

    response = client.responses.create(
        model="gpt-4.1",
        input=[{
            "role": "user",
            "content": [
                {"type": "input_text", "text": system_prompt},
                {"type": "input_image", "image_url": image_url}
            ]
        }]
    )

    return response.output_text

# -------------------------
# Handle incoming photos
# -------------------------
async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        photo = update.message.photo[-1]
        file = await photo.get_file()
        image_url = file.file_path

        await update.message.reply_text("🔍 جاري تحليل الشارت، انتظر قليلًا...")

        result = await analyze_image(image_url)

        await update.message.reply_text(result)

    except Exception as e:
        logging.exception("Error while analyzing image")
        await update.message.reply_text(
            "❌ حصل خطأ أثناء التحليل، حاول مرة أخرى."
        )

# -------------------------
# Main
# -------------------------
def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))

    # أهم سطر: استقبال الصور
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))

    logging.info("TradingAI bot started")
    app.run_polling()

# -------------------------
if __name__ == "__main__":
    main()
