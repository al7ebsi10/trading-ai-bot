import os
import json
import time
import sqlite3
import logging
import base64
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

# OpenAI model (Vision-capable). Keep configurable.
DEFAULT_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini").strip()

# DB
DB_PATH = os.getenv("DB_PATH", "vip.db")

# Default product/mode
DEFAULT_MODE = os.getenv("DEFAULT_MODE", "ALL").strip().upper()  # GOLD or ALL

# Thresholds (min confidence to allow BUY/SELL)
THRESH_ALL_LITE = int(os.getenv("THRESH_ALL_LITE", "60"))
THRESH_ALL_PRO  = int(os.getenv("THRESH_ALL_PRO",  "65"))
THRESH_ALL_VIP  = int(os.getenv("THRESH_ALL_VIP",  "70"))

THRESH_GOLD_LITE = int(os.getenv("THRESH_GOLD_LITE", "65"))
THRESH_GOLD_PRO  = int(os.getenv("THRESH_GOLD_PRO",  "70"))
THRESH_GOLD_VIP  = int(os.getenv("THRESH_GOLD_VIP",  "75"))

# Strictness (affects "WAIT" triggers density + demands)
STRICT_GOLD = True

logging.basicConfig(level=logging.INFO)


# =========================
# PRODUCT TIERS
# =========================
# Tiers:
# - LITE: basic clean signal
# - PRO: more reasons + clearer triggers
# - VIP: VIP access gate (can be GOLD or ALL)
#
# We store user's plan in DB: LITE / PRO / VIP_GOLD / VIP_ALL / VIP_PRO
# VIP_PRO behaves like VIP_ALL with pro formatting (more details) + higher strictness optional

PLANS_TEXT = """\
💎 Trading AI – Plans

$49  - GOLD VIP  (XAUUSD only, M5/M15, stricter)
$99  - VIP ALL   (All pairs & timeframes)
$119 - VIP PRO   (All pairs & timeframes + extra clarity + priority style)

🟦 Lite / Pro are internal tiers you can use for trials.
To activate, contact admin.
"""

DISCLAIMER_TEXT = "⚠️ Risk: 1–2% | Educational only"


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

