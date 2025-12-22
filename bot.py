import os
import logging
import base64
import json
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

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

ADMIN_USER_ID_RAW = os.environ.get("ADMIN_USER_ID", "").strip()
ADMIN_ID = int(ADMIN_USER_ID_RAW) if ADMIN_USER_ID_RAW.isdigit() else None

DB_PATH = os.environ.get("VIP_DB_PATH", "vip.db")

client = OpenAI(api_key=OPENAI_API_KEY)

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO
)
logger = logging.getLogger("TradingAI")


# ================== DB (VIP with expiry) ==================
def db_connect():
    return sqlite3.connect(DB_PATH, check_same_thread=False)

def db_init():
    con = db_connect()
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
    con = db_connect()
    cur = con.cursor()
    cur.execute(
        "INSERT INTO vip_users(user_id, expires_at_utc) VALUES(?, ?) "
        "ON CONFLICT(user_id) DO UPDATE SET expires_at_utc=excluded.expires_at_utc",
        (user_id, expires.isoformat())
    )
    con.commit()
    con.close()
    return expires

def remove_vip(user_id: int):
    con = db_connect()
    cur = con.cursor()
    cur.execute("DELETE FROM vip_users WHERE user_id = ?", (user_id,))
    con.commit()
    con.close()

def get_vip_expiry(user_id: int) -> Optional[datetime]:
    con = db_connect()
    cur = con.cursor()
    cur.execute("SELECT expires_at_utc FROM vip_users WHERE user_id = ?", (user_id,))
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

def list_vips(limit: int = 50):
    con = db_connect()
    cur = con.cursor()
    cur.execute(
        "SELECT user_id, expires_at_utc FROM vip_users ORDER BY expires_at_utc DESC LIMIT ?",
        (limit,)
    )
    rows = cur.fetchall()
    con.close()
    out = []
    for uid, exp in rows:
        try:
            out.append((int(uid), datetime.fromisoformat(exp).astimezone(timezone.utc)))
        except Exception:
            out.append((int(uid), None))
    return out


# ================== Helpers ==================
def _clean(s: str) -> str:
    s = (s or "").strip()
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s

def _is_admin(uid: int) -> bool:
    return ADMIN_ID is not None and uid == ADMIN_ID

def _act(v: str) -> str:
    v = (v or "").upper().strip()
    if v == "BUY":
        return "🟢 BUY"
    if v == "SELL":
        return "🔴 SELL"
    return "🟡 WAIT"

def _g(d: Dict[str, Any], k: str, fb: str) -> str:
    x = d.get(k)
    x = "" if x is None else str(x).strip()
    return x if x else fb

def _prob(d: Dict[str, Any], fb: str = "--") -> str:
    try:
        p = int(float(d.get("probability", 0)))
        p = max(0, min(100, p))
        return f"{p}%"
    except Exception:
        return fb

def _short_warning_ar() -> str:
    return "⚠️ تنبيه: تعليمي فقط | المخاطرة 1–2%"

def _short_warning_en() -> str:
    return "⚠️ Warning: Educational only | Risk 1–2%"


