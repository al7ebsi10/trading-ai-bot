import os
import json
import time
import sqlite3
import logging
from typing import Optional, Dict, Any, Tuple

import requests
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters
)

# =========================
# ENV
# =========================
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
ADMIN_USER_ID = int(os.getenv("ADMIN_USER_ID", "0") or "0")

# Optional tuning
DEFAULT_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini").strip()  # you can change later
VIP_MIN_CONFIDENCE_ALL = int(os.getenv("VIP_MIN_CONF_ALL", "65"))  # ALL mode threshold
VIP_MIN_CONFIDENCE_GOLD = int(os.getenv("VIP_MIN_CONF_GOLD", "70")) # GOLD mode threshold

# Modes:
# - "ALL": all symbols/timeframes (recommended for VIP)
# - "GOLD": XAUUSD only + M5/M15 stricter (higher win-rate marketing)
DEFAULT_MODE = os.getenv("DEFAULT_MODE", "ALL").strip().upper()

DB_PATH = os.getenv("DB_PATH", "vip.db")

logging.basicConfig(level=logging.INFO)


# =========================
# DB (VIP)
# =========================
def db_conn():
    return sqlite3.connect(DB_PATH)

def init_db():
    with db_conn() as con:
        cur = con.cursor()
        cur.execute("""
        CREATE TABLE IF NOT EXISTS vip_users(
            user_id INTEGER PRIMARY KEY,
            expires_at INTEGER NOT NULL
        )
        """)
        cur.execute("""
        CREATE TABLE IF NOT EXISTS settings(
            k TEXT PRIMARY KEY,
            v TEXT NOT NULL
        )
        """)
        con.commit()

