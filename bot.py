# =========================================
# Trading AI Telegram Bot – Commercial Build
# English first, Arabic below
# Lite (free) + VIP Auto + Gold Mode (VIP)
# Plans: 49$ / 99$ / 119$
# Photo analysis + /signal command
# =========================================

import os
import asyncio
import re
import json
import base64
import html
import sqlite3
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters

from openai import OpenAI

# ================== CONFIG ==================
BOT_TOKEN = (os.getenv("BOT_TOKEN") or "").strip()
OPENAI_API_KEY = (os.getenv("OPENAI_API_KEY") or "").strip()
OUTPUT_LANG = (os.getenv("OUTPUT_LANG") or "EN").strip().upper()  # EN or BOTH
ADMIN_RAW = (os.getenv("ADMIN_USER_ID") or "").strip()
ADMIN_ID = int(ADMIN_RAW) if ADMIN_RAW.isdigit() else None
DB_PATH = (os.getenv("VIP_DB_PATH") or "vip.db").strip()

# Prices (marketing)
PRICE_AUTO = "49$"
PRICE_GOLD = "99$"
PRICE_BUNDLE = "119$"

# Thresholds
VIP_AUTO_MIN_PROB = 65
GOLD_MODE_MIN_PROB = 70
GOLD_MODE_SYMBOLS = {"XAUUSD", "GOLD"}
GOLD_MODE_TFS = {"M5", "M15"}

# Model
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")

client = OpenAI(api_key=OPENAI_API_KEY)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
log = logging.getLogger("TradingAI")


# ================== DB (VIP) ==================
def _db():
    return sqlite3.connect(DB_PATH, check_same_thread=False)