# ================== Compact Formatter (Simple + Smooth) ==================
def format_message(ar: Dict[str, Any], en: Dict[str, Any]) -> str:
    # Arabic block
    ar_action = _act(ar.get("action"))
    ar_symbol = _g(ar, "symbol", "غير واضح")
    ar_tf = _g(ar, "timeframe", "؟")
    ar_trend = _g(ar, "trend", "غير واضح")
    ar_conf = _g(ar, "confidence", "؟")
    ar_prob = _prob(ar, "--")

    ar_entry = _g(ar, "entry", "غير واضح")
    ar_sl = _g(ar, "sl", "غير واضح")
    ar_tp1 = _g(ar, "tp1", "غير واضح")
    ar_tp2 = _g(ar, "tp2", "غير واضح")
    ar_tp3 = _g(ar, "tp3", "غير واضح")

    ar_reason = _g(ar, "reason", "غير واضح")
    ar_wait = _g(ar, "wait_reason", "غير واضح")

    # English block
    en_action = _act(en.get("action"))
    en_symbol = _g(en, "symbol", "Not clear")
    en_tf = _g(en, "timeframe", "?")
    en_trend = _g(en, "trend", "Not clear")
    en_conf = _g(en, "confidence", "?")
    en_prob = _prob(en, "--")

    en_entry = _g(en, "entry", "Not clear")
    en_sl = _g(en, "sl", "Not clear")
    en_tp1 = _g(en, "tp1", "Not clear")
    en_tp2 = _g(en, "tp2", "Not clear")
    en_tp3 = _g(en, "tp3", "Not clear")

    en_reason = _g(en, "reason", "Not clear")
    en_wait = _g(en, "wait_reason", "Not clear")

    lines = []
    lines.append("🤖 Trading AI")
    lines.append(f"{ar_action} | {ar_symbol} {ar_tf} | {ar_trend} | {ar_conf} {ar_prob}")

    if (ar.get("action") or "").upper().strip() == "WAIT":
        lines.append(f"⏳ السبب: {ar_wait}")
        lines.append(f"🧠 {ar_reason}")
    else:
        lines.append(f"🎯 دخول: {ar_entry}")
        lines.append(f"🛑 SL: {ar_sl}")
        lines.append(f"✅ TP1: {ar_tp1} | ✅ TP2: {ar_tp2} | ✅ TP3: {ar_tp3}")
        lines.append(f"🧠 {ar_reason}")

    lines.append(_short_warning_ar())
    lines.append("—" * 18)

    lines.append(f"{en_action} | {en_symbol} {en_tf} | {en_trend} | {en_conf} {en_prob}")

    if (en.get("action") or "").upper().strip() == "WAIT":
        lines.append(f"⏳ Reason: {en_wait}")
        lines.append(f"🧠 {en_reason}")
    else:
        lines.append(f"🎯 Entry: {en_entry}")
        lines.append(f"🛑 SL: {en_sl}")
        lines.append(f"✅ TP1: {en_tp1} | ✅ TP2: {en_tp2} | ✅ TP3: {en_tp3}")
        lines.append(f"🧠 {en_reason}")

    lines.append(_short_warning_en())
    return _clean("\n".join(lines))


# ================== Robust JSON extraction ==================
def _extract_json_object(text: str) -> Dict[str, Any]:
    text = (text or "").strip()
    if not text:
        raise ValueError("Empty response")

    try:
        return json.loads(text)
    except Exception:
        pass

    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        chunk = text[start:end + 1]
        return json.loads(chunk)

    raise ValueError("No JSON object found")


# ================== AI PROMPT (Balanced: fewer WAIT, still safe) ==================
IMAGE_PROMPT = """
You are a trading signal generator (scalping-friendly). Output Arabic+English JSON ONLY.

Goal:
- Keep output short and practical.
- Provide Entry, SL, TP1, TP2, TP3 whenever readable.
- If numbers are not readable, set them to "Not clear/غير واضح" (NEVER invent prices).

Decision Rule (balanced):
- Choose BUY or SELL if:
  A) probability >= 60 and you have at least 2 confirmations, OR
  B) probability >= 70 with 1 strong confirmation.
- Otherwise choose WAIT.
- Do NOT require RSI/Stoch to exist. If they are not visible, set them to "Not shown/غير موجود" and continue analysis.
- Avoid excessive WAIT. If trend + EMA + structure is clear, you may decide BUY/SELL even if candle signal is not perfect.

Confirmations (count them):
1) Trend/structure direction (higher highs/lows or lower highs/lows, or clear range).
2) EMA alignment if visible (price above/below EMA20 & EMA50; or cross).
3) Candle clue if visible (engulfing/pin/doji/inside/breakout).
4) RSI/Stoch RSI if visible (overbought/oversold + turning or momentum).

TP logic:
- TP1 = nearest realistic objective
- TP2 = next objective
- TP3 = extended objective
If not readable, set Not clear.

Keep reason max 2 short lines.
WAIT must include wait_reason.

Output VALID JSON ONLY in this schema:
{
  "ar":{
    "symbol":"...", "timeframe":"...", "trend":"Bullish/Bearish/Sideways",
    "action":"BUY/SELL/WAIT",
    "probability":0, "confidence":"High/Medium/Low",
    "entry":"...", "sl":"...", "tp1":"...", "tp2":"...", "tp3":"...",
    "reason":"...", "wait_reason":"..."
  },
  "en":{
    "symbol":"...", "timeframe":"...", "trend":"Bullish/Bearish/Sideways",
    "action":"BUY/SELL/WAIT",
    "probability":0, "confidence":"High/Medium/Low",
    "entry":"...", "sl":"...", "tp1":"...", "tp2":"...", "tp3":"...",
    "reason":"...", "wait_reason":"..."
  }
}
"""

