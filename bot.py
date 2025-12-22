import os
import logging
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    ContextTypes,
    CommandHandler,
    MessageHandler,
    filters,
)
from openai import OpenAI
from PIL import Image
import base64
import io

# =======================
# CONFIG
# =======================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

SYSTEM_PROMPT_PATH = "system_prompt.txt"

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

client = OpenAI(api_key=OPENAI_API_KEY)

# =======================
# HELPERS
# =======================
def load_system_prompt():
    try:
        with open(SYSTEM_PROMPT_PATH, "r", encoding="utf-8") as f:
            return f.read()
    except Exception:
        return "You are a professional trading analysis AI."

def image_to_base64(photo_bytes: bytes) -> str:
    image = Image.open(io.BytesIO(photo_bytes))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode()

async def analyze_chart(image_bytes: bytes) -> str:
    system_prompt = load_system_prompt()
    img_b64 = image_to_base64(image_bytes)

    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "حلل الشارت المرفق وقدم توصية احترافية حسب الفريم."},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/png;base64,{img_b64}"},
                    },
                ],
            },
        ],
        max_tokens=800,
    )

    return response.choices[0].message.content

# =======================
# TELEGRAM HANDLERS
# =======================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "✅ TradingAI Pro يعمل الآن\n\n"
        "📊 أرسل صورة الشارت (أي زوج / أي فريم)\n"
        "وسيتم التحليل + التوصية حسب الفريم تلقائياً."
    )

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📌 طريقة الاستخدام:\n"
        "- أرسل صورة الشارت\n"
        "- يدعم: RSI / Stoch RSI / Price Action / Patterns\n"
        "- يعمل على جميع العملات والفريمات"
    )

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        photo = update.message.photo[-1]
        file = await photo.get_file()
        image_bytes = await file.download_as_bytearray()

        await update.message.reply_text("⏳ يتم تحليل الشارت...")

        analysis = await analyze_chart(bytes(image_bytes))
        await update.message.reply_text(analysis)

    except Exception as e:
        logging.error(e)
        await update.message.reply_text("❌ حدث خطأ أثناء التحليل.")

# =======================
# MAIN
# =======================
def main():
    if not TELEGRAM_TOKEN:
        raise ValueError("TELEGRAM_TOKEN غير موجود")

    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))

    app.run_polling()

if __name__ == "__main__":
    main()