def db_init():
    con = _db()
    cur = con.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS vip_users (
            user_id INTEGER PRIMARY KEY,
            expires_at_utc TEXT NOT NULL
        )
    """)
    con.commit()
    con.close()

def set_vip(user_id: int, days: int) -> datetime:
    expires = datetime.now(timezone.utc) + timedelta(days=days)
    con = _db()
    cur = con.cursor()
    cur.execute(
        "INSERT INTO vip_users(user_id, expires_at_utc) VALUES(?, ?) "
        "ON CONFLICT(user_id) DO UPDATE SET expires_at_utc=excluded.expires_at_utc",
        (user_id, expires.isoformat()),
    )
    con.commit()
    con.close()
    return expires

def remove_vip(user_id: int):
    con = _db()
    cur = con.cursor()
    cur.execute("DELETE FROM vip_users WHERE user_id=?", (user_id,))
    con.commit()
    con.close()

def get_vip_expiry(user_id: int) -> Optional[datetime]:
    con = _db()
    cur = con.cursor()
    cur.execute("SELECT expires_at_utc FROM vip_users WHERE user_id=?", (user_id,))
    row = cur.fetchone()
    con.close()
    if not row:
        return None
    try:
        return datetime.fromisoformat(row[0]).astimezone(timezone.utc)
    except Exception:
        return None

def is_vip(user_id: int) -> bool:
    exp = get_vip_expiry(user_id)
    return bool(exp and datetime.now(timezone.utc) < exp)

def is_admin(user_id: int) -> bool:
    return ADMIN_ID is not None and user_id == ADMIN_ID


# ================== UTIL ==================
def clean(s: str) -> str:
    s = (s or "").strip()
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s

def clip(s: str, n: int = 160) -> str:
    s = (s or "").strip()
    return s if len(s) <= n else (s[: n - 1].rstrip() + "…")

def act_icon(a: str) -> str:
    a = (a or "").upper().strip()
    if a == "BUY":
        return "🟢 BUY"
    if a == "SELL":
        return "🔴 SELL"
    return "🟡 WAIT"

def g(d: Dict[str, Any], k: str, fb: str) -> str:
    v = d.get(k)
    v = "" if v is None else str(v).strip()
    return v if v else fb

def prob_fmt(d: Dict[str, Any], fb: str = "--") -> str:
    try:
        p = int(float(d.get("probability", 0)))
        p = max(0, min(100, p))
        return f"{p}%"
    except Exception:
        return fb

async def send_pre(msg, text: str):
    safe = html.escape(text or "")
    await msg.reply_text(f"<pre>{safe}</pre>", parse_mode="HTML")


# ================== OUTPUT FORMAT ==================
def format_signal(en: Dict[str, Any], ar: Dict[str, Any], *, include_marketing: bool = False) -> str:
    """Return formatted message.
    OUTPUT_LANG:
      - EN   : English only
      - BOTH : English then Arabic
    """

    def _fmt_one(d: Dict[str, Any], *, lang: str) -> str:
        action = str(d.get("action") or "WAIT").upper()
        symbol = str(d.get("symbol") or "XAUUSD").upper()
        tf = str(d.get("timeframe") or "M5").upper()
        trend = str(d.get("trend") or "Not clear")
        conf = str(d.get("confidence") or "Low")
        prob = int(d.get("probability") or 55)

        entry = d.get("entry") or "Not clear"
        sl = d.get("sl") or "Not clear"
        tp1 = d.get("tp1") or "Not clear"
        tp2 = d.get("tp2") or "Not clear"
        tp3 = d.get("tp3") or "Not clear"

        if action == "BUY":
            head = f"🟢 BUY | {symbol} {tf} | {trend} | {conf} {prob}%"
        elif action == "SELL":
            head = f"🔴 SELL | {symbol} {tf} | {trend} | {conf} {prob}%"
        else:
            head = f"🟡 WAIT | {symbol} {tf} | {trend} | {conf} {prob}%"

        lines = [head]

        if action in ("BUY", "SELL"):
            lines.append(f"🎯 Entry: {entry}")
            lines.append(f"🛑 SL: {sl}")
            lines.append(f"✅ TP1: {tp1} | TP2: {tp2} | TP3: {tp3}")
        else:
            if entry != "Not clear":
                lines.append(f"🎯 Key level: {entry}")

        reason = (d.get("reason") or d.get("wait_reason") or "").strip()
        if reason:
            reason = reason.replace("\n", " ").strip()
            if len(reason) > 180:
                reason = reason[:177].rstrip() + "..."
            lines.append(f"🧠 {reason}")

        if lang == "EN":
            lines.append("⚠️ Warning: Educational only | Risk 1–2%")
        else:
            lines.append("⚠️ تنبيه: تعليمي فقط | المخاطرة 1–2%")

        if include_marketing:
            lines.append("")
            lines.append("💎 VIP unlock: /plans")

        return "\n".join(lines)

    blocks = [_fmt_one(en, lang="EN")]
    if OUTPUT_LANG == "BOTH":
        blocks += ["────────────", _fmt_one(ar, lang="AR")]
    return clean("\n".join(blocks))


# ================== AI (Robust JSON) ==================
def extract_json(text: str) -> Dict[str, Any]:
    text = (text or "").strip()
    if not text:
        raise ValueError("Empty model output")
    try:
        return json.loads(text)
    except Exception:
        pass

    # Try to find a JSON object within text
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        return json.loads(text[start:end + 1])

    raise ValueError("No JSON found")

def is_gold_mode(symbol: str, timeframe: str) -> bool:
    s = (symbol or "").upper().strip()
    tf = (timeframe or "").upper().strip()
    return (s in GOLD_MODE_SYMBOLS) and (tf in GOLD_MODE_TFS)

def build_prompt(mode: str, min_prob: int) -> str:
    # mode: "lite" or "vip"
    return f"""
You are a trading signal generator. Output JSON ONLY (English fields only).

Mode: {mode}
Minimum probability for BUY/SELL: {min_prob}

Rules:
- Decide BUY or SELL only if probability >= {min_prob} and trend direction is clear.
- Otherwise WAIT.
- NEVER invent exact prices. If Entry/SL/TP are not readable, set them to "Not clear" (English) and "غير واضح" (Arabic).
- Keep outputs short and clean.
- Provide TP1, TP2, TP3 when possible.
- If indicators (RSI/Stoch) are not visible, continue using price action / trend / structure. Do NOT force WAIT just because indicators are missing.
- WAIT must include a short wait_reason.