def _fallback_analysis(symbol="XAUUSD", tf="M5") -> str:
    ar = {
        "symbol": symbol, "timeframe": tf, "trend": "غير واضح",
        "action": "WAIT", "probability": 55, "confidence": "Low",
        "entry": "غير واضح", "sl": "غير واضح", "tp1": "غير واضح", "tp2": "غير واضح", "tp3": "غير واضح",
        "reason": "تعذر قراءة الأرقام/الإشارة بوضوح من الصورة.",
        "wait_reason": "الصورة غير واضحة أو لا يوجد تأكيد كافي."
    }
    en = {
        "symbol": symbol, "timeframe": tf, "trend": "Not clear",
        "action": "WAIT", "probability": 55, "confidence": "Low",
        "entry": "Not clear", "sl": "Not clear", "tp1": "Not clear", "tp2": "Not clear", "tp3": "Not clear",
        "reason": "Could not read levels clearly from the image.",
        "wait_reason": "Image unclear or not enough confirmation."
    }
    return format_message(ar, en)

def analyze_with_ai(image_bytes: bytes) -> str:
    b64 = base64.b64encode(bytes(image_bytes)).decode("utf-8")

    last_err = None
    for attempt in range(2):
        try:
            resp = client.responses.create(
                model="gpt-4.1-mini",
                input=[{
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": IMAGE_PROMPT},
                        {"type": "input_image", "image_url": f"data:image/jpeg;base64,{b64}"}
                    ]
                }]
            )
            raw = (resp.output_text or "").strip()
            data = _extract_json_object(raw)

            ar = data.get("ar", {}) if isinstance(data, dict) else {}
            en = data.get("en", {}) if isinstance(data, dict) else {}

            # ensure tp3 exists to avoid formatter gaps
            if "tp3" not in ar: ar["tp3"] = "غير واضح"
            if "tp3" not in en: en["tp3"] = "Not clear"

            return format_message(ar, en)

        except Exception as e:
            last_err = e
            logger.warning(f"AI analyze attempt {attempt+1}/2 failed: {e}")

    logger.warning(f"AI analyze fallback used. last_err={last_err}")
    return _fallback_analysis()

def generate_signal_with_ai(symbol: str, timeframe: str) -> str:
    symbol = (symbol or "XAUUSD").upper().strip()
    timeframe = (timeframe or "M5").upper().strip()

    prompt = f"""
You are a trading signal generator (VIP). Output Arabic+English JSON ONLY.

Symbol={symbol}, Timeframe={timeframe}

Rules:
- Choose BUY/SELL if probability >= 60 with 2 confirmations OR >=70 with 1 strong confirmation.
- Otherwise WAIT (with wait_reason).
- Do NOT invent exact prices. If you can't justify numbers, set entry/sl/tp1/tp2/tp3 as Not clear/غير واضح.
- Keep reason max 2 short lines.

Schema:
{{
  "ar":{{"symbol":"{symbol}","timeframe":"{timeframe}","trend":"Bullish/Bearish/Sideways",
         "action":"BUY/SELL/WAIT","probability":0,"confidence":"High/Medium/Low",
         "entry":"...","sl":"...","tp1":"...","tp2":"...","tp3":"...",
         "reason":"...","wait_reason":"..."}},
  "en":{{"symbol":"{symbol}","timeframe":"{timeframe}","trend":"Bullish/Bearish/Sideways",
         "action":"BUY/SELL/WAIT","probability":0,"confidence":"High/Medium/Low",
         "entry":"...","sl":"...","tp1":"...","tp2":"...","tp3":"...",
         "reason":"...","wait_reason":"..."}}
}}
"""
    last_err = None
    for attempt in range(2):
        try:
            resp = client.responses.create(model="gpt-4.1-mini", input=prompt)
            raw = (resp.output_text or "").strip()
            data = _extract_json_object(raw)

            ar = data.get("ar", {}) if isinstance(data, dict) else {}
            en = data.get("en", {}) if isinstance(data, dict) else {}

            if "tp3" not in ar: ar["tp3"] = "غير واضح"
            if "tp3" not in en: en["tp3"] = "Not clear"

            return format_message(ar, en)

        except Exception as e:
            last_err = e
            logger.warning(f"AI signal attempt {attempt+1}/2 failed: {e}")

    logger.warning(f"AI signal fallback used. last_err={last_err}")
    return _fallback_analysis(symbol=symbol, tf=timeframe)


