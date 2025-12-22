import logging
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# ================== الإعدادات ==================
TOKEN = "PUT_YOUR_BOT_TOKEN_HERE"

USER_LANG = {}  # تخزين لغة المستخدم (اختياري)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)

# ================== أوامر ==================
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 أهلاً بك\n"
        "📸 أرسل صورة الشارت وسأقوم بتحليلها\n"
        "🧠 يدعم RSI / Stoch RSI / Price Action"
    )

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📌 طريقة الاستخدام:\n"
        "- أرسل صورة الشارت\n"
        "- يدعم RSI / Stoch RSI / Price\n"
        "- يعمل على جميع العملات والفريمات"
    )

# ================== تحليل الشارت (وهمي حالياً) ==================
async def analyze_chart(image_bytes: bytearray, lang: str = "ar") -> str:
    # هنا مستقبلاً تحط AI / CV / تحليل حقيقي
    return (
        "📊 نتيجة التحليل:\n"
        "• الاتجاه: صاعد ⬆️\n"
        "• RSI: تشبع بيع\n"
        "• Stoch RSI: انعكاس محتمل\n"
        "⚠️ هذه نتيجة تجريبية"
    )

# ================== استقبال الصور ==================
async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message

    # رد فوري للتأكد أن البوت استلم الصورة
    await msg.reply_text("✅ وصلتني الصورة")

    try:
        photo = msg.photo[-1]
        file = await context.bot.get_file(photo.file_id)
        image_bytes = await file.download_as_bytearray()

        user_id = update.effective_user.id
        lang_mode = USER_LANG.get(user_id, "ar")

        await msg.reply_text("⏳ جاري تحليل الشارت...")

        analysis = await analyze_chart(image_bytes, lang_mode)
        await msg.reply_text(analysis)

    except Exception as e:
        logging.exception("PHOTO_HANDLER_ERROR")
        await msg.reply_text(f"❌ خطأ: {type(e).__name__}\n{e}")

# ================== تشغيل البوت ==================
def main():
    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("help", help_cmd))

    # مهم جداً: هاندلر الصور
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))

    print("🤖 Bot is running...")
    app.run_polling()

if __name__ == "__main__":
    main()
