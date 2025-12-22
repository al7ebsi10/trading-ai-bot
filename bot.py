import os
import json
import asyncio
from io import BytesIO
from typing import Optional, Dict, Any
from fastapi import FastAPI, Request, Header, HTTPException
from fastapi.responses import JSONResponse
from PIL import Image

from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters

# ---- ENV ----
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
PUBLIC_URL = os.getenv("PUBLIC_URL", "").strip().rstrip("/")  # e.g. https://xxxx.onrender.com
OWNER_ID = int(os.getenv("OWNER_ID", "0") or "0")

TV_WEBHOOK_SECRET = os.getenv("TV_WEBHOOK_SECRET", "").strip()  # optional (recommended)

PRO_CHANNEL_ID = int(os.getenv("PRO_CHANNEL_ID", "0") or "0")
VIP_ALL_CHANNEL_ID = int(os.getenv("VIP_ALL_CHANNEL_ID", "0") or "0")
VIP_GOLD_CHANNEL_ID = int(os.getenv("VIP_GOLD_CHANNEL_ID", "0") or "0")

FREE_TRIAL_LIMIT = int(os.getenv("FREE_TRIAL_LIMIT", "5") or "5")

# ---- BASIC STORAGE (in-memory) ----
# NOTE: On Render free/starter, restarting will reset this.
user_free_used: Dict[int, int] = {}
user_plan: Dict[int, str] = {}  # "FREE", "PRO", "VIP_ALL", "VIP_GOLD"

# ---- FastAPI ----
app = FastAPI()

# ---- Telegram Application ----
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is missing in environment variables.")

tg_app = Application.builder().token(BOT_TOKEN).build()

# ---- Helpers ----
def plan_of(uid: int) -> str:
    return user_plan.get(uid, "FREE")

def free_left(uid: int) -> int:
    used = user_free_used.get(uid, 0)
    return max(0, FREE_TRIAL_LIMIT - used)

def inc_free(uid: int):
    user_free_used[uid] = user_free_used.get(uid, 0) + 1

def extract_symbol_tf_from_caption(text: str) -> (str, str):
    # very simple: user may write "XAUUSD M5" etc.
    if not text:
        return ("XAUUSD", "M5")
    t = text.upper()
    sym = "XAUUSD" if "XAU" in t or "GOLD" in t else "XAUUSD"
    tf = "M5"
    for cand in ["M1", "M3", "M5", "M15", "M30", "H1", "H4", "D1"]:
        if cand in t:
            tf = cand
            break
    return sym, tf

def build_signal_block(symbol: str, tf: str, trend: str) -> str:
    # Placeholder logic (you can later replace with real OpenAI output)
    # We'll create plausible zones based on "trend"
    if trend == "BULLISH":
        entry = "Buy Zone: pullback near support"
        sl = "SL: below last swing low"
        tp1, tp2, tp3 = "TP1: near recent high", "TP2: extension", "TP3: strong extension"
        caution = "Caution: wait for pullback confirmation (avoid chasing)."
        action = "BUY"
    elif trend == "BEARISH":
        entry = "Sell Zone: retest near resistance"
        sl = "SL: above last swing high"
        tp1, tp2, tp3 = "TP1: near recent low", "TP2: extension", "TP3: strong extension"
        caution = "Caution: watch for sharp reversals on news spikes."
        action = "SELL"
    else:
        entry = "Entry Zone: range edges (support/resistance)"
        sl = "SL: outside the range"
        tp1, tp2, tp3 = "TP1: mid-range", "TP2: opposite edge", "TP3: only if breakout holds"
        caution = "Caution: market is choppy; reduce size and wait for breakout."
        action = "NEUTRAL"

    return (
        f"📌 **{action} | {symbol} {tf}**\n"
        f"📈 Market State: **{trend}**\n\n"
        f"🎯 {entry}\n"
        f"✅ TP1: {tp1}\n"
        f"✅ TP2: {tp2}\n"
        f"✅ TP3: {tp3}\n"
        f"🛑 {sl}\n\n"
        f"⚠️ {caution}\n"
        f"📚 Educational only | Risk 1–2%\n"
    )

