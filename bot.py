import os
import json
import time
import sqlite3
import base64
import logging
import asyncio
from typing import Optional, Dict, Any, Tuple

import requests
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# =========================
# ENV
# =========================
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
ADMIN_USER_ID = int(os.getenv("ADMIN_USER_ID", "0") or "0")

DEFAULT_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini").strip()
DEFAULT_MODE = os.getenv("DEFAULT_MODE", "ALL").strip().upper()  # ALL or GOLD
DB_PATH = os.getenv("DB_PATH", "vip.db")

# Free trial: number of analyses allowed for free users
FREE_TRIAL_LIMIT = int(os.getenv("FREE_TRIAL_LIMIT", "5"))

# Confidence thresholds
THRESH_ALL_LITE = int(os.getenv("THRESH_ALL_LITE", "62"))
THRESH_ALL_PRO = int(os.getenv("THRESH_ALL_PRO", "67"))
THRESH_ALL_VIP = int(os.getenv("THRESH_ALL_VIP", "70"))

THRESH_GOLD_LITE = int(os.getenv("THRESH_GOLD_LITE", "65"))
THRESH_GOLD_PRO = int(os.getenv("THRESH_GOLD_PRO", "70"))
THRESH_GOLD_VIP = int(os.getenv("THRESH_GOLD_VIP", "74"))

logging.basicConfig(level=logging.INFO)


# =========================
# DB
# =========================
def db_conn():
    return sqlite3.connect(DB_PATH)


def init_db():
    with db_conn() as con:
        cur = con.cursor()

        # paid plans
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS plans(
                user_id INTEGER PRIMARY KEY,
                plan TEXT NOT NULL,
                expires_at INTEGER NOT NULL
            )
            """
        )

        # free trial usage
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS free_trials(
                user_id INTEGER PRIMARY KEY,
                used_count INTEGER NOT NULL DEFAULT 0
            )
            """
        )

        # settings
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS settings(
                k TEXT PRIMARY KEY,
                v TEXT NOT NULL
            )
            """
        )

        con.commit()


def set_setting(k: str, v: str):
    with db_conn() as con:
        cur = con.cursor()
        cur.execute(
            "INSERT INTO settings(k,v) VALUES(?,?) "
            "ON CONFLICT(k) DO UPDATE SET v=excluded.v",
            (k, v),
        )
        con.commit()


def get_setting(k: str, default: str = "") -> str:
    with db_conn() as con:
        cur = con.cursor()
        cur.execute("SELECT v FROM settings WHERE k=?", (k,))
        row = cur.fetchone()
        return row[0] if row else default


def current_mode() -> str:
    m = (get_setting("MODE", DEFAULT_MODE) or DEFAULT_MODE).upper()
    return "GOLD" if m == "GOLD" else "ALL"


def set_plan(user_id: int, plan: str, days: int):
    expires_at = int(time.time()) + int(days) * 86400
    with db_conn() as con:
        cur = con.cursor()
        cur.execute(
            "INSERT INTO plans(user_id, plan, expires_at) VALUES(?,?,?) "
            "ON CONFLICT(user_id) DO UPDATE SET plan=excluded.plan, expires_at=excluded.expires_at",
            (user_id, plan, expires_at),
        )
        con.commit()


def get_plan_row(user_id: int) -> Optional[Tuple[str, int]]:
    with db_conn() as con:
        cur = con.cursor()
        cur.execute("SELECT plan, expires_at FROM plans WHERE user_id=?", (user_id,))
        row = cur.fetchone()
        if not row:
            return None
        return str(row[0]), int(row[1])


def user_plan(user_id: int) -> str:
    row = get_plan_row(user_id)
    return row[0] if row else "FREE"


def is_active(user_id: int) -> bool:
    row = get_plan_row(user_id)
    if not row:
        return False
    _, expires_at = row
    return expires_at > int(time.time())


def days_left(user_id: int) -> int:
    row = get_plan_row(user_id)
    if not row:
        return 0
    _, exp = row
    left = exp - int(time.time())
    return max(0, left // 86400)


def has_access(plan: str) -> bool:
    # paid plans that can analyze indefinitely
    return plan in ("LITE", "PRO", "VIP_GOLD", "VIP_ALL", "VIP_PRO")


def free_used(user_id: int) -> int:
    with db_conn() as con:
        cur = con.cursor()
        cur.execute("SELECT used_count FROM free_trials WHERE user_id=?", (user_id,))
        row = cur.fetchone()
        return int(row[0]) if row else 0


def free_remaining(user_id: int) -> int:
    return max(0, FREE_TRIAL_LIMIT - free_used(user_id))


def inc_free_used(user_id: int):
    with db_conn() as con:
        cur = con.cursor()
        cur.execute(
            """
            INSERT INTO free_trials(user_id, used_count) VALUES(?, 1)
            ON CONFLICT(user_id) DO UPDATE SET used_count = used_count + 1
            """,
            (user_id,),
        )
        con.commit()


# =========================
# UI / TEXT
# =========================
PLANS_TEXT = """\
💎 Trading AI – Plans