Output schema (VALID JSON ONLY):
{{
  "en": {{
    "symbol":"...", "timeframe":"...", "trend":"Bullish/Bearish/Sideways",
    "action":"BUY/SELL/WAIT",
    "probability":0, "confidence":"High/Medium/Low",
    "entry":"...", "sl":"...", "tp1":"...", "tp2":"...", "tp3":"...",
    "wait_reason":"..."
  }},
  "ar": {{
    "symbol":"...", "timeframe":"...", "trend":"صاعد/هابط/تذبذب",
    "action":"BUY/SELL/WAIT",
    "probability":0, "confidence":"High/Medium/Low",
    "entry":"...", "sl":"...", "tp1":"...", "tp2":"...", "tp3":"...",
    "wait_reason":"..."
  }}
}}
""".strip()

def analyze_image(image_bytes: bytes, *, mode: str, min_prob: int) -> str:
    b64 = base64.b64encode(bytes(image_bytes)).decode("utf-8")
    prompt = build_prompt(mode=mode, min_prob=min_prob)

    last_err = None
    for attempt in range(2):
        try:
            resp = client.responses.create(model=OPENAI_MODEL, input=prompt)
            data = extract_json(resp.output_text)

            # Flat JSON (English only) or {en, ar}
            if isinstance(data, dict) and ('en' not in data and 'ar' not in data):
                en = data
                ar = {}
            else:
                en = data.get('en', {}) if isinstance(data, dict) else {}
                ar = data.get('ar', {}) if isinstance(data, dict) else {}

            en['action'] = str(en.get('action') or 'WAIT').upper()
            if en['action'] not in ('BUY','SELL','WAIT'):
                en['action'] = 'WAIT'
            for k in ["symbol", "timeframe", "trend", "action", "probability", "confidence", "entry", "sl", "tp1", "tp2", "tp3", "wait_reason"]:
                en.setdefault(k, "Not clear" if k not in ("probability",) else 0)
                ar.setdefault(k, "غير واضح" if k not in ("probability",) else 0)

            include_marketing = (mode == "lite")
            return format_signal(en, ar, include_marketing=include_marketing)

        except Exception as e:
            last_err = e
            log.warning(f"analyze_image attempt {attempt+1}/2 failed: {e}")

    # Fallback
    log.warning(f"analyze_image fallback used. last_err={last_err}")
    en_fb = {
        "symbol": "Not clear", "timeframe": "?", "trend": "Not clear",
        "action": "WAIT", "probability": 55, "confidence": "Low",
        "entry": "Not clear", "sl": "Not clear", "tp1": "Not clear", "tp2": "Not clear", "tp3": "Not clear",
        "wait_reason": "Image unclear. Please resend a clearer chart."
    }
    ar_fb = {
        "symbol": "غير واضح", "timeframe": "؟", "trend": "غير واضح",
        "action": "WAIT", "probability": 55, "confidence": "Low",
        "entry": "غير واضح", "sl": "غير واضح", "tp1": "غير واضح", "tp2": "غير واضح", "tp3": "غير واضح",
        "wait_reason": "الصورة غير واضحة. أعد إرسال الشارت بشكل أوضح."
    }
    return format_signal(en_fb, ar_fb, include_marketing=(mode == "lite"))

def generate_signal_text(symbol: str, timeframe: str, *, min_prob: int, gold_mode: bool) -> str:
    symbol = (symbol or "XAUUSD").upper().strip()
    timeframe = (timeframe or "M5").upper().strip()
    mode_name = "Gold Mode" if gold_mode else "VIP Auto"

    prompt = f"""
You are a trading signal generator. Output JSON ONLY (English fields only).

Mode: {mode_name}
Symbol: {symbol}
Timeframe: {timeframe}
Minimum probability for BUY/SELL: {min_prob}

Rules:
- You do NOT have live price feed. Do NOT invent exact prices.
- If you can't provide Entry/SL/TP clearly, set them to "Not clear" / "غير واضح".
- Decide BUY/SELL only if probability >= {min_prob} and trend is clear, else WAIT.
- Output short wait_reason.
- VALID JSON only.