async def simple_trend_guess_from_image(img: Image.Image) -> str:
    # Very lightweight heuristic: if image is too dark/blank -> NEUTRAL
    # (You can replace with OpenAI vision later.)
    try:
        small = img.convert("L").resize((80, 80))
        px = list(small.getdata())
        avg = sum(px) / len(px)
        if avg < 40 or avg > 240:
            return "NEUTRAL"
        return "BULLISH"  # default guess to avoid constant WAIT
    except:
        return "NEUTRAL"

# ---- Commands ----
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    p = plan_of(uid)
    left = free_left(uid)
    msg = (
        "🤖 Trading AI Bot is online ✅\n\n"
        f"Your plan: **{p}**\n"
        f"Free analyses left: **{left}/{FREE_TRIAL_LIMIT}**\n\n"
        "📸 Send a clear chart screenshot (zoom candles).\n"
        "💡 Tip: include symbol/timeframe in caption مثل: XAUUSD M5\n"
        "⭐ Upgrade: /plans\n"
    )
    await update.message.reply_text(msg, parse_mode="Markdown")

async def plans_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = (
        "💎 Plans:\n"
        "• FREE: 5 analyses (trial)\n"
        "• PRO: signals to PRO channel\n"
        "• VIP_GOLD: gold-only premium signals\n"
        "• VIP_ALL: all assets premium signals\n\n"
        "Admin command:\n"
        "/setplan <user_id> <plan> <days>\n"
        "plans: FREE, PRO, VIP_GOLD, VIP_ALL\n"
    )
    await update.message.reply_text(txt)

async def myid_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    await update.message.reply_text(f"Your Telegram ID: {uid}")

async def setplan_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if OWNER_ID and uid != OWNER_ID:
        await update.message.reply_text("❌ Admin only.")
        return

    if len(context.args) < 2:
        await update.message.reply_text("Usage: /setplan <user_id> <plan> <days>")
        return

    try:
        target_id = int(context.args[0])
        plan = context.args[1].upper().strip()
        if plan not in ["FREE", "PRO", "VIP_GOLD", "VIP_ALL"]:
            await update.message.reply_text("Invalid plan. Use: FREE, PRO, VIP_GOLD, VIP_ALL")
            return
        user_plan[target_id] = plan
        await update.message.reply_text(f"✅ Set {target_id} plan={plan}")
    except Exception as e:
        await update.message.reply_text(f"Error: {e}")

# ---- Image handler ----
async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    p = plan_of(uid)

    if p == "FREE":
        if free_left(uid) <= 0:
            await update.message.reply_text("🔒 Free trial finished. Upgrade: /plans")
            return

    await update.message.reply_text("💣 Received. Analyzing...")

    # download photo
    photo = update.message.photo[-1]
    file = await context.bot.get_file(photo.file_id)
    b = BytesIO()
    await file.download_to_memory(out=b)
    b.seek(0)

    try:
        img = Image.open(b)
    except:
        await update.message.reply_text("❌ Could not read image. Send a clearer screenshot.")
        return

    caption = update.message.caption or ""
    symbol, tf = extract_symbol_tf_from_caption(caption)

    # Trend guess (replace with OpenAI later)
    trend = await simple_trend_guess_from_image(img)

    # Build signal (always give something for FREE, not only WAIT)
    signal_text = build_signal_block(symbol, tf, trend)

    if p == "FREE":
        inc_free(uid)
        signal_text += f"\n🧪 Free Trial remaining: **{free_left(uid)}/{FREE_TRIAL_LIMIT}**\n⭐ Upgrade: /plans\n"

    await update.message.reply_text(signal_text, parse_mode="Markdown")