$49  - VIP_GOLD (XAUUSD, M5/M15)  ✅ High accuracy focus
$99  - VIP_ALL  (All pairs & timeframes)
$119 - VIP_PRO  (VIP_ALL + priority updates)

🎁 Free Trial:
• 5 chart analyses for FREE, then subscription required.

To subscribe, contact admin.
"""

HELP_TEXT = """\
🤖 Trading AI Bot

Commands:
- /start
- /plans
- /status
- /myid

Admin:
- /setplan <user_id> <plan> <days>
  plans: LITE, PRO, VIP_GOLD, VIP_ALL, VIP_PRO
- /mode gold | /mode all

Usage:
Send a chart image (candles only OK).
Bot returns: BUY/SELL/WAIT + Entry/SL/TP1/TP2/TP3 + Confidence.
"""


DISCLAIMER = "⚠️ Educational only | Risk 1–2%"


# =========================
# Thresholds
# =========================
def required_threshold(plan: str) -> int:
    mode = current_mode()

    # Free trial: keep quality decent (you can change this)
    if plan == "FREE_TRIAL":
        return 70 if mode == "ALL" else 74

    if mode == "GOLD":
        if plan == "VIP_GOLD":
            return THRESH_GOLD_VIP
        if plan in ("VIP_ALL", "VIP_PRO", "PRO"):
            return THRESH_GOLD_PRO
        return THRESH_GOLD_LITE
    else:
        if plan in ("VIP_ALL", "VIP_PRO"):
            return THRESH_ALL_VIP
        if plan == "PRO":
            return THRESH_ALL_PRO
        return THRESH_ALL_LITE


def mode_constraints_prompt() -> str:
    if current_mode() == "GOLD":
        return (
            "Mode: GOLD\n"
            "- Focus XAUUSD (Gold) primarily.\n"
            "- Prefer M5/M15.\n"
            "- Be strict: avoid signals unless clear.\n"
        )
    return (
        "Mode: ALL\n"
        "- Any symbol/timeframe.\n"
        "- Still avoid low-quality signals.\n"
    )


# =========================
# Formatting
# =========================
def fmt_signal(res: Dict[str, Any], plan: str, trial_remaining: Optional[int] = None) -> str:
    action = (res.get("action") or "WAIT").upper()
    pair = (res.get("pair") or "N/A").upper()
    tf = (res.get("timeframe") or "N/A").upper()
    bias = (res.get("bias") or "Neutral").title()
    conf = int(res.get("confidence") or 0)

    note = (res.get("note") or "").strip()
    note_line = f"\n🧠 Note: {note}" if note else ""

    if action == "WAIT":
        trial_line = ""
        if plan == "FREE_TRIAL" and trial_remaining is not None:
            trial_line = f"\n🧪 Free Trial remaining: {trial_remaining}"
        elif plan == "FREE":
            trial_line = "\n🧪 Free Trial: send a chart to start."

        return (
            f"🟡 WAIT | {pair} {tf} | {conf}%\n\n"
            f"No clean confirmation.\n"
            f"Wait for clearer price action."
            f"{note_line}"
            f"{trial_line}\n\n"
            f"{DISCLAIMER}"
        ).strip()

    entry = res.get("entry")
    sl = res.get("sl")
    tp1 = res.get("tp1")
    tp2 = res.get("tp2")
    tp3 = res.get("tp3")

    trial_line = ""
    if plan == "FREE_TRIAL" and trial_remaining is not None:
        trial_line = f"\n🧪 Free Trial remaining: {trial_remaining}"

    return (
        f"🟢 {action} | {pair} {tf} | {bias} | {conf}%\n\n"
        f"🎯 Entry: {entry}\n"
        f"🛑 SL: {sl}\n"
        f"✅ TP1: {tp1}\n"
        f"✅ TP2: {tp2}\n"
        f"✅ TP3: {tp3}"
        f"{note_line}"
        f"{trial_line}\n\n"
        f"{DISCLAIMER}"
    ).strip()


# =========================
# OpenAI (blocking)
# =========================
def call_openai_vision_blocking(image_bytes: bytes, plan: str) -> Tuple[Optional[Dict[str, Any]], str]:
    if not OPENAI_API_KEY:
        return None, "Missing OPENAI_API_KEY"

    threshold = required_threshold(plan)

    system_prompt = (
        "You are a professional trading signal assistant.\n"
        "Return ONLY valid JSON. No markdown, no extra text.\n"
        "Schema:\n"
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
        "- If image has only candles: still analyze using price action, trend, structure, key levels.\n"
        "- Prefer high accuracy over frequent signals.\n"
    )

    user_prompt = (
        f"{mode_constraints_prompt()}\n"
        f"Minimum confidence to allow BUY/SELL: {threshold}.\n"
        "Analyze the chart screenshot. Output JSON only.\n"
        "If levels are not clear, return WAIT.\n"
    )

    b64 = base64.b64encode(image_bytes).decode("utf-8")

    # ✅ FIXED: use input_text (NOT text)
    payload = {
        "model": DEFAULT_MODEL,
        "input": [
            {
                "role": "system",
                "content": [{"type": "input_text", "text": system_prompt}],
            },
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": user_prompt},
                    {"type": "input_image", "image_url": f"data:image/jpeg;base64,{b64}"},
                ],
            },
        ],
        "temperature": 0.2,
    }

    url = "https://api.openai.com/v1/responses"
    headers = {"Authorization": f"Bearer {OPENAI_API_KEY}", "Content-Type": "application/json"}

    try:
        r = requests.post(url, headers=headers, json=payload, timeout=60)
        raw = r.text

        if r.status_code != 200:
            return None, raw

        data = r.json()

        # Try common shapes
        text = (data.get("output_text") or "").strip()
        if not text:
            try:
                out = data.get("output", [])
                if out and out[0].get("content"):
                    # content might contain output_text blocks
                    for block in out[0]["content"]:
                        if block.get("type") in ("output_text", "text") and block.get("text"):
                            text = (block.get("text") or "").strip()
                            break
            except Exception:
                pass

        if not text:
            return None, raw

        # strict JSON parse
        try:
            j = json.loads(text)
            return j, text
        except json.JSONDecodeError:
            # extract first json object
            start = text.find("{")
            end = text.rfind("}")
            if start != -1 and end != -1 and end > start:
                candidate = text[start : end + 1]
                try:
                    j = json.loads(candidate)
                    return j, text
                except Exception:
                    return None, text
            return None, text

    except Exception as e:
        return None, str(e)


# =========================
# Sanitizer
# =========================
def sanitize_result(j: Dict[str, Any], plan: str) -> Dict[str, Any]:
    threshold = required_threshold(plan)

    action = (j.get("action") or "WAIT").upper().strip()
    pair = (j.get("pair") or "N/A").upper().strip()
    tf = (j.get("timeframe") or "N/A").upper().strip()
    bias = (j.get("bias") or "Sideways").strip()
    conf = int(float(j.get("confidence") or 0))

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

    # clamp confidence
    conf = max(0, min(100, conf))

    # If below threshold -> WAIT
    if action in ("BUY", "SELL") and conf < threshold:
        action = "WAIT"

    # WAIT must not contain levels
    if action == "WAIT":
        entry = sl = tp1 = tp2 = tp3 = None

    # BUY/SELL must contain all levels or switch to WAIT
    if action in ("BUY", "SELL"):
        if any(v is None for v in (entry, sl, tp1, tp2, tp3)):
            action = "WAIT"
            entry = sl = tp1 = tp2 = tp3 = None

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
        "note": note[:120] if note else "",
    }


# =========================
# Telegram Handlers
# =========================
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    rem = free_remaining(uid)
    await update.message.reply_text(
        "🤖 Trading AI\n"
        "Send a chart image to get a clean signal.\n\n"
        f"🧪 Free Trial: {rem}/{FREE_TRIAL_LIMIT} analyses remaining\n"
        "Type /plans for pricing.\n"
        "Type /help for commands."
    )


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(HELP_TEXT)


async def plans_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(PLANS_TEXT)


async def myid_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    await update.message.reply_text(f"🆔 Your Telegram ID: {uid}")


async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    p = user_plan(uid)
    if is_active(uid) and has_access(p):
        await update.message.reply_text(
            f"✅ Active\nUser ID: {uid}\nPlan: {p}\nDays left: {days_left(uid)}\nMode: {current_mode()}"
        )
    else:
        await update.message.reply_text(
            f"🔓 Free Trial\nUser ID: {uid}\nTrial remaining: {free_remaining(uid)}/{FREE_TRIAL_LIMIT}\nMode: {current_mode()}\n\nType /plans to upgrade."
        )


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


async def setplan_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid != ADMIN_USER_ID:
        await update.message.reply_text("❌ Admin only.")
        return

    if len(context.args) < 3:
        await update.message.reply_text(
            "Usage: /setplan <user_id> <plan> <days>\n"
            "plans: LITE, PRO, VIP_GOLD, VIP_ALL, VIP_PRO"
        )
        return

    try:
        user_id = int(context.args[0])
        plan = context.args[1].strip().upper()
        days = int(context.args[2])

        if plan not in ("LITE", "PRO", "VIP_GOLD", "VIP_ALL", "VIP_PRO"):
            await update.message.reply_text("❌ Invalid plan. Use: LITE, PRO, VIP_GOLD, VIP_ALL, VIP_PRO")
            return

        set_plan(user_id, plan, days)
        await update.message.reply_text(f"✅ Set {user_id} plan={plan} for {days} days")
    except Exception:
        await update.message.reply_text("❌ Invalid arguments.")


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id

    # Determine access
    plan = user_plan(uid)
    paid = is_active(uid) and has_access(plan)

    # Free trial gating
    if not paid:
        rem = free_remaining(uid)
        if rem <= 0:
            await update.message.reply_text(
                "🔒 Free trial finished (5/5).\n\nTo continue, subscribe:\n/plans"
            )
            return
        # allow analysis for trial
        plan = "FREE_TRIAL"

    await update.message.reply_text("📸 Received. Analyzing...")

    try:
        photo = update.message.photo[-1]
        file = await photo.get_file()
        image_bytes = await file.download_as_bytearray()

        # Run blocking call in a thread
        j, raw = await asyncio.to_thread(call_openai_vision_blocking, bytes(image_bytes), plan)

        if not j:
            logging.error(f"OpenAI error: {raw[:800]}")
            await update.message.reply_text("❌ Analysis failed. Please send a clearer chart (zoom candles) and try again.")
            return

        res = sanitize_result(j, plan)
        msg = fmt_signal(res, plan, trial_remaining=free_remaining(uid) if plan == "FREE_TRIAL" else None)
        await update.message.reply_text(msg)

        # Count only if success & trial
        if plan == "FREE_TRIAL":
            inc_free_used(uid)
            await update.message.reply_text(
                f"🧪 Free Trial: {free_remaining(uid)}/{FREE_TRIAL_LIMIT} remaining.\nUpgrade anytime: /plans"
            )

    except Exception as e:
        logging.exception(e)
        await update.message.reply_text("❌ Error while processing image. Please try again.")


def main():
    if not BOT_TOKEN:
        raise RuntimeError("Missing BOT_TOKEN env var")

    init_db()

    if not get_setting("MODE"):
        set_setting("MODE", DEFAULT_MODE)

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("plans", plans_cmd))
    app.add_handler(CommandHandler("status", status_cmd))
    app.add_handler(CommandHandler("myid", myid_cmd))

    # Admin
    app.add_handler(CommandHandler("mode", mode_cmd))
    app.add_handler(CommandHandler("setplan", setplan_cmd))

    # Photos
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))

    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