Schema:
{{
  "en": {{
    "symbol":"{symbol}", "timeframe":"{timeframe}", "trend":"Bullish/Bearish/Sideways",
    "action":"BUY/SELL/WAIT",
    "probability":0, "confidence":"High/Medium/Low",
    "entry":"...", "sl":"...", "tp1":"...", "tp2":"...", "tp3":"...",
    "wait_reason":"..."
  }},
  "ar": {{
    "symbol":"{symbol}", "timeframe":"{timeframe}", "trend":"صاعد/هابط/تذبذب",
    "action":"BUY/SELL/WAIT",
    "probability":0, "confidence":"High/Medium/Low",
    "entry":"...", "sl":"...", "tp1":"...", "tp2":"...", "tp3":"...",
    "wait_reason":"..."
  }}
}}
""".strip()

    last_err = None
    for attempt in range(2):
        try:
            resp = client.responses.create(model=OPENAI_MODEL, input=prompt)
            data = extract_json(resp.output_text)

            # Flat JSON (English only) or {en, ar}
            if isinstance(data, dict) and ('en' not in data and 'ar' not in data):
                en = data
                ar = {}
            else:
                en = data.get('en', {}) if isinstance(data, dict) else {}
                ar = data.get('ar', {}) if isinstance(data, dict) else {}

            en['action'] = str(en.get('action') or 'WAIT').upper()
            if en['action'] not in ('BUY','SELL','WAIT'):
                en['action'] = 'WAIT'
            for k in ["symbol", "timeframe", "trend", "action", "probability", "confidence", "entry", "sl", "tp1", "tp2", "tp3", "wait_reason"]:
                en.setdefault(k, "Not clear" if k not in ("probability",) else 0)
                ar.setdefault(k, "غير واضح" if k not in ("probability",) else 0)

            return format_signal(en, ar, include_marketing=False)
        except Exception as e:
            last_err = e
            log.warning(f"generate_signal_text attempt {attempt+1}/2 failed: {e}")

    log.warning(f"generate_signal_text fallback used. last_err={last_err}")
    en_fb = {
        "symbol": symbol, "timeframe": timeframe, "trend": "Not clear",
        "action": "WAIT", "probability": 55, "confidence": "Low",
        "entry": "Not clear", "sl": "Not clear", "tp1": "Not clear", "tp2": "Not clear", "tp3": "Not clear",
        "wait_reason": "No chart/price feed. Send a screenshot for accurate levels."
    }
    ar_fb = {
        "symbol": symbol, "timeframe": timeframe, "trend": "غير واضح",
        "action": "WAIT", "probability": 55, "confidence": "Low",
        "entry": "غير واضح", "sl": "غير واضح", "tp1": "غير واضح", "tp2": "غير واضح", "tp3": "غير واضح",
        "wait_reason": "لا يوجد شارت/سعر مباشر. أرسل صورة للشارت لتحديد المستويات بدقة."
    }
    return format_signal(en_fb, ar_fb, include_marketing=False)


# ================== MARKETING TEXT ==================
def plans_text() -> str:
    return clean(f"""
💎 VIP Plans

• VIP Auto: {PRICE_AUTO} / month
  - All pairs & timeframes
  - Smart filtering (min {VIP_AUTO_MIN_PROB}%)

• Gold Mode: {PRICE_GOLD} / month
  - XAUUSD only (M5/M15)
  - Higher accuracy (min {GOLD_MODE_MIN_PROB}%)

• Bundle: {PRICE_BUNDLE} / month ⭐
  - VIP Auto + Gold Mode

To activate: message admin with /myid
""".strip())

def vip_locked_text() -> str:
    return clean(f"""
🔒 VIP Feature

💎 Plans:
• VIP Auto: {PRICE_AUTO} / month
• Gold Mode: {PRICE_GOLD} / month
• Bundle: {PRICE_BUNDLE} / month ⭐