# ================== Commands ==================
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(
        "✅ البوت شغال\n"
        "📸 أرسل صورة الشارت للتحليل (يطلع Entry/SL/TP1/TP2/TP3)\n"
        "🔒 /signal XAUUSD M5 (VIP)\n"
        "ℹ️ رقمك: /myid"
    )

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(
        "📌 الأوامر:\n"
        "• أرسل صورة شارت للتحليل\n"
        "• /signal XAUUSD M5 (VIP)\n"
        "• /myid\n\n"
        "Admin:\n"
        "/vipadd <user_id> <days>\n"
        "/vipremove <user_id>\n"
        "/vipcheck <user_id>\n"
        "/viplist"
    )

async def myid_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    exp = get_vip_expiry(uid)
    if exp and is_vip(uid):
        exp_str = exp.strftime("%Y-%m-%d %H:%M UTC")
        vip_line = f"\n✅ VIP Active until: {exp_str}\n✅ VIP فعال حتى: {exp_str}"
    else:
        vip_line = "\n🔒 VIP: غير مفعل\n🔒 VIP: Not active"

    admin_hint = ""
    if ADMIN_ID is None:
        admin_hint = (
            "\n\n⚠️ Admin غير مفعّل بعد.\n"
            "ضع هذا الرقم في Render كـ ADMIN_USER_ID ثم Redeploy."
        )

    await update.effective_message.reply_text(
        f"🆔 Your Telegram ID: {uid}\n"
        f"🆔 رقمك في تيليجرام: {uid}"
        f"{vip_line}{admin_hint}"
    )

async def signal_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id

    if not is_vip(uid) and not _is_admin(uid):
        await update.effective_message.reply_text(
            "🔒 هذا الأمر VIP فقط.\n"
            "أرسل /myid للمشرف.\n\n"
            "🔒 VIP only.\n"
            "Send /myid to admin."
        )
        return

    symbol = context.args[0] if len(context.args) >= 1 else "XAUUSD"
    timeframe = context.args[1] if len(context.args) >= 2 else "M5"

    await update.effective_message.reply_text("⏳ جاري توليد إشارة VIP ...")
    try:
        msg = generate_signal_with_ai(symbol, timeframe)
        await update.effective_message.reply_text(msg)
    except Exception:
        logger.exception("SIGNAL_ERROR")
        await update.effective_message.reply_text(_fallback_analysis(symbol=symbol.upper(), tf=timeframe.upper()))


# ----- Admin VIP management -----
async def vipadd_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not _is_admin(uid):
        await update.effective_message.reply_text("❌ Admin only (فعّل ADMIN_USER_ID أولاً).")
        return

    if len(context.args) < 2:
        await update.effective_message.reply_text(
            "استخدم:\n"
            "/vipadd <user_id> <days>\n"
            "Example:\n"
            "/vipadd 123456789 30"
        )
        return

    user_id_str, days_str = context.args[0], context.args[1]
    if not user_id_str.isdigit() or not days_str.isdigit():
        await update.effective_message.reply_text("❌ تأكد أن user_id و days أرقام.")
        return

    user_id = int(user_id_str)
    days = int(days_str)
    expires = set_vip(user_id, days)
    exp_str = expires.strftime("%Y-%m-%d %H:%M UTC")
    await update.effective_message.reply_text(
        f"✅ تم تفعيل VIP للمستخدم {user_id} لمدة {days} يوم.\n"
        f"ينتهي: {exp_str}"
    )