def set_setting(k: str, v: str):
    with db_conn() as con:
        cur = con.cursor()
        cur.execute("INSERT INTO settings(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (k, v))
        con.commit()

def get_setting(k: str, default: str = "") -> str:
    with db_conn() as con:
        cur = con.cursor()
        cur.execute("SELECT v FROM settings WHERE k=?", (k,))
        row = cur.fetchone()
        return row[0] if row else default

def add_vip(user_id: int, days: int):
    expires_at = int(time.time()) + int(days) * 86400
    with db_conn() as con:
        cur = con.cursor()
        cur.execute("INSERT INTO vip_users(user_id, expires_at) VALUES(?,?) "
                    "ON CONFLICT(user_id) DO UPDATE SET expires_at=excluded.expires_at",
                    (user_id, expires_at))
        con.commit()

def remove_vip(user_id: int):
    with db_conn() as con:
        cur = con.cursor()
        cur.execute("DELETE FROM vip_users WHERE user_id=?", (user_id,))
        con.commit()

def vip_expires_at(user_id: int) -> Optional[int]:
    with db_conn() as con:
        cur = con.cursor()
        cur.execute("SELECT expires_at FROM vip_users WHERE user_id=?", (user_id,))
        row = cur.fetchone()
        return int(row[0]) if row else None

def is_vip(user_id: int) -> bool:
    exp = vip_expires_at(user_id)
    return bool(exp and exp > int(time.time()))

def vip_days_left(user_id: int) -> int:
    exp = vip_expires_at(user_id) or 0
    left = exp - int(time.time())
    return max(0, left // 86400)


# =========================
# UI / MARKETING
# =========================
PLANS_TEXT = """\
💎 Trading AI – Plans

$49  - GOLD VIP (XAUUSD, M5/M15)
$99  - VIP ALL (All pairs & timeframes)
$119 - VIP PRO (All pairs & timeframes + priority updates)

To activate VIP, contact admin.
"""

FREE_TEXT = """\
🔒 VIP Feature
This bot provides VIP trading signals.

Type /plans to see pricing.
"""

HELP_TEXT = """\
🤖 Trading AI Bot (EN)

Commands:
- /start
- /plans
- /status  (check your VIP status)
- /mode gold | /mode all   (admin only)
- /vipadd <user_id> <days> (admin only)
- /vipremove <user_id>     (admin only)

Usage:
- Send a chart image (candles only is OK).
The bot will return a clean signal:
BUY/SELL/WAIT + Entry/SL/TP1/TP2/TP3 + Confidence.
"""


# =========================
# OUTPUT FORMAT (SHORT, CLEAN)
# =========================
def fmt_signal(res: Dict[str, Any]) -> str:
    """
    Expected keys:
    action: BUY/SELL/WAIT
    pair: e.g. XAUUSD
    timeframe: e.g. M5
    bias: Bullish/Bearish/Sideways
    confidence: 0-100
    entry, sl, tp1, tp2, tp3: floats (only if action != WAIT)
    note: short string
    """
    action = (res.get("action") or "WAIT").upper()
    pair = (res.get("pair") or "N/A").upper()
    tf = (res.get("timeframe") or "N/A").upper()
    bias = (res.get("bias") or "Neutral").title()
    conf = int(res.get("confidence") or 0)
    note = (res.get("note") or "").strip()

    if action == "WAIT":
        # Keep it short, no long essay
        return f"""\
🟡 WAIT | {pair} {tf} | {conf}%

No clean confirmation.
Wait for clearer price action.

⚠️ Risk 1–2% | Educational only""".strip()

    entry = res.get("entry")
    sl = res.get("sl")
    tp1 = res.get("tp1")
    tp2 = res.get("tp2")
    tp3 = res.get("tp3")

    # Minimal note (1 line max)
    if note:
        note_line = f"\n🧠 Note: {note[:120]}"
    else:
        note_line = ""

    return f"""\
🟢 {action} | {pair} {tf} | {bias} | {conf}%

🎯 Entry: {entry}
🛑 SL: {sl}
✅ TP1: {tp1}
✅ TP2: {tp2}
✅ TP3: {tp3}{note_line}

⚠️ Risk 1–2% | Educational only""".strip()


# =========================
# MODE RULES (Reduce WAIT / Raise accuracy)
# =========================
def current_mode() -> str:
    return get_setting("MODE", DEFAULT_MODE).upper() or "ALL"

def mode_threshold() -> int:
    m = current_mode()
    if m == "GOLD":
        return VIP_MIN_CONFIDENCE_GOLD
    return VIP_MIN_CONFIDENCE_ALL

def mode_constraints_prompt() -> str:
    m = current_mode()
    if m == "GOLD":
        return (
            "Mode is GOLD ONLY:\n"
            "- Focus on XAUUSD (Gold) primarily.\n"
            "- Prefer M5/M15.\n"
            "- Be stricter; avoid signals unless clear.\n"
        )
    return (
        "Mode is ALL:\n"
        "- Any symbol/timeframe allowed.\n"
        "- Still avoid low-quality signals.\n"
    )


# =========================
# OPENAI VISION CALL
# =========================
def call_openai_vision(image_bytes: bytes, extra_prompt: str = "") -> Tuple[Optional[Dict[str, Any]], str]:
    """
    Returns (json_result_or_none, raw_text).
    Uses OpenAI Responses API style via HTTP.
    """
    if not OPENAI_API_KEY:
        return None, "Missing OPENAI_API_KEY"

    # Strong JSON-only contract to avoid JSONDecodeError
    system = (
        "You are a professional trading signal assistant.\n"
        "Return ONLY valid JSON. No markdown, no extra text.\n"
        "Your JSON must follow this schema:\n"
        "{"
        "\"action\":\"BUY|SELL|WAIT\","
        "\"pair\":\"string\","
        "\"timeframe\":\"string\","
        "\"bias\":\"Bullish|Bearish|Sideways\","
        "\"confidence\": number(0-100),"
        "\"entry\": number|null,"
        "\"sl\": number|null,"
        "\"tp1\": number|null,"
        "\"tp2\": number|null,"
        "\"tp3\": number|null,"
        "\"note\":\"short string\""
        "}\n"
        "Rules:\n"
        "- If no clear confirmation, action MUST be WAIT.\n"
        "- If action is WAIT: entry/sl/tp1/tp2/tp3 must be null.\n"
        "- If BUY/SELL: MUST provide entry, sl, tp1,tp2,tp3.\n"
        "- Keep note short (<= 120 chars).\n"
        "- If the image shows only candles without RSI/Stoch, still analyze using price action, EMAs, key levels, structure.\n"
        "- Patterns: mention ONLY if clear; else say 'No clear pattern'.\n"
        "- Prefer high accuracy over frequent signals.\n"
    )

    user_prompt = (
        f"{mode_constraints_prompt()}\n"
        f"Min confidence threshold for action (BUY/SELL) is: {mode_threshold()}.\n"
        "Analyze the provided chart screenshot and create ONE clean decision.\n"
        "Output must be JSON only.\n"
    )
    if extra_prompt:
        user_prompt += "\n" + extra_prompt.strip()

    url = "https://api.openai.com/v1/responses"
    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json"
    }

    # base64 encode image
    import base64
    b64 = base64.b64encode(image_bytes).decode("utf-8")

    payload = {
        "model": DEFAULT_MODEL,
        "input": [
            {"role": "system", "content": [{"type": "text", "text": system}]},
            {"role": "user", "content": [
                {"type": "text", "text": user_prompt},
                {"type": "input_image", "image_url": f"data:image/jpeg;base64,{b64}"}
            ]}
        ],
        "temperature": 0.2
    }

    try:
        r = requests.post(url, headers=headers, json=payload, timeout=45)
        raw = r.text
        if r.status_code != 200:
            return None, raw

        data = r.json()

        # Responses API often returns text in output[0].content[0].text
        # We handle a couple shapes defensively:
        text = ""
        try:
            # typical
            out = data.get("output", [])
            if out and out[0].get("content"):
                text = out[0]["content"][0].get("text", "") or ""
        except Exception:
            pass

        if not text:
            # fallback: sometimes "output_text" is present
            text = data.get("output_text", "") or ""

        text = (text or "").strip()
        if not text:
            return None, raw

        # Parse strict JSON
        try:
            j = json.loads(text)
            return j, text
        except json.JSONDecodeError:
            # second attempt: extract first JSON object
            start = text.find("{")
            end = text.rfind("}")
            if start != -1 and end != -1 and end > start:
                candidate = text[start:end+1]
                try:
                    j = json.loads(candidate)
                    return j, text
                except Exception:
                    return None, text
            return None, text

    except Exception as e:
        return None, str(e)


# =========================
# DECISION SANITIZER (NO CONTRADICTION, SHORT)
# =========================
def sanitize_result(j: Dict[str, Any]) -> Dict[str, Any]:
    action = (j.get("action") or "WAIT").upper().strip()
    pair = (j.get("pair") or "XAUUSD").upper().strip()
    tf = (j.get("timeframe") or "M5").upper().strip()
    bias = (j.get("bias") or "Sideways").strip()
    conf = int(float(j.get("confidence") or 0))

    # Apply threshold: if below threshold, force WAIT
    if action in ("BUY", "SELL") and conf < mode_threshold():
        action = "WAIT"

    def num_or_none(x):
        if x is None:
            return None
        try:
            return round(float(x), 2)
        except Exception:
            return None

    entry = num_or_none(j.get("entry"))
    sl = num_or_none(j.get("sl"))
    tp1 = num_or_none(j.get("tp1"))
    tp2 = num_or_none(j.get("tp2"))
    tp3 = num_or_none(j.get("tp3"))
    note = (j.get("note") or "").strip()

    if action == "WAIT":
        entry = sl = tp1 = tp2 = tp3 = None

    # Ensure BUY/SELL has targets; otherwise WAIT
    if action in ("BUY", "SELL"):
        if any(v is None for v in [entry, sl, tp1, tp2, tp3]):
            action = "WAIT"
            entry = sl = tp1 = tp2 = tp3 = None

    # clamp confidence
    conf = max(0, min(100, conf))

    return {
        "action": action,
        "pair": pair,
        "timeframe": tf,
        "bias": bias.title() if isinstance(bias, str) else "Sideways",
        "confidence": conf,
        "entry": entry,
        "sl": sl,
        "tp1": tp1,
        "tp2": tp2,
        "tp3": tp3,
        "note": note[:120] if note else ""
    }


# =========================
# TELEGRAM HANDLERS
# =========================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🤖 Trading AI\nSend a chart image to get a clean signal.\nType /plans for pricing.\nType /help for commands.")

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(HELP_TEXT)

async def plans(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(PLANS_TEXT)

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if is_vip(uid):
        await update.message.reply_text(f"✅ VIP Active\nDays left: {vip_days_left(uid)}\nMode: {current_mode()}")
    else:
        await update.message.reply_text(f"🔒 VIP Inactive\nMode: {current_mode()}\n\n{FREE_TEXT}")

async def mode_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid != ADMIN_USER_ID:
        await update.message.reply_text("❌ Admin only.")
        return

    if not context.args:
        await update.message.reply_text(f"Mode: {current_mode()}\nUse: /mode gold OR /mode all")
        return

    m = context.args[0].strip().upper()
    if m not in ("GOLD", "ALL"):
        await update.message.reply_text("❌ Invalid mode. Use /mode gold or /mode all")
        return

    set_setting("MODE", m)
    await update.message.reply_text(f"✅ Mode updated: {m}")

async def vipadd_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid != ADMIN_USER_ID:
        await update.message.reply_text("❌ Admin only.")
        return
    if len(context.args) < 2:
        await update.message.reply_text("Usage: /vipadd <user_id> <days>\nExample: /vipadd 123456789 30")
        return
    try:
        user_id = int(context.args[0])
        days = int(context.args[1])
        add_vip(user_id, days)
        await update.message.reply_text(f"✅ VIP added: {user_id} for {days} days")
    except Exception:
        await update.message.reply_text("❌ Invalid arguments.")

async def vipremove_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid != ADMIN_USER_ID:
        await update.message.reply_text("❌ Admin only.")
        return
    if len(context.args) < 1:
        await update.message.reply_text("Usage: /vipremove <user_id>")
        return
    try:
        user_id = int(context.args[0])
        remove_vip(user_id)
        await update.message.reply_text(f"✅ VIP removed: {user_id}")
    except Exception:
        await update.message.reply_text("❌ Invalid user_id.")


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id

    # VIP gating
    if not is_vip(uid):
        await update.message.reply_text(FREE_TEXT)
        await update.message.reply_text("💳 Type /plans to subscribe.")
        return

    await update.message.reply_text("📸 Received. Analyzing...")

    try:
        photo = update.message.photo[-1]
        file = await photo.get_file()
        image_bytes = await file.download_as_bytearray()

        # AI analysis
        j, raw = call_openai_vision(bytes(image_bytes))
        if not j:
            # Keep error short (no long logs to user)
            await update.message.reply_text("❌ Analysis failed. Try again with a clearer chart screenshot.")
            return

        res = sanitize_result(j)

        # If mode GOLD, enforce XAUUSD preference in output name only (analysis already guided)
        if current_mode() == "GOLD":
            # If model returned other symbol, we still show it but you can force:
            # res["pair"] = "XAUUSD"
            pass

        msg = fmt_signal(res)
        await update.message.reply_text(msg)

    except Exception as e:
        logging.exception(e)
        await update.message.reply_text("❌ Error while processing image. Please try again.")


# =========================
# MAIN
# =========================
def main():
    if not BOT_TOKEN:
        raise RuntimeError("Missing BOT_TOKEN env var")

    init_db()
    # persist mode in DB if missing
    if not get_setting("MODE"):
        set_setting("MODE", DEFAULT_MODE)

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("plans", plans))
    app.add_handler(CommandHandler("status", status))

    # Admin
    app.add_handler(CommandHandler("mode", mode_cmd))
    app.add_handler(CommandHandler("vipadd", vipadd_cmd))
    app.add_handler(CommandHandler("vipremove", vipremove_cmd))

    # Photos
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))

    app.run_polling()


if __name__ == "__main__":
    main()