def set_setting(k: str, v: str):
    with db_conn() as con:
        cur = con.cursor()
        cur.execute(
            "INSERT INTO settings(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v",
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
# TIER RULES
# =========================
def is_gold_plan(plan: str) -> bool:
    return plan in ("VIP_GOLD",)

def is_all_plan(plan: str) -> bool:
    return plan in ("VIP_ALL", "VIP_PRO")

def is_pro_like(plan: str) -> bool:
    return plan in ("PRO", "VIP_PRO")

def has_access(plan: str) -> bool:
    # You can allow trial plans (LITE/PRO) as paid access too if you want:
    return plan in ("LITE", "PRO", "VIP_GOLD", "VIP_ALL", "VIP_PRO")

def required_threshold(plan: str) -> int:
    mode = current_mode()
    if mode == "GOLD":
        if plan in ("VIP_GOLD",):
            return THRESH_GOLD_VIP
        if plan in ("VIP_PRO", "VIP_ALL", "PRO"):
            return THRESH_GOLD_PRO
        return THRESH_GOLD_LITE
    else:
        if plan in ("VIP_PRO",):
            return THRESH_ALL_VIP
        if plan in ("VIP_ALL", "PRO"):
            return THRESH_ALL_PRO
        return THRESH_ALL_LITE

def mode_constraints_prompt(plan: str) -> str:
    mode = current_mode()
    if mode == "GOLD":
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

def confidence_level(conf: int) -> str:
    if conf >= 80: return "High"
    if conf >= 65: return "Medium"
    return "Low"


# =========================
# CONFIDENCE ENGINE
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
# OUTPUT FORMAT (VIP STYLE)
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
            trig_txt = "\n".join([f"• {t}" for t in triggers[:4]]) if triggers else "• Wait for RSI break + candle close"
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

    # VIP ALL / PRO formatting
    if action == "WAIT":
        trig_txt = "\n".join([f"• {t}" for t in triggers[:5]]) if triggers else "• Wait for breakout + retest"
        extra = ""
        if is_pro_like(plan):
            reasons_txt = "\n".join([f"• {r}" for r in reasons[:4]]) if reasons else "• No strong confirmation"
            extra = f"\n\n📊 State:\n{reasons_txt}"
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
# OPENAI VISION CALL
# =========================
def call_openai_vision(image_bytes: bytes, plan: str) -> Tuple[Optional[Dict[str, Any]], str]:
    """
    Returns (json_result_or_none, raw_text)
    Uses OpenAI Responses API.
    """
    if not OPENAI_API_KEY:
        return None, "Missing OPENAI_API_KEY"

    threshold = required_threshold(plan)

    # JSON-only contract (NO "Not clear")
    system = (
        "You are a professional trading signal assistant.\n"
        "Return ONLY valid JSON. No markdown. No extra text.\n"
        "NEVER output phrases like 'Not clear' or 'Insufficient data'.\n"
        "If chart indicators (RSI/Stoch) are not visible, still analyze using price action, structure, levels and candle confirmation.\n"
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
        r = requests.post(url, headers=headers, json=payload, timeout=45)
        raw = r.text
        if r.status_code != 200:
            return None, raw

        data = r.json()
        text = ""

        try:
            out = data.get("output", [])
            if out and out[0].get("content"):
                text = out[0]["content"][0].get("text", "") or ""
        except Exception:
            pass

        if not text:
            text = data.get("output_text", "") or ""

        text = (text or "").strip()
        if not text:
            return None, raw

        # Parse JSON strict
        try:
            j = json.loads(text)
            return j, text
        except json.JSONDecodeError:
            # attempt extract first json object
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
# SANITIZER (No contradictions)
# =========================
def sanitize_result(j: Dict[str, Any], plan: str) -> Dict[str, Any]:
    action = (j.get("action") or "WAIT").upper().strip()
    pair = (j.get("pair") or "XAUUSD").upper().strip()
    tf = (j.get("timeframe") or "M5").upper().strip()
    bias = (j.get("bias") or "Sideways")

    subs = j.get("subscores") or {}
    conf = compute_confidence_from_subscores(subs)

    # Enforce plan + mode constraints
    threshold = required_threshold(plan)
    if action in ("BUY", "SELL") and conf < threshold:
        action = "WAIT"

    # GOLD mode hard preference (marketing & clarity)
    if current_mode() == "GOLD":
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

    # Normalize strings (keep them short & clean)
    reasons = [str(x).strip()[:90] for x in reasons if str(x).strip()]
    triggers = [str(x).strip()[:90] for x in triggers if str(x).strip()]

    # WAIT must have triggers and no prices
    if action == "WAIT":
        ezl = ezh = sl = None
        tps = None
        if len(triggers) < 3:
            triggers = [
                "BUY if RSI > 50 + bullish candle close",
                "SELL if RSI < 45 + bearish candle close",
                "Breakout from range + retest"
            ]

    # BUY/SELL must have prices; else WAIT
    if action in ("BUY", "SELL"):
        if any(v is None for v in [ezl, ezh, sl]) or not tps or any(x is None for x in tps):
            action = "WAIT"
            ezl = ezh = sl = None
            tps = None
            if len(triggers) < 3:
                triggers = [
                    "Wait for clean breakout + retest",
                    "Confirm candle close in signal direction",
                    "Avoid chop/sideways zone"
                ]

    # PRO-like: ensure reasons exist
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
# TELEGRAM COMMANDS
# =========================
HELP_TEXT = """\
🤖 Trading AI Bot (EN)

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
Output: BUY/SELL/WAIT + Entry Zone + SL + TP(3) + Confidence + Triggers on WAIT.
"""

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
        plan = str(context.args[1]).upper()
        days = int(context.args[2])
        if plan not in ("LITE", "PRO", "VIP_GOLD", "VIP_ALL", "VIP_PRO"):
            await update.message.reply_text("❌ Invalid plan.")
            return
        add_user_plan(user_id, days, plan)
        await update.message.reply_text(f"✅ Set {user_id} plan={plan} for {days} days")
    except Exception:
        await update.message.reply_text("❌ Invalid arguments.")

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
    except Exception:
        await update.message.reply_text("❌ Invalid user_id.")


# =========================
# PHOTO HANDLER
# =========================
async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    plan = user_plan(uid)

    # Gate
    if not (is_active(uid) and has_access(plan)):
        await update.message.reply_text("🔒 VIP Feature\nThis bot provides VIP trading signals.\nType /plans to subscribe.")
        return

    await update.message.reply_text("📸 Received. Analyzing...")

    try:
        photo = update.message.photo[-1]
        file = await photo.get_file()
        image_bytes = await file.download_as_bytearray()

        j, raw = call_openai_vision(bytes(image_bytes), plan)
        if not j:
            await update.message.reply_text("❌ Analysis failed. Try again with a clearer chart screenshot (zoom candles).")
            return

        res = sanitize_result(j, plan)

        msg = fmt_signal(res, plan)
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
    if not get_setting("MODE"):
        set_mode(DEFAULT_MODE)

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("plans", plans))
    app.add_handler(CommandHandler("status", status))

    # Admin
    app.add_handler(CommandHandler("mode", mode_cmd))
    app.add_handler(CommandHandler("setplan", setplan_cmd))
    app.add_handler(CommandHandler("remove", remove_cmd))

    # Photos
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))

    app.run_polling()

if __name__ == "__main__":
    main()
