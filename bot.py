import os
import json
import time
import base64
import sqlite3
import asyncio
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any

import requests
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# =========================================================
# ENV
# =========================================================
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
OWNER_ID = int(os.getenv("OWNER_ID", "0").strip() or "0")

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4.1-mini").strip()  # vision supported
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1").strip()

FREE_LIMIT = int(os.getenv("FREE_LIMIT", "5").strip() or "5")

# Channels (Telegram channel IDs are negative numbers usually, like -100123...)
PRO_CHANNEL_ID = int(os.getenv("PRO_CHANNEL_ID", "0").strip() or "0")
VIP_CHANNEL_ID = int(os.getenv("VIP_CHANNEL_ID", "0").strip() or "0")
VIP_GOLD_CHANNEL_ID = int(os.getenv("VIP_GOLD_CHANNEL_ID", "0").strip() or "0")

# Optional webhook auth for TradingView -> to prevent spam
TV_WEBHOOK_SECRET = os.getenv("TV_WEBHOOK_SECRET", "").strip()

# Timezone (UAE default GMT+4)
TZ_OFFSET_HOURS = int(os.getenv("TZ_OFFSET_HOURS", "4").strip() or "4")
TZ = timezone(timedelta(hours=TZ_OFFSET_HOURS))

# =========================================================
# DB (SQLite)
# =========================================================
DB_PATH = os.getenv("DB_PATH", "bot.db").strip()

PLANS = ["FREE", "LITE", "PRO", "VIP_GOLD", "VIP_ALL", "VIP_PRO"]