# ---- Telegram setup ----
tg_app.add_handler(CommandHandler("start", start_cmd))
tg_app.add_handler(CommandHandler("plans", plans_cmd))
tg_app.add_handler(CommandHandler("myid", myid_cmd))
tg_app.add_handler(CommandHandler("setplan", setplan_cmd))
tg_app.add_handler(MessageHandler(filters.PHOTO, handle_photo))

# ---- FastAPI routes ----
@app.get("/")
async def root():
    return {"ok": True, "service": "trading-ai-bot"}

@app.post("/telegram")
async def telegram_webhook(req: Request):
    data = await req.json()
    update = Update.de_json(data, tg_app.bot)
    await tg_app.process_update(update)
    return {"ok": True}

@app.post("/tv_webhook")
async def tv_webhook(req: Request, x_tv_secret: Optional[str] = Header(default=None)):
    # Optional security
    if TV_WEBHOOK_SECRET:
        if not x_tv_secret or x_tv_secret != TV_WEBHOOK_SECRET:
            raise HTTPException(status_code=401, detail="Invalid TV secret")

    payload = await req.json()
    # Expected payload example:
    # {"symbol":"XAUUSD","tf":"M5","side":"BUY","entry":"...","sl":"...","tp1":"...","tp2":"...","tp3":"...","note":"...","plan":"VIP_GOLD"}
    symbol = str(payload.get("symbol", "XAUUSD")).upper()
    tf = str(payload.get("tf", "M5")).upper()
    side = str(payload.get("side", "BUY")).upper()
    entry = str(payload.get("entry", "Entry Zone")).strip()
    sl = str(payload.get("sl", "SL")).strip()
    tp1 = str(payload.get("tp1", "TP1")).strip()
    tp2 = str(payload.get("tp2", "TP2")).strip()
    tp3 = str(payload.get("tp3", "TP3")).strip()
    note = str(payload.get("note", "")).strip()
    target_plan = str(payload.get("plan", "VIP_ALL")).upper()

    msg = (
        f"🚨 **{side} SIGNAL**\n"
        f"📌 {symbol} {tf}\n\n"
        f"🎯 Entry: {entry}\n"
        f"✅ TP1: {tp1}\n"
        f"✅ TP2: {tp2}\n"
        f"✅ TP3: {tp3}\n"
        f"🛑 SL: {sl}\n"
    )
    if note:
        msg += f"\n⚠️ {note}\n"
    msg += "\n📚 Educational only | Risk 1–2%"

    # Route to channels (if configured)
    chat_id = None
    if target_plan == "PRO" and PRO_CHANNEL_ID:
        chat_id = PRO_CHANNEL_ID
    elif target_plan == "VIP_GOLD" and VIP_GOLD_CHANNEL_ID:
        chat_id = VIP_GOLD_CHANNEL_ID
    elif target_plan == "VIP_ALL" and VIP_ALL_CHANNEL_ID:
        chat_id = VIP_ALL_CHANNEL_ID

    if chat_id:
        await tg_app.bot.send_message(chat_id=chat_id, text=msg, parse_mode="Markdown")

    return {"ok": True}

@app.on_event("startup")
async def on_startup():
    # Initialize telegram app
    await tg_app.initialize()
    await tg_app.start()

    # Set webhook
    if not PUBLIC_URL:
        # If not set, bot can still run but webhook won't work
        if OWNER_ID:
            await tg_app.bot.send_message(chat_id=OWNER_ID, text="⚠️ PUBLIC_URL is missing. Telegram webhook not set.")
        return

    wh_url = f"{PUBLIC_URL}/telegram"
    await tg_app.bot.set_webhook(url=wh_url)

    if OWNER_ID:
        await tg_app.bot.send_message(chat_id=OWNER_ID, text=f"✅ Bot is LIVE.\nTelegram webhook set:\n{wh_url}")

@app.on_event("shutdown")
async def on_shutdown():
    await tg_app.stop()
    await tg_app.shutdown()