async def vipremove_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not _is_admin(uid):
        await update.effective_message.reply_text("❌ Admin only (فعّل ADMIN_USER_ID أولاً).")
        return
    if len(context.args) < 1 or not context.args[0].isdigit():
        await update.effective_message.reply_text("استخدم: /vipremove <user_id>")
        return
    user_id = int(context.args[0])
    remove_vip(user_id)
    await update.effective_message.reply_text(f"✅ تم حذف VIP للمستخدم {user_id}")

async def vipcheck_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not _is_admin(uid):
        await update.effective_message.reply_text("❌ Admin only (فعّل ADMIN_USER_ID أولاً).")
        return
    if len(context.args) < 1 or not context.args[0].isdigit():
        await update.effective_message.reply_text("استخدم: /vipcheck <user_id>")
        return
    user_id = int(context.args[0])
    exp = get_vip_expiry(user_id)
    if exp and datetime.now(timezone.utc) < exp:
        await update.effective_message.reply_text(
            f"✅ VIP فعال للمستخدم {user_id}\n"
            f"ينتهي: {exp.strftime('%Y-%m-%d %H:%M UTC')}"
        )
    else:
        await update.effective_message.reply_text(f"🔒 VIP غير فعال للمستخدم {user_id}")

async def viplist_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not _is_admin(uid):
        await update.effective_message.reply_text("❌ Admin only (فعّل ADMIN_USER_ID أولاً).")
        return
    rows = list_vips(limit=50)
    if not rows:
        await update.effective_message.reply_text("لا يوجد VIP حالياً.")
        return
    lines = ["📌 VIP List (Top 50):"]
    now = datetime.now(timezone.utc)
    for u, exp in rows:
        if exp:
            status = "ACTIVE" if now < exp else "EXPIRED"
            lines.append(f"- {u} | {status} | {exp.strftime('%Y-%m-%d %H:%M UTC')}")
        else:
            lines.append(f"- {u} | (bad date)")
    await update.effective_message.reply_text("\n".join(lines))


# ================== Photo Handler ==================
async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    await msg.reply_text("✅ وصلتني الصورة 📸\n⏳ جاري التحليل...")

    try:
        photo = msg.photo[-1]
        tg_file = await context.bot.get_file(photo.file_id)
        image_bytes = await tg_file.download_as_bytearray()

        analysis = analyze_with_ai(image_bytes)
        await msg.reply_text(analysis)

    except Exception:
        logger.exception("PHOTO_HANDLER_ERROR")
        await msg.reply_text(_fallback_analysis())


async def ignore_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    return


# ================== Run ==================
def main():
    if not TOKEN:
        raise RuntimeError("❌ BOT_TOKEN غير موجود في Render → Environment.")
    if not OPENAI_API_KEY:
        raise RuntimeError("❌ OPENAI_API_KEY غير موجود في Render → Environment.")

    if ADMIN_ID is None:
        logger.warning("⚠️ ADMIN_USER_ID not set yet. Running limited admin mode. Use /myid to get your ID.")

    db_init()

    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("myid", myid_cmd))
    app.add_handler(CommandHandler("signal", signal_cmd))

    app.add_handler(CommandHandler("vipadd", vipadd_cmd))
    app.add_handler(CommandHandler("vipremove", vipremove_cmd))
    app.add_handler(CommandHandler("vipcheck", vipcheck_cmd))
    app.add_handler(CommandHandler("viplist", viplist_cmd))

    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, ignore_text))

    logger.info("🤖 Trading AI Bot is running...")
    app.run_polling()

if __name__ == "__main__":
    main()