def db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = db()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            plan TEXT NOT NULL DEFAULT 'FREE',
            plan_until INTEGER NOT NULL DEFAULT 0,
            free_used INTEGER NOT NULL DEFAULT 0,
            created_at INTEGER NOT NULL DEFAULT 0
        )
    """)
    conn.commit()
    conn.close()

def now_ts() -> int:
    return int(time.time())

def ensure_user(user_id: int):
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT user_id FROM users WHERE user_id=?", (user_id,))
    row = cur.fetchone()
    if not row:
        cur.execute(
            "INSERT INTO users (user_id, plan, plan_until, free_used, created_at) VALUES (?,?,?,?,?)",
            (user_id, "FREE", 0, 0, now_ts())
        )
        conn.commit()
    conn.close()

def get_user(user_id: int) -> Dict[str, Any]:
    ensure_user(user_id)
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE user_id=?", (user_id,))
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else {"user_id": user_id, "plan": "FREE", "plan_until": 0, "free_used": 0}

def set_plan(user_id: int, plan: str, days: int):
    plan = plan.strip().upper()
    if plan not in PLANS:
        raise ValueError(f"Invalid plan. Allowed: {', '.join(PLANS)}")
    until = now_ts() + int(days) * 86400
    conn = db()
    cur = conn.cursor()
    ensure_user(user_id)
    cur.execute("UPDATE users SET plan=?, plan_until=? WHERE user_id=?", (plan, until, user_id))
    conn.commit()
    conn.close()

def get_effective_plan(user_id: int) -> str:
    u = get_user(user_id)
    plan = (u.get("plan") or "FREE").upper()
    until = int(u.get("plan_until") or 0)
    if plan != "FREE" and until > 0 and now_ts() > until:
        # expired -> revert to FREE
        conn = db()
        cur = conn.cursor()
        cur.execute("UPDATE users SET plan='FREE', plan_until=0 WHERE user_id=?", (user_id,))
        conn.commit()
        conn.close()
        return "FREE"
    return plan

def inc_free_used(user_id: int) -> int:
    conn = db()
    cur = conn.cursor()
    ensure_user(user_id)
    cur.execute("UPDATE users SET free_used = free_used + 1 WHERE user_id=?", (user_id,))
    conn.commit()
    cur.execute("SELECT free_used FROM users WHERE user_id=?", (user_id,))
    val = int(cur.fetchone()["free_used"])
    conn.close()
    return val

def reset_free_if_new_day(user_id: int):
    """
    OPTIONAL: لو تبغى تصفير التجربة يوميًا.
    حاليا: التجربة = 5 مرات فقط ثم تنتهي نهائيا.
    """
    return

# =========================================================
# OpenAI (Responses API) - Vision
# =========================================================
def openai_analyze_chart(image_bytes: bytes) -> Dict[str, Any]:
    """
    Returns structured signal:
    trend, action, confidence, entry_zone, tp1,tp2,tp3, sl, caution, symbol, timeframe
    """
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY missing")

    b64 = base64.b64encode(image_bytes).decode("utf-8")
    # Responses API content types must be: input_text, input_image, output_text, etc.
    payload = {
        "model": OPENAI_MODEL,
        "input": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": (
                            "You are a trading assistant analyzing a chart screenshot. "
                            "IMPORTANT RULES:\n"
                            "1) Always output a COMPLETE actionable signal (never only WAIT). If uncertainty exists, output a conservative conditional setup.\n"
                            "2) Output JSON only (no markdown) with keys:\n"
                            "   symbol, timeframe, market_state (BULLISH/BEARISH/NEUTRAL), action (BUY/SELL/WAIT), confidence (0-100 int),\n"
                            "   entry_zone (string), tp1 (string), tp2 (string), tp3 (string), sl (string), caution (string).\n"
                            "3) If you cannot read exact prices, estimate levels from visible axis/price labels and use conditional phrasing.\n"
                            "4) Prefer breakout/structure: provide BUY above and SELL below (still fill entry_zone/tps/sl).\n"
                            "5) Keep it short and clear.\n"
                        )
                    },
                    {
                        "type": "input_image",
                        "image_url": f"data:image/jpeg;base64,{b64}"
                    }
                ]
            }
        ]
    }

    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json",
    }

    r = requests.post(f"{OPENAI_BASE_URL}/responses", headers=headers, json=payload, timeout=60)
    if r.status_code >= 400:
        raise RuntimeError(f"OpenAI error {r.status_code}: {r.text}")

    data = r.json()

    # Extract output text (Responses API)
    out_text = ""
    for item in data.get("output", []):
        if item.get("type") == "message":
            for c in item.get("content", []):
                if c.get("type") == "output_text":
                    out_text += c.get("text", "")
    out_text = out_text.strip()

    # Try parse JSON
    try:
        result = json.loads(out_text)
        if not isinstance(result, dict):
            raise ValueError("Not dict JSON")
        return result
    except Exception:
        # fallback: return a conservative template if model output wasn't JSON
        return {
            "symbol": "UNKNOWN",
            "timeframe": "UNKNOWN",
            "market_state": "NEUTRAL",
            "action": "WAIT",
            "confidence": 35,
            "entry_zone": "Conditional: BUY above recent high / SELL below recent low",
            "tp1": "TP1: nearest resistance/support",
            "tp2": "TP2: next major level",
            "tp3": "TP3: extended target",
            "sl": "SL: below/above swing level",
            "caution": "Image unclear. Zoom candles + show price axis."
        }

# =========================================================
# Formatting
# =========================================================
def fmt_signal(sig: Dict[str, Any], free_left: Optional[int] = None) -> str:
    symbol = sig.get("symbol", "UNKNOWN")
    tf = sig.get("timeframe", "UNKNOWN")
    state = str(sig.get("market_state", "NEUTRAL")).upper()
    action = str(sig.get("action", "WAIT")).upper()
    conf = sig.get("confidence", 0)

    entry = sig.get("entry_zone", "-")
    tp1 = sig.get("tp1", "-")
    tp2 = sig.get("tp2", "-")
    tp3 = sig.get("tp3", "-")
    sl = sig.get("sl", "-")
    caution = sig.get("caution", "-")

    # Emojis
    state_emoji = "📈" if state == "BULLISH" else "📉" if state == "BEARISH" else "➖"
    action_emoji = "🟢" if action == "BUY" else "🔴" if action == "SELL" else "🟡"

    lines = []
    lines.append(f"{action_emoji} **{action} | {symbol} | {tf} | {conf}%**")
    lines.append(f"{state_emoji} **Market:** {state}")
    lines.append("")
    lines.append(f"🎯 **Entry Zone:** {entry}")
    lines.append(f"✅ **TP1:** {tp1}")
    lines.append(f"✅ **TP2:** {tp2}")
    lines.append(f"✅ **TP3:** {tp3}")
    lines.append(f"🛑 **SL:** {sl}")
    lines.append("")
    lines.append(f"⚠️ **Caution:** {caution}")
    lines.append("")
    lines.append("📚 _Educational only | Risk 1–2%_")

    if free_left is not None:
        lines.append("")
        lines.append(f"🧪 **Free Trial:** {free_left}/{FREE_LIMIT} remaining.  Upgrade: /plans")

    return "\n".join(lines)

def plans_text() -> str:
    return (
        "💎 **Plans**\n\n"
        "🧪 FREE: 5 chart analyses (by image) ثم يطلب الاشتراك.\n"
        "🟦 LITE: إشارات أقل + تحليل صور.\n"
        "🟪 PRO: إشارات قوية تلقائية (بدون صور) + تحليل صور.\n"
        "🟨 VIP_GOLD: إشارات ذهب فقط تلقائية + تحليل صور.\n"
        "🟥 VIP_ALL: إشارات جميع العملات والذهب تلقائية + تحليل صور.\n"
        "⭐ VIP_PRO: أعلى أولوية وأقوى فلترة.\n\n"
        "للتفعيل (Admin):\n"
        "`/setplan <user_id> <plan> <days>`\n"
        "مثال:\n"
        "`/setplan 7269750900 VIP_ALL 365`"
    )

# =========================================================
# Telegram Handlers
# =========================================================
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    ensure_user(user_id)
    p = get_effective_plan(user_id)
    u = get_user(user_id)
    free_used = int(u.get("free_used", 0))
    free_left = max(0, FREE_LIMIT - free_used)

    msg = (
        "🤖 أهلاً! أنا **Trading AI**\n\n"
        "📷 أرسل صورة الشارت وسأعطيك:\n"
        "- Trend (صعود/هبوط/محايد)\n"
        "- Entry Zone\n"
        "- TP1 / TP2 / TP3\n"
        "- SL\n"
        "- Caution\n\n"
        f"👤 **Your plan:** {p}\n"
        f"🧪 **Free remaining:** {free_left}/{FREE_LIMIT}\n\n"
        "ℹ️ /myid  |  💎 /plans"
    )
    await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)

async def cmd_myid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    await update.message.reply_text(f"🆔 Your Telegram ID: `{user_id}`", parse_mode=ParseMode.MARKDOWN)

async def cmd_plans(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(plans_text(), parse_mode=ParseMode.MARKDOWN)

async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    u = get_user(user_id)
    plan = get_effective_plan(user_id)
    until = int(u.get("plan_until") or 0)
    free_used = int(u.get("free_used") or 0)
    free_left = max(0, FREE_LIMIT - free_used)

    until_str = "-"
    if plan != "FREE" and until > 0:
        until_str = datetime.fromtimestamp(until, TZ).strftime("%Y-%m-%d %H:%M")

    txt = (
        f"👤 Plan: **{plan}**\n"
        f"⏳ Until: **{until_str}**\n"
        f"🧪 Free used: **{free_used}/{FREE_LIMIT}** (left {free_left})"
    )
    await update.message.reply_text(txt, parse_mode=ParseMode.MARKDOWN)

async def cmd_setplan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if OWNER_ID and user_id != OWNER_ID:
        await update.message.reply_text("⛔ Admin only.")
        return

    if not context.args or len(context.args) < 3:
        await update.message.reply_text(
            "Usage:\n`/setplan <user_id> <plan> <days>`\n"
            f"plans: {', '.join(PLANS)}",
            parse_mode=ParseMode.MARKDOWN
        )
        return

    try:
        target_id = int(context.args[0])
        plan = context.args[1].upper()
        days = int(context.args[2])
        set_plan(target_id, plan, days)
        await update.message.reply_text(f"✅ Set {target_id} plan={plan} for {days} days")
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")

async def on_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    ensure_user(user_id)
    plan = get_effective_plan(user_id)

    # FREE limitation
    u = get_user(user_id)
    free_used = int(u.get("free_used", 0))
    free_left = max(0, FREE_LIMIT - free_used)

    if plan == "FREE" and free_left <= 0:
        await update.message.reply_text(
            "🔒 Free Trial انتهى.\n\nUpgrade: /plans",
            parse_mode=ParseMode.MARKDOWN
        )
        return

    await update.message.reply_text("📸 Received. Analyzing...")

    # Get largest photo
    photo = update.message.photo[-1]
    file = await context.bot.get_file(photo.file_id)
    img_bytes = await file.download_as_bytearray()

    try:
        sig = await asyncio.to_thread(openai_analyze_chart, bytes(img_bytes))
    except Exception as e:
        await update.message.reply_text(
            "❌ Analysis failed.\n"
            "جرّب صورة أوضح (Zoom candles) + أظهر الأسعار.\n\n"
            f"Error: `{str(e)[:300]}`",
            parse_mode=ParseMode.MARKDOWN
        )
        return

    # decrement free
    if plan == "FREE":
        used = inc_free_used(user_id)
        free_left = max(0, FREE_LIMIT - used)
        msg = fmt_signal(sig, free_left=free_left)
    else:
        msg = fmt_signal(sig, free_left=None)

    await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)

async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # For unknown text - friendly help
    txt = (update.message.text or "").strip().lower()
    if txt in ["/start", "start"]:
        return
    await update.message.reply_text("📷 أرسل صورة الشارت للتحليل.\n💎 /plans | 🆔 /myid")

# =========================================================
# FastAPI (TradingView Webhook)
# =========================================================
app = FastAPI()

@app.get("/")
def root():
    return {"ok": True, "service": "trading-ai-bot"}

def parse_tv_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Expected payload sample:
    {
      "plan": "PRO" or "VIP_ALL" or "VIP_GOLD",
      "symbol": "XAUUSD",
      "timeframe": "M5",
      "action": "BUY"/"SELL",
      "entry_zone": "4440-4443",
      "tp1": "...",
      "tp2": "...",
      "tp3": "...",
      "sl": "...",
      "confidence": 82,
      "market_state": "BULLISH",
      "caution": "news soon"
    }
    """
    plan = str(payload.get("plan", "PRO")).upper()
    symbol = str(payload.get("symbol", "UNKNOWN")).upper()
    tf = str(payload.get("timeframe", "TF")).upper()

    sig = {
        "symbol": symbol,
        "timeframe": tf,
        "market_state": str(payload.get("market_state", "NEUTRAL")).upper(),
        "action": str(payload.get("action", "WAIT")).upper(),
        "confidence": int(payload.get("confidence", 70)),
        "entry_zone": str(payload.get("entry_zone", "Use breakout above/below structure")),
        "tp1": str(payload.get("tp1", "TP1")),
        "tp2": str(payload.get("tp2", "TP2")),
        "tp3": str(payload.get("tp3", "TP3")),
        "sl": str(payload.get("sl", "SL")),
        "caution": str(payload.get("caution", "Manage risk; avoid news spikes")),
    }
    return {"plan": plan, "sig": sig}

