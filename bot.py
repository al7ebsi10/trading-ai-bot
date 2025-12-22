import asyncio
import logging
import sys

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

TOKEN = "PUT_YOUR_BOT_TOKEN_HERE"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s"
)
logger = logging.getLogger("bot")

# يطبع أي خطأ حتى لو صار قبل تشغيل polling
def excepthook(exc_type, exc, tb):
    logger.error("UNCAUGHT ERROR", exc_info=(exc_type, exc, tb))
sys.excepthook = excepthook


async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text("✅ شغال. أرسل صورة شارت الآن.")


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text("📌 أرسل صورة شارت وسأرد عليك مباشرة.")


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    await msg.reply_text("✅ وصلتني الصورة")

    try:
        photo = msg.photo[-1]
        tg_file = await context.bot.get_file(photo.file_id)
        image_bytes = await tg_file.download_as_bytearray()

        await msg.reply_text(f"📦 تم تحميل الصورة بنجاح ({len(image_bytes)} bytes)")
        await msg.reply_text("📊 تحليل تجريبي: جاهز (بنضيف AI بعدين).")

    except Exception as e:
        logger.exception("PHOTO_HANDLER_ERROR")
        await msg.reply_text(f"❌ خطأ: {type(e).__name__}\n{e}")


async def main():
    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))

    # يحذف أي Webhook سابق (مهم)
    await app.bot.delete_webhook(drop_pending_updates=True)

    print("🤖 Bot is running...")
    await app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    asyncio.run(main())
