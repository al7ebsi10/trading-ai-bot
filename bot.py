import os
import json
import time
import base64
import sqlite3
import logging
import asyncio
from typing import Optional, Dict, Any, Tuple, List

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

# Vision-capable model
DEFAULT_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini").strip()

DB_PATH = os.getenv("DB_PATH", "vip.db")

# Modes: GOLD / ALL
DEFAULT_MODE = os.getenv("DEFAULT_MODE", "ALL").strip().upper()

# Thresholds (min confidence to allow BUY/SELL)
THRESH_ALL_LITE = int(os.getenv("THRESH_ALL_LITE", "60"))
THRESH_ALL_PRO  = int(os.getenv("THRESH_ALL_PRO",  "65"))
THRESH_ALL_VIP  = int(os.getenv("THRESH_ALL_VIP",  "70"))

THRESH_GOLD_LITE = int(os.getenv("THRESH_GOLD_LITE", "65"))
THRESH_GOLD_PRO  = int(os.getenv("THRESH_GOLD_PRO",  "70"))
THRESH_GOLD_VIP  = int(os.getenv("THRESH_GOLD_VIP",  "75"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

DISCLAIMER_TEXT = "⚠️ Risk: 1–2% | Educational only"

PLANS_TEXT = """\
💎 Trading AI – Plans

$49  - GOLD VIP  (XAUUSD only, M5/M15, stricter)
$99  - VIP ALL   (All pairs & timeframes)
$119 - VIP PRO   (All pairs & timeframes + extra clarity + priority style)

To activate VIP, contact admin.
"""

HELP_TEXT = """\
🤖 Trading AI Bot

Commands:
- /start
- /plans
- /status

Admin:
- /mode gold | /mode all
- /setplan <user_id> <plan> <days>
  plans: LITE, PRO, VIP_GOLD, VIP_ALL, VIP_PRO
- /remove <user_id>

Usage:
- Send a chart image (candles only is OK).
Output: BUY/SELL/WAIT + Entry Zone + SL + TP(3) + Confidence
WAIT includes clear triggers (conditions to enter).
"""


# =========================
# DB
# =========================
def db_conn():
    return sqlite3.connect(DB_PATH)

def init_db():
    with db_conn() as con:
        cur = con.cursor()
        cur.execute("""
        CREATE TABLE IF NOT EXISTS vip_users(
            user_id INTEGER PRIMARY KEY,
            expires_at INTEGER NOT NULL,
            plan TEXT NOT NULL
        )
        """)
        cur.execute("""
        CREATE TABLE IF NOT EXISTS settings(
            k TEXT PRIMARY KEY,
            v TEXT NOT NULL
        )
        """)
        con.commit()

def migrate_db():
    """
    If old DB exists with vip_users(user_id, expires_at) only,
    add column plan safely.
    """
    try:
        with db_conn() as con:
            cur = con.cursor()
            cur.execute("PRAGMA table_info(vip_users)")
            cols = [r[1] for r in cur.fetchall()]  # name is index 1
            if "plan" not in cols:
                cur.execute('ALTER TABLE vip_users ADD COLUMN plan TEXT DEFAULT "VIP_ALL"')
                con.commit()
                logging.info("DB migrated: added column vip_users.plan")
    except Exception as e:
        logging.warning(f"DB migrate skipped/failed: {e}")

def set_setting(k: str, v: str):
    with db_conn() as con:
        cur = con.cursor()
        cur.execute(
            "INSERT INTO settings(k,v) VALUES(?,?) "
            "ON CONFLICT(k) DO UPDATE SET v=excluded.v",
            (k, v)
        )
        con.commit()

def get_setting(k: str, default: str = "") -> str:
    with db_conn() as con:
        cur = con.cursor()
        cur.execute("SELECT v FROM settings WHERE k=?", (k,))
        row = cur.fetchone()
        return row[0] if row else default

def add_user_plan(user_id: int, days: int, plan: str):
    expires_at = int(time.time()) + int(days) * 86400
    plan = plan.strip().upper()
    with db_conn() as con:
        cur = con.cursor()
        cur.execute(
            "INSERT INTO vip_users(user_id, expires_at, plan) VALUES(?,?,?) "
            "ON CONFLICT(user_id) DO UPDATE SET expires_at=excluded.expires_at, plan=excluded.plan",
            (user_id, expires_at, plan)
        )
        con.commit()

def remove_user(user_id: int):
    with db_conn() as con:
        cur = con.cursor()
        cur.execute("DELETE FROM vip_users WHERE user_id=?", (user_id,))
        con.commit()

def user_row(user_id: int) -> Optional[Tuple[int, int, str]]:
    with db_conn() as con:
        cur = con.cursor()
        cur.execute("SELECT user_id, expires_at, plan FROM vip_users WHERE user_id=?", (user_id,))
        row = cur.fetchone()
        if not row:
            return None
        return int(row[0]), int(row[1]), str(row[2])

def is_active(user_id: int) -> bool:
    r = user_row(user_id)
    return bool(r and r[1] > int(time.time()))

def days_left(user_id: int) -> int:
    r = user_row(user_id)
    if not r:
        return 0
    left = r[1] - int(time.time())
    return max(0, left // 86400)

def user_plan(user_id: int) -> str:
    r = user_row(user_id)
    if not r or r[1] <= int(time.time()):
        return "FREE"
    return r[2].upper()

def current_mode() -> str:
    return (get_setting("MODE", DEFAULT_MODE) or "ALL").upper()

def set_mode(m: str):
    set_setting("MODE", m.upper())


# =========================
# Tier rules
# =========================
def has_access(plan: str) -> bool:
    return plan in ("LITE", "PRO", "VIP_GOLD", "VIP_ALL", "VIP_PRO")

def is_gold_plan(plan: str) -> bool:
    return plan == "VIP_GOLD"

def is_pro_like(plan: str) -> bool:
    return plan in ("PRO", "VIP_PRO")

def required_threshold(plan: str) -> int:
    mode = current_mode()
    if mode == "GOLD":
        if plan == "VIP_GOLD":
            return THRESH_GOLD_VIP
        if plan in ("VIP_ALL", "VIP_PRO", "PRO"):
            return THRESH_GOLD_PRO
        return THRESH_GOLD_LITE
    else:
        if plan == "VIP_PRO":
            return THRESH_ALL_VIP
        if plan in ("VIP_ALL", "PRO"):
            return THRESH_ALL_PRO
        return THRESH_ALL_LITE

def confidence_level(conf: int) -> str:
    if conf >= 80:
        return "High"
    if conf >= 65:
        return "Medium"
    return "Low"

def mode_constraints_prompt(plan: str) -> str:
    m = current_mode()
    if m == "GOLD" or is_gold_plan(plan):
        return (
            "Mode is GOLD ONLY:\n"
            "- Focus on XAUUSD (Gold).\n"
            "- Prefer M5 or M15.\n"
            "- Be strict: if not clear, output WAIT with triggers.\n"
        )
    return (
        "Mode is ALL:\n"
        "- Any symbol/timeframe allowed.\n"
        "- Still prefer accuracy over frequent signals.\n"
    )


# =========================
# Confidence engine
# =========================
def compute_confidence_from_subscores(sub: Dict[str, Any]) -> int:
    def clamp(x, lo, hi):
        try:
            v = int(float(x))
        except Exception:
            v = 0
        return max(lo, min(hi, v))

    trend  = clamp(sub.get("trend"),  0, 25)
    rsi    = clamp(sub.get("rsi"),    0, 20)
    stoch  = clamp(sub.get("stoch"),  0, 20)
    candle = clamp(sub.get("candle"), 0, 20)
    clean  = clamp(sub.get("clean"),  0, 15)

    score = trend + rsi + stoch + candle + clean
    return max(0, min(95, score))


# =========================
# Format output
# =========================
def fmt_signal(res: Dict[str, Any], plan: str) -> str:
    action = (res.get("action") or "WAIT").upper()
    pair   = (res.get("pair") or "XAUUSD").upper()
    tf     = (res.get("timeframe") or "M5").upper()
    conf   = int(res.get("confidence") or 0)
    lvl    = confidence_level(conf)

    reasons  = res.get("reasons") or []
    triggers = res.get("triggers") or []

    # VIP GOLD style
    if current_mode() == "GOLD" or is_gold_plan(plan):
        header = f"👑 VIP GOLD | {pair} | {tf}"
        if action == "WAIT":
            trig_txt = "\n".join([f"• {t}" for t in triggers[:5]]) if triggers else "• Wait for breakout + retest"
            return f"""\
{header}

Status: 🟡 WAIT
Confidence: {conf}% ({lvl})

Trigger:
{trig_txt}

{DISCLAIMER_TEXT}""".strip()

        ezl, ezh = res.get("entry_zone_low"), res.get("entry_zone_high")
        sl = res.get("sl")
        tps = res.get("tps") or []
        tp_txt = " / ".join([str(x) for x in tps[:3]])
        return f"""\
{header}

Signal: {'🟢 BUY' if action=='BUY' else '🔴 SELL'}
Confidence: {conf}% ({lvl})

Entry: {ezl} – {ezh}
SL: {sl}
TP: {tp_txt}

Mode: Scalping
{DISCLAIMER_TEXT}""".strip()

    # ALL / PRO style
    if action == "WAIT":
        trig_txt = "\n".join([f"• {t}" for t in triggers[:5]]) if triggers else "• Wait for breakout + retest"
        extra = ""
        if is_pro_like(plan):
            st_txt = "\n".join([f"• {r}" for r in reasons[:4]]) if reasons else "• No strong confirmation"
            extra = f"\n\n📊 State:\n{st_txt}"
        return f"""\
🟡 WAIT | {pair} {tf}
Confidence: {conf}% ({lvl})

⏳ No trade now — market not ready

✅ Wait for:
{trig_txt}{extra}

{DISCLAIMER_TEXT}""".strip()

    ezl, ezh = res.get("entry_zone_low"), res.get("entry_zone_high")
    sl = res.get("sl")
    tps = res.get("tps") or []
    reasons_txt = "\n".join([f"• {r}" for r in reasons[:5]]) if reasons else "• No clear reasons"
    note = (res.get("note") or "").strip()
    note_line = f"\n🧠 Note: {note[:120]}" if (note and is_pro_like(plan)) else ""

    return f"""\
{'🟢 BUY' if action=='BUY' else '🔴 SELL'} | {pair} {tf}
Confidence: {conf}% ({lvl})

📍 Entry Zone: {ezl} – {ezh}
🛑 SL: {sl}
🎯 TP: {tps[0]} / {tps[1]} / {tps[2]}

📊 Reasons:
{reasons_txt}{note_line}

{DISCLAIMER_TEXT}""".strip()


# =========================
# OpenAI Vision Call (blocking)
# =========================
def call_openai_vision_blocking(image_bytes: bytes, plan: str) -> Tuple[Optional[Dict[str, Any]], str]:
    if not OPENAI_API_KEY:
        return None, "Missing OPENAI_API_KEY"

    threshold = required_threshold(plan)

    system = (
        "You are a professional trading signal assistant.\n"
        "Return ONLY valid JSON. No markdown. No extra text.\n"
        "NEVER output phrases like 'Not clear' or 'Insufficient data'.\n"
        "If RSI/Stoch are not visible, still analyze using price action, structure, levels and candle confirmation.\n"
        "Always base decision on at least TWO of: (trend/structure, key levels, candle confirmation, volatility/cleanliness). "
        "RSI/Stoch are optional enhancers.\n\n"
        "Schema:\n"
        "{"
        "\"action\":\"BUY|SELL|WAIT\","
        "\"pair\":\"string\","
        "\"timeframe\":\"string\","
        "\"bias\":\"Bullish|Bearish|Sideways\","
        "\"subscores\":{"
            "\"trend\":0-25,"
            "\"rsi\":0-20,"
            "\"stoch\":0-20,"
            "\"candle\":0-20,"
            "\"clean\":0-15"
        "},"
        "\"entry_zone_low\":number|null,"
        "\"entry_zone_high\":number|null,"
        "\"sl\":number|null,"
        "\"tps\":[number,number,number] | null,"
        "\"reasons\":[\"string\",...],"
        "\"triggers\":[\"string\",...],"
        "\"note\":\"short string\""
        "}\n\n"
        "Rules:\n"
        "- If action=WAIT: entry_zone_low/high, sl, tps must be null; triggers must have 3-5 concise bullets.\n"
        "- If action=BUY/SELL: MUST provide entry_zone_low/high, sl, and exactly 3 take-profits in tps.\n"
        "- Reasons: 3-6 bullets, concise and practical.\n"
        "- Keep note <= 120 chars.\n"
        "- Prefer accuracy over frequency.\n"
    )

    user_prompt = (
        f"{mode_constraints_prompt(plan)}\n"
        f"Plan: {plan}\n"
        f"Minimum confidence threshold for BUY/SELL: {threshold}\n"
        "Analyze the chart screenshot.\n"
        "If not ready, output WAIT with clear triggers (conditions to enter).\n"
        "Output JSON only.\n"
    )

    b64 = base64.b64encode(image_bytes).decode("utf-8")

    url = "https://api.openai.com/v1/responses"
    headers = {"Authorization": f"Bearer {OPENAI_API_KEY}", "Content-Type": "application/json"}

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
        r = requests.post(url, headers=headers, json=payload, timeout=30)
        raw = r.text
        if r.status_code != 200:
            return None, raw

        data = r.json()

        # extract text in multiple ways
        text = ""
        try:
            out = data.get("output", [])
            if out and out[0].get("content"):
                text = out[0]["content"][0].get("text", "") or ""
        except Exception:
            pass

        if not text:
            text = data.get("output_text", "") or ""

        if not text:
            # deeper fallback: search any output_text block
            try:
                out = data.get("output", [])
                for item in out:
                    for c in item.get("content", []):
                        if c.get("type") in ("output_text", "text") and c.get("text"):
                            text = c["text"]
                            break
                    if text:
                        break
            except Exception:
                pass

        text = (text or "").strip()
        if not text:
            return None, raw

        # parse strict json
        try:
            return json.loads(text), text
        except json.JSONDecodeError:
            s = text.find("{")
            e = text.rfind("}")
            if s != -1 and e != -1 and e > s:
                candidate = text[s:e+1]
                try:
                    return json.loads(candidate), text
                except Exception:
                    return None, text
            return None, text

    except Exception as e:
        return None, repr(e)


# =========================
# Sanitizer
# =========================
def sanitize_result(j: Dict[str, Any], plan: str) -> Dict[str, Any]:
    action = (j.get("action") or "WAIT").upper().strip()
    pair = (j.get("pair") or "XAUUSD").upper().strip()
    tf = (j.get("timeframe") or "M5").upper().strip()
    bias = (j.get("bias") or "Sideways")

    subs = j.get("subscores") or {}
    conf = compute_confidence_from_subscores(subs)
    threshold = required_threshold(plan)

    if action in ("BUY", "SELL") and conf < threshold:
        action = "WAIT"

    # GOLD enforcement if mode GOLD
    if current_mode() == "GOLD" or is_gold_plan(plan):
        pair = "XAUUSD"
        if tf not in ("M5", "M15"):
            tf = "M5"

    def num(x):
        try:
            return round(float(x), 2)
        except Exception:
            return None

    ezl = num(j.get("entry_zone_low"))
    ezh = num(j.get("entry_zone_high"))
    sl  = num(j.get("sl"))

    tps = j.get("tps")
    if isinstance(tps, list) and len(tps) >= 3:
        tps = [num(tps[0]), num(tps[1]), num(tps[2])]
    else:
        tps = None

    reasons = j.get("reasons") or []
    triggers = j.get("triggers") or []
    note = (j.get("note") or "").strip()[:120]

    reasons = [str(x).strip()[:90] for x in reasons if str(x).strip()]
    triggers = [str(x).strip()[:90] for x in triggers if str(x).strip()]

    # WAIT: no prices, must have triggers
    if action == "WAIT":
        ezl = ezh = sl = None
        tps = None
        if len(triggers) < 3:
            triggers = [
                "BUY if RSI > 50 + bullish candle close",
                "SELL if RSI < 45 + bearish candle close",
                "Breakout from range + retest"
            ]

    # BUY/SELL must have all numbers
    if action in ("BUY", "SELL"):
        if any(v is None for v in [ezl, ezh, sl]) or not tps or any(x is None for x in tps):
            action = "WAIT"
            ezl = ezh = sl = None
            tps = None
            if len(triggers) < 3:
                triggers = [
                    "Wait for clean breakout + retest",
                    "Confirm candle close in signal direction",
                    "Avoid sideways/chop zone"
                ]

    if is_pro_like(plan) and not reasons:
        reasons = ["Structure not fully confirmed", "Waiting for candle confirmation", "Range conditions"]

    return {
        "action": action,
        "pair": pair,
        "timeframe": tf,
        "bias": str(bias).title(),
        "confidence": max(0, min(100, int(conf))),
        "subscores": subs,
        "entry_zone_low": ezl,
        "entry_zone_high": ezh,
        "sl": sl,
        "tps": tps,
        "reasons": reasons,
        "triggers": triggers,
        "note": note
    }


# =========================
# Telegram helpers
# =========================
async def notify_admin(context: ContextTypes.DEFAULT_TYPE, text: str):
    if not ADMIN_USER_ID:
        return
    try:
        await context.bot.send_message(chat_id=ADMIN_USER_ID, text=text[:3500])
    except Exception:
        pass


# =========================
# Handlers
# =========================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🤖 Trading AI\nSend a chart image to get a clean signal.\nType /plans for pricing.\nType /help for commands.")

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(HELP_TEXT)

async def plans(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(PLANS_TEXT)

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    plan = user_plan(uid)
    if is_active(uid) and has_access(plan):
        await update.message.reply_text(f"✅ Active\nPlan: {plan}\nDays left: {days_left(uid)}\nMode: {current_mode()}")
    else:
        await update.message.reply_text(f"🔒 Inactive\nMode: {current_mode()}\n\nType /plans for pricing.")

async def ping(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("✅ Alive")

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

    set_mode(m)
    await update.message.reply_text(f"✅ Mode updated: {m}")

async def setplan_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid != ADMIN_USER_ID:
        await update.message.reply_text("❌ Admin only.")
        return
    if len(context.args) < 3:
        await update.message.reply_text("Usage: /setplan <user_id> <plan> <days>\nplans: LITE, PRO, VIP_GOLD, VIP_ALL, VIP_PRO")
        return
    try:
        user_id = int(context.args[0])
        plan = str(context.args[1]).upper().strip()
        days = int(context.args[2])
        if plan not in ("LITE", "PRO", "VIP_GOLD", "VIP_ALL", "VIP_PRO"):
            await update.message.reply_text("❌ Invalid plan.")
            return
        add_user_plan(user_id, days, plan)
        await update.message.reply_text(f"✅ Set {user_id} plan={plan} for {days} days")
    except Exception as e:
        await update.message.reply_text("❌ Invalid arguments.")
        await notify_admin(context, f"setplan error: {repr(e)}")

async def remove_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid != ADMIN_USER_ID:
        await update.message.reply_text("❌ Admin only.")
        return
    if len(context.args) < 1:
        await update.message.reply_text("Usage: /remove <user_id>")
        return
    try:
        user_id = int(context.args[0])
        remove_user(user_id)
        await update.message.reply_text(f"✅ Removed user {user_id}")
    except Exception as e:
        await update.message.reply_text("❌ Invalid user_id.")
        await notify_admin(context, f"remove error: {repr(e)}")


# =========================
# Core analysis runner (async safe)
# =========================
async def analyze_and_reply(update: Update, context: ContextTypes.DEFAULT_TYPE, image_bytes: bytes):
    uid = update.effective_user.id
    plan = user_plan(uid)

    if not (is_active(uid) and has_access(plan)):
        await update.message.reply_text("🔒 VIP Feature\nThis bot provides VIP trading signals.\nType /plans to subscribe.")
        return

    await update.message.reply_text("📸 Received. Analyzing...")

    loop = asyncio.get_running_loop()

    def _do_call():
        return call_openai_vision_blocking(image_bytes, plan)

    try:
        # Important: run blocking request in a thread + timeout
        j, raw = await asyncio.wait_for(loop.run_in_executor(None, _do_call), timeout=55)
    except asyncio.TimeoutError:
        await update.message.reply_text("⏳ Timeout. Please resend a clearer chart (zoom candles).")
        await notify_admin(context, "OpenAI timeout while analyzing image.")
        return
    except Exception as e:
        await update.message.reply_text("❌ Error while analyzing. Try again.")
        await notify_admin(context, f"Analyze exception: {repr(e)}")
        return

    if not j:
        await update.message.reply_text("❌ Analysis failed. Try again with a clearer chart screenshot (zoom candles).")
        await notify_admin(context, f"OpenAI raw/error:\n{str(raw)[:3500]}")
        return

    res = sanitize_result(j, plan)
    msg = fmt_signal(res, plan)
    await update.message.reply_text(msg)


# =========================
# Photo / Document handlers
# =========================
async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        photo = update.message.photo[-1]
        file = await photo.get_file()
        image_bytes = await file.download_as_bytearray()
        await analyze_and_reply(update, context, bytes(image_bytes))
    except Exception as e:
        logging.exception(e)
        await update.message.reply_text("❌ Error reading photo. Please try again.")
        await notify_admin(context, f"handle_photo error: {repr(e)}")

async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        doc = update.message.document
        if not doc:
            await update.message.reply_text("📎 Please send a chart image (jpg/png).")
            return

        mime = (doc.mime_type or "")
        if not mime.startswith("image/"):
            await update.message.reply_text("📎 Please send an IMAGE file (jpg/png).")
            return

        file = await doc.get_file()
        image_bytes = await file.download_as_bytearray()
        await analyze_and_reply(update, context, bytes(image_bytes))
    except Exception as e:
        logging.exception(e)
        await update.message.reply_text("❌ Error reading image file. Please try again.")
        await notify_admin(context, f"handle_document error: {repr(e)}")


# =========================
# MAIN
# =========================
def main():
    if not BOT_TOKEN:
        raise RuntimeError("Missing BOT_TOKEN env var (BOT_TOKEN).")

    init_db()
    migrate_db()

    if not get_setting("MODE"):
        set_mode(DEFAULT_MODE)

    logging.info("Bot starting...")
    logging.info(f"MODE={current_mode()} | MODEL={DEFAULT_MODEL} | DB={DB_PATH}")

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    # basic
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("plans", plans))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("ping", ping))

    # admin
    app.add_handler(CommandHandler("mode", mode_cmd))
    app.add_handler(CommandHandler("setplan", setplan_cmd))
    app.add_handler(CommandHandler("remove", remove_cmd))

    # images: BOTH photo + document image (send as file)
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.Document.IMAGE, handle_document))

    app.run_polling()


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        import traceback
        print("🔥 BOT CRASHED:", repr(e))
        traceback.print_exc()
        raise