def pick_channel(plan: str, symbol: str) -> int:
    plan = plan.upper()
    symbol = symbol.upper()

    # VIP_GOLD -> gold channel if set
    if plan == "VIP_GOLD":
        return VIP_GOLD_CHANNEL_ID or VIP_CHANNEL_ID

    # VIP_ALL / VIP_PRO -> VIP channel
    if plan in ["VIP_ALL", "VIP_PRO"]:
        return VIP_CHANNEL_ID

    # PRO / LITE -> PRO channel
    if plan in ["PRO", "LITE"]:
        return PRO_CHANNEL_ID

    # default
    return PRO_CHANNEL_ID or VIP_CHANNEL_ID or 0

@app.post("/tv_webhook")
async def tv_webhook(request: Request):
    # Optional security
    if TV_WEBHOOK_SECRET:
        got = request.headers.get("X-TV-SECRET", "").strip()
        if got != TV_WEBHOOK_SECRET:
            raise HTTPException(status_code=401, detail="Unauthorized")

    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    parsed = parse_tv_payload(payload)
    plan = parsed["plan"]
    sig = parsed["sig"]

    channel_id = pick_channel(plan, sig.get("symbol", "UNKNOWN"))
    if not channel_id:
        raise HTTPException(status_code=400, detail="Channel ID not configured")

    text = fmt_signal(sig, free_left=None)
    # Add "Auto Signal" label
    text = "⚡ **AUTO SIGNAL**\n\n" + text

    # Send via bot
    if telegram_app is None:
        raise HTTPException(status_code=500, detail="Telegram bot not ready")

    try:
        await telegram_app.bot.send_message(chat_id=channel_id, text=text, parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to send: {e}")

    return JSONResponse({"ok": True, "sent_to": channel_id})

# =========================================================
# Run Telegram in FastAPI lifecycle
# =========================================================
telegram_app: Optional[Application] = None

async def start_telegram():
    global telegram_app
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN missing")

    telegram_app = Application.builder().token(BOT_TOKEN).build()

    telegram_app.add_handler(CommandHandler("start", cmd_start))
    telegram_app.add_handler(CommandHandler("myid", cmd_myid))
    telegram_app.add_handler(CommandHandler("plans", cmd_plans))
    telegram_app.add_handler(CommandHandler("status", cmd_status))
    telegram_app.add_handler(CommandHandler("setplan", cmd_setplan))

    telegram_app.add_handler(MessageHandler(filters.PHOTO, on_photo))
    telegram_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))

    await telegram_app.initialize()
    await telegram_app.start()
    await telegram_app.updater.start_polling(drop_pending_updates=True)

async def stop_telegram():
    global telegram_app
    if telegram_app:
        try:
            await telegram_app.updater.stop()
        except Exception:
            pass
        try:
            await telegram_app.stop()
        except Exception:
            pass
        try:
            await telegram_app.shutdown()
        except Exception:
            pass
        telegram_app = None

@app.on_event("startup")
async def on_startup():
    init_db()
    # start telegram polling in background
    asyncio.create_task(start_telegram())

@app.on_event("shutdown")
async def on_shutdown():
    await stop_telegram()