To activate: send /myid to admin.
""".strip())


# ================== COMMANDS ==================
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = clean(
        "✅ Bot is running\n"
        "📸 Send a chart image for analysis (Lite free)\n"
        "🔒 VIP: /signal XAUUSD M5\n"
        "💎 Plans: /plans\n"
        "🆔 Your ID: /myid"
    )
    await update.effective_message.reply_text(txt)

async def cmd_plans(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await send_pre(update.effective_message, plans_text())

async def cmd_myid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    exp = get_vip_expiry(uid)
    vip_status = "✅ VIP Active" if is_vip(uid) else "🔒 VIP Not active"
    if exp and is_vip(uid):
        exp_str = exp.strftime("%Y-%m-%d %H:%M UTC")
        vip_status += f"\nExpires: {exp_str}"

    admin_hint = ""
    if ADMIN_ID is None:
        admin_hint = "\n\n⚠️ ADMIN_USER_ID not set yet (admin commands disabled)."

    await update.effective_message.reply_text(
        f"🆔 Your Telegram ID: {uid}\n{vip_status}{admin_hint}"
    )

async def cmd_signal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_vip(uid) and not is_admin(uid):
        await send_pre(update.effective_message, vip_locked_text())
        return

    symbol = (context.args[0] if len(context.args) >= 1 else "XAUUSD").upper().strip()
    tf = (context.args[1] if len(context.args) >= 2 else "M5").upper().strip()

    gold = is_gold_mode(symbol, tf)
    min_prob = GOLD_MODE_MIN_PROB if gold else VIP_AUTO_MIN_PROB

    await update.effective_message.reply_text("⏳ Generating VIP signal...")
    msg = generate_signal_text(symbol, tf, min_prob=min_prob, gold_mode=gold)
    await send_pre(update.effective_message, msg)

# -------- Admin VIP management --------
async def cmd_vipadd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_admin(uid):
        await update.effective_message.reply_text("❌ Admin only.")
        return
    if len(context.args) < 2 or (not context.args[0].isdigit()) or (not context.args[1].isdigit()):
        await update.effective_message.reply_text("Usage: /vipadd <user_id> <days>")
        return
    user_id = int(context.args[0])
    days = int(context.args[1])
    exp = set_vip(user_id, days)
    await update.effective_message.reply_text(
        f"✅ VIP added for {user_id} for {days} days.\nExpires: {exp.strftime('%Y-%m-%d %H:%M UTC')}"
    )

async def cmd_vipremove(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_admin(uid):
        await update.effective_message.reply_text("❌ Admin only.")
        return
    if len(context.args) < 1 or (not context.args[0].isdigit()):
        await update.effective_message.reply_text("Usage: /vipremove <user_id>")
        return
    user_id = int(context.args[0])
    remove_vip(user_id)
    await update.effective_message.reply_text(f"✅ VIP removed for {user_id}")


# ================== PHOTO HANDLER ==================
async def on_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    uid = update.effective_user.id

    await msg.reply_text("📸 Received. Analyzing...")

    try:
        photo = msg.photo[-1]
        tg_file = await context.bot.get_file(photo.file_id)
        image_bytes = await tg_file.download_as_bytearray()

        # Decide mode
        vip = is_vip(uid) or is_admin(uid)

        # VIP photo analysis uses stricter logic (but still simple output)
        # For gold charts, we apply Gold Mode min_prob if symbol/tf can be inferred by model.
        # We can't reliably parse symbol/tf before analysis, so:
        # - VIP analysis uses VIP_AUTO_MIN_PROB
        # - Model will include symbol/timeframe; users can send /signal for forced Gold Mode.
        mode = "vip" if vip else "lite"
        min_prob = VIP_AUTO_MIN_PROB if vip else 60  # Lite slightly looser for engagement

        result = analyze_image(image_bytes, mode=mode, min_prob=min_prob)
        await send_pre(msg, result)

    except Exception as e:
        log.exception(f"PHOTO_ERROR: {e}")
        await msg.reply_text("🟡 WAIT\nPlease resend a clearer chart image.")


# ================== RUN ==================
def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is missing.")
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY is missing.")

    db_init()

    app = Application.builder().token(BOT_TOKEN).build()

    # Public
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("plans", cmd_plans))
    app.add_handler(CommandHandler("myid", cmd_myid))
    app.add_handler(CommandHandler("signal", cmd_signal))

    # Admin
    app.add_handler(CommandHandler("vipadd", cmd_vipadd))
    app.add_handler(CommandHandler("vipremove", cmd_vipremove))

    # Photos
    app.add_handler(MessageHandler(filters.PHOTO, on_photo))

    log.info("Trading AI Bot running...")
    app.run_polling()

if __name__ == "__main__":
    main()
