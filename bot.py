import os
import logging
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# ================== TOKEN من Render Environment ==================
TOKEN = os.environ.get("BOT_TOKEN", "").strip()

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO
)
logger = logging.getLogger("TradingAI")

# ================== أوامر ==================
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(
        "✅ البوت شغال\n"
        "📸 أرسل صورة الشارت الآن"
    )

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(
        "📌 طريقة الاستخدام:\n"
        "- أرسل صورة الشارت\n"
        "- البوت بيرد عليك مباشرة"
    )

# ================== استقبال الصور ==================
async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message

    # رد فوري للتأكد أنه استلم الصورة
    await msg.reply_text("✅ وصلتني الصورة")

    try:
        photo = msg.photo[-1]
        tg_file = await context.bot.get_file(photo.file_id)
        image_bytes = await tg_file.download_as_bytearray()

        await msg.reply_text(f"📦 تم تحميل الصورة بنجاح ({len(image_bytes)} bytes)")
        await msg.reply_text("📊 تحليل تجريبي: جاهز (بنضيف AI لاحقًا).")

    except Exception as e:
        logger.exception("PHOTO_HANDLER_ERROR")
        await msg.reply_text(f"❌ خطأ: {type(e).__name__}\n{e}")

# ================== تشغيل البوت ==================
def main():
    if not TOKEN:
        raise RuntimeError("❌ BOT_TOKEN غير موجود. ضعه في Render → Environment.")

    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))

    logger.info("🤖 Bot running...")
    app.run_polling()

if __name__ == "__main__":
    main()
