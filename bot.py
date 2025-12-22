import os
import json
import time
import base64
import sqlite3
import logging
import asyncio
from typing import Optional, Dict, Any, Tuple, List

import requests
from fastapi import FastAPI, Request, HTTPException

from telegram import Update
from telegram.ext import (
    Application,
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
DB_PATH = os.getenv("DB_PATH", "vip.db")

# FREE trial count
FREE_TRIAL_LIMIT = int(os.getenv("FREE_TRIAL_LIMIT", "5") or "5")

# TradingView Webhook secret
TV_WEBHOOK_SECRET = os.getenv("TV_WEBHOOK_SECRET", "").strip()

# Confidence thresholds (for image analysis)
THRESH_FREE = int(os.getenv("THRESH_FREE", "55") or "55")
THRESH_PRO = int(os.getenv("THRESH_PRO", "65") or "65")
THRESH_VIP = int(os.getenv("THRESH_VIP", "70") or "70")

# Channels (Telegram Channel IDs start with -100...)
PRO_CHANNEL_ID = int(os.getenv("PRO_CHANNEL_ID", "0") or "0")
VIP_GOLD_CHANNEL_ID = int(os.getenv("VIP_GOLD_CHANNEL_ID", "0") or "0")
VIP_ALL_CHANNEL_ID = int(os.getenv("VIP_ALL_CHANNEL_ID", "0") or "0")
VIP_PRO_CHANNEL_ID = int(os.getenv("VIP_PRO_CHANNEL_ID", "0") or "0")

# Optional: send same signal also to DM for paying users
DM_VIP_TOO = os.getenv("DM_VIP_TOO", "0").strip() == "1"

# Dedupe window (seconds)
DEDUP_WINDOW_SEC = int(os.getenv("DEDUP_WINDOW_SEC", "600") or "600")  # 10 minutes

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("trading-ai-bot")

# Telegram Application (global)
tg_app: Optional[Application] = None


# =========================
# DB
# =========================
def db_conn():
    return sqlite3.connect(DB_PATH)


def init_db():
    with db_conn() as con:
        cur = con.cursor()

        # users plan
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS users(
                user_id INTEGER PRIMARY KEY,
                plan TEXT NOT NULL DEFAULT 'FREE',
                expires_at INTEGER NOT NULL DEFAULT 0
            )
            """
        )

        # free trials usage
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS free_trials(
                user_id INTEGER PRIMARY KEY,
                used INTEGER NOT NULL DEFAULT 0
            )
            """
        )

        # dedupe keys
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS dedupe(
                k TEXT PRIMARY KEY,
                ts INTEGER NOT NULL
            )
            """
        )

        # signals state for management updates
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS signal_state(
                signal_id TEXT PRIMARY KEY,
                symbol TEXT NOT NULL,
                timeframe TEXT NOT NULL,
                action TEXT NOT NULL,
                created_ts INTEGER NOT NULL,
                last_update TEXT NOT NULL DEFAULT 'NEW'
            )
            """
        )

        con.commit()


def get_user_plan(user_id: int) -> str:
    with db_conn() as con:
        cur = con.cursor()
        cur.execute("SELECT plan, expires_at FROM users WHERE user_id=?", (user_id,))
        row = cur.fetchone()
        if not row:
            return "FREE"

        plan, expires_at = str(row[0]).upper(), int(row[1])
        if plan == "FREE":
            return "FREE"

        if expires_at > int(time.time()):
            return plan

        # expired -> revert to FREE
        cur.execute("UPDATE users SET plan='FREE', expires_at=0 WHERE user_id=?", (user_id,))
        con.commit()
        return "FREE"


def set_user_plan(user_id: int, plan: str, days: int):
    plan = plan.upper().strip()
    expires_at = 0
    if plan != "FREE":
        expires_at = int(time.time()) + int(days) * 86400

    with db_conn() as con:
        cur = con.cursor()
        cur.execute(
            """
            INSERT INTO users(user_id, plan, expires_at) VALUES(?,?,?)
            ON CONFLICT(user_id) DO UPDATE SET plan=excluded.plan, expires_at=excluded.expires_at
            """,
            (user_id, plan, expires_at),
        )
        con.commit()


def list_paid_users(plans: List[str]) -> List[int]:
    plans = [p.upper() for p in plans]
    now = int(time.time())
    q = ",".join(["?"] * len(plans))
    with db_conn() as con:
        cur = con.cursor()
        cur.execute(f"SELECT user_id FROM users WHERE plan IN ({q}) AND expires_at > ?", (*plans, now))
        rows = cur.fetchall()
        return [int(r[0]) for r in rows]


def free_used(user_id: int) -> int:
    with db_conn() as con:
        cur = con.cursor()
        cur.execute("SELECT used FROM free_trials WHERE user_id=?", (user_id,))
        row = cur.fetchone()
        return int(row[0]) if row else 0


def free_remaining(user_id: int) -> int:
    return max(0, FREE_TRIAL_LIMIT - free_used(user_id))


def free_inc(user_id: int):
    with db_conn() as con:
        cur = con.cursor()
        cur.execute(
            """
            INSERT INTO free_trials(user_id, used) VALUES(?,1)
            ON CONFLICT(user_id) DO UPDATE SET used = used + 1
            """,
            (user_id,),
        )
        con.commit()


def dedupe_ok(key: str) -> bool:
    """True if allowed; False if duplicate within DEDUP_WINDOW_SEC."""
    now = int(time.time())
    with db_conn() as con:
        cur = con.cursor()
        cur.execute("SELECT ts FROM dedupe WHERE k=?", (key,))
        row = cur.fetchone()
        if row:
            ts = int(row[0])
            if now - ts < DEDUP_WINDOW_SEC:
                return False

        cur.execute(
            "INSERT INTO dedupe(k, ts) VALUES(?, ?) ON CONFLICT(k) DO UPDATE SET ts=excluded.ts",
            (key, now),
        )

        # cleanup older than 24h
        cur.execute("DELETE FROM dedupe WHERE ts < ?", (now - 86400,))
        con.commit()

    return True


def save_signal_state(signal_id: str, symbol: str, timeframe: str, action: str, update: str):
    now = int(time.time())
    with db_conn() as con:
        cur = con.cursor()
        cur.execute(
            """
            INSERT INTO signal_state(signal_id, symbol, timeframe, action, created_ts, last_update)
            VALUES(?,?,?,?,?,?)
            ON CONFLICT(signal_id) DO UPDATE SET last_update=excluded.last_update
            """,
            (signal_id, symbol, timeframe, action, now, update),
        )
        con.commit()


# =========================
# TEXT / PLANS
# =========================
PLANS_TEXT = """\
💎 Trading AI – Plans

🟢 FREE: 5 chart analyses (full setup: Bias + Entry Zone + SL + TP1/2/3 + Caution)

$49  - LITE (manual images only)
$99  - PRO (Auto Signals via TradingView + images)
$119 - VIP GOLD (Auto Signals Gold priority + images)
$149 - VIP ALL  (Auto Signals all pairs+gold + images)
$199 - VIP PRO  (strongest filters + priority + auto updates)

To activate: contact admin.
"""

HELP_TEXT = """\
🤖 Trading AI Bot

Commands:
- /start
- /help
- /plans
- /status
- /myid

Admin:
- /setplan <user_id> <PLAN> <days>
PLANS: FREE, LITE, PRO, VIP_GOLD, VIP_ALL, VIP_PRO
Example:
  /setplan 7269750900 VIP_ALL 365
"""

DISCLAIMER = "⚠️ Educational only | Risk 1–2%"


# =========================
# Delivery targets (Best setup)
# =========================
def targets_for_plan(plan: str) -> Dict[str, Any]:
    plan = plan.upper()

    # Best choice:
    # - PRO -> PRO channel
    # - VIP_GOLD -> GOLD channel
    # - VIP_ALL -> ALL channel
    # - VIP_PRO -> VIP_PRO channel (or ALL if you want)
    if plan == "PRO":
        return {"channel_id": PRO_CHANNEL_ID, "dm_plans": ["PRO"] if DM_VIP_TOO else []}
    if plan == "VIP_GOLD":
        dm = ["VIP_GOLD", "VIP_PRO"] if DM_VIP_TOO else []
        return {"channel_id": VIP_GOLD_CHANNEL_ID, "dm_plans": dm}
    if plan == "VIP_ALL":
        dm = ["VIP_ALL", "VIP_PRO"] if DM_VIP_TOO else []
        return {"channel_id": VIP_ALL_CHANNEL_ID, "dm_plans": dm}
    if plan == "VIP_PRO":
        dm = ["VIP_PRO"] if DM_VIP_TOO else []
        return {"channel_id": VIP_PRO_CHANNEL_ID or VIP_ALL_CHANNEL_ID, "dm_plans": dm}

    # fallback
    return {"channel_id": 0, "dm_plans": []}


# =========================
# Telegram send helpers
# =========================
async def tg_send(user_id: int, text: str):
    global tg_app
    if not tg_app:
        return
    try:
        await tg_app.bot.send_message(chat_id=user_id, text=text)
    except Exception:
        pass


async def tg_send_to_channel(channel_id: int, text: str):
    global tg_app
    if not tg_app or not channel_id:
        return
    try:
        await tg_app.bot.send_message(chat_id=channel_id, text=text)
    except Exception:
        pass


# =========================
# Formatting (Image analysis output)
# FREE must output full setup always (even if WAIT -> SETUP)
# =========================
def fmt_image_result(res: Dict[str, Any], plan: str, trial_left: Optional[int]) -> str:
    action = (res.get("action") or "WAIT").upper()
    pair = (res.get("pair") or "N/A").upper()
    tf = (res.get("timeframe") or "N/A").upper()
    bias = (res.get("market_bias") or "Neutral").title()
    conf = int(res.get("confidence") or 0)

    entry_zone = res.get("entry_zone") or {}
    buy_zone = entry_zone.get("buy")
    sell_zone = entry_zone.get("sell")

    sl = res.get("sl")
    tp1 = res.get("tp1")
    tp2 = res.get("tp2")
    tp3 = res.get("tp3")

    levels = res.get("levels") or {}
    buy_break = levels.get("buy_break")
    sell_break = levels.get("sell_break")

    caution = (res.get("caution") or "").strip()

    header_action = "SETUP" if action == "WAIT" else action
    icon = "🟡" if action == "WAIT" else ("🟢" if action == "BUY" else "🔴")

    lines = [
        f"{icon} {header_action} | {pair} {tf} | {conf}%",
        "",
        f"📌 Market Bias: {bias}",
        "",
        "🎯 Entry Zone:",
    ]

    if buy_zone:
        lines.append(f"🟢 Buy Zone: {buy_zone}")
    if sell_zone:
        lines.append(f"🔴 Sell Zone: {sell_zone}")
    if (not buy_zone) and (not sell_zone):
        lines.append("—")

    lines += [
        "",
        f"🛑 SL: {sl}",
        f"✅ TP1: {tp1}",
        f"✅ TP2: {tp2}",
        f"✅ TP3: {tp3}",
        "",
        "📈 Trigger Levels:",
    ]

    if buy_break is not None:
        lines.append(f"⬆️ Buy if breaks above: {buy_break}")
    if sell_break is not None:
        lines.append(f"⬇️ Sell if breaks below: {sell_break}")
    if (buy_break is None) and (sell_break is None):
        lines.append("—")

    if caution:
        lines += ["", f"⚠️ Caution: {caution[:180]}"]

    if plan == "FREE" and trial_left is not None:
        lines += ["", f"🧪 Free Trial remaining: {trial_left}/{FREE_TRIAL_LIMIT}", "Upgrade: /plans"]

    lines += ["", DISCLAIMER]
    return "\n".join(lines).strip()


# =========================
# OpenAI Vision (Responses API)
# Important: input_text + input_image
# FREE returns full setup always
# =========================
def call_openai_vision(image_bytes: bytes, plan: str) -> Tuple[Optional[Dict[str, Any]], str]:
    if not OPENAI_API_KEY:
        return None, "Missing OPENAI_API_KEY"

    plan = plan.upper()
    if plan in ("VIP_GOLD", "VIP_ALL", "VIP_PRO"):
        min_conf = THRESH_VIP
    elif plan == "PRO":
        min_conf = THRESH_PRO
    else:
        min_conf = THRESH_FREE

    system_prompt = (
        "You are a professional trading signal assistant.\n"
        "Return ONLY valid JSON. No markdown.\n"
        "Schema:\n"
        "{"
        "\"action\":\"BUY|SELL|WAIT\","
        "\"pair\":\"string\","
        "\"timeframe\":\"string\","
        "\"market_bias\":\"Bullish|Bearish|Neutral\","
        "\"confidence\": number(0-100),"
        "\"entry_zone\": {\"buy\":\"string|null\",\"sell\":\"string|null\"},"
        "\"sl\": number,"
        "\"tp1\": number,"
        "\"tp2\": number,"
        "\"tp3\": number,"
        "\"levels\": {\"buy_break\": number|null, \"sell_break\": number|null},"
        "\"caution\":\"short string\""
        "}\n"
        "Rules:\n"
        "- Always provide market_bias.\n"
        "- If no clean confirmation, action MUST be WAIT.\n"
        "- EVEN IF action is WAIT: still provide entry_zone, SL, TP1-TP3, and trigger levels if possible.\n"
        "- If indicators are missing, analyze using price action + trend + structure + key levels.\n"
        "- SL/TP must be realistic relative to current price on the chart.\n"
        "- Keep caution short (<= 180 chars).\n"
    )

    user_prompt = (
        f"Plan: {plan}\n"
        f"Min confidence for BUY/SELL: {min_conf}\n"
        "Analyze the chart screenshot and output ONE decision.\n"
        "If not confirmed, output WAIT but still provide a complete SETUP.\n"
        "Return JSON only.\n"
    )

    b64 = base64.b64encode(image_bytes).decode("utf-8")

    payload = {
        "model": DEFAULT_MODEL,
        "input": [
            {"role": "system", "content": [{"type": "input_text", "text": system_prompt}]},
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

        text = (data.get("output_text") or "").strip()
        if not text:
            try:
                out = data.get("output", [])
                for item in out:
                    for c in item.get("content", []):
                        if c.get("type") == "output_text" and c.get("text"):
                            text += c.get("text")
            except Exception:
                pass

        text = (text or "").strip()
        if not text:
            return None, raw

        try:
            return json.loads(text), text
        except json.JSONDecodeError:
            s = text.find("{")
            e = text.rfind("}")
            if s != -1 and e != -1 and e > s:
                cand = text[s : e + 1]
                try:
                    return json.loads(cand), text
                except Exception:
                    return None, text
            return None, text

    except Exception as e:
        return None, str(e)


def sanitize_res(j: Dict[str, Any], plan: str) -> Dict[str, Any]:
    def to_num(x, default=None):
        try:
            if x is None:
                return default
            return round(float(x), 2)
        except Exception:
            return default

    plan = plan.upper()
    if plan in ("VIP_GOLD", "VIP_ALL", "VIP_PRO"):
        min_conf = THRESH_VIP
    elif plan == "PRO":
        min_conf = THRESH_PRO
    else:
        min_conf = THRESH_FREE

    action = (j.get("action") or "WAIT").upper().strip()
    conf = int(float(j.get("confidence") or 0))
    conf = max(0, min(100, conf))

    # if BUY/SELL but below min confidence -> WAIT
    if action in ("BUY", "SELL") and conf < min_conf:
        action = "WAIT"

    # avoid weird 0% in WAIT
    if action == "WAIT" and conf == 0:
        conf = 55

    entry_zone = j.get("entry_zone") or {}
    levels = j.get("levels") or {}

    return {
        "action": action,
        "pair": (j.get("pair") or "XAUUSD").upper().strip(),
        "timeframe": (j.get("timeframe") or "M5").upper().strip(),
        "market_bias": (j.get("market_bias") or "Neutral").title(),
        "confidence": conf,
        "entry_zone": {
            "buy": entry_zone.get("buy"),
            "sell": entry_zone.get("sell"),
        },
        "sl": to_num(j.get("sl"), 0),
        "tp1": to_num(j.get("tp1"), 0),
        "tp2": to_num(j.get("tp2"), 0),
        "tp3": to_num(j.get("tp3"), 0),
        "levels": {
            "buy_break": to_num(levels.get("buy_break"), None),
            "sell_break": to_num(levels.get("sell_break"), None),
        },
        "caution": (j.get("caution") or "").strip(),
    }


# =========================
# Telegram handlers
# =========================
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    plan = get_user_plan(uid)
    if plan == "FREE":
        rem = free_remaining(uid)
        await update.message.reply_text(
            f"🤖 Trading AI\nSend a chart image to get a full setup.\n\n🧪 Free Trial: {rem}/{FREE_TRIAL_LIMIT}\n\n/plans"
        )
    else:
        await update.message.reply_text(
            f"🤖 Trading AI\n✅ Plan: {plan}\n\nSend images anytime.\nAuto Signals will arrive automatically (PRO/VIP).\n\n/status"
        )


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(HELP_TEXT)


async def plans_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(PLANS_TEXT)


async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    plan = get_user_plan(uid)
    if plan == "FREE":
        await update.message.reply_text(
            f"Plan: FREE\nFree Trial remaining: {free_remaining(uid)}/{FREE_TRIAL_LIMIT}\n\n/plans"
        )
    else:
        await update.message.reply_text(
            f"✅ Active Plan: {plan}\n\nAuto Signals: ON (TradingView)\nImages: ON\n"
        )


async def myid_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"🆔 Your Telegram ID:\n{update.effective_user.id}")


async def setplan_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid != ADMIN_USER_ID:
        await update.message.reply_text("❌ Admin only.")
        return

    if len(context.args) < 3:
        await update.message.reply_text(
            "Usage: /setplan <user_id> <PLAN> <days>\nPLANS: FREE, LITE, PRO, VIP_GOLD, VIP_ALL, VIP_PRO"
        )
        return

    try:
        user_id = int(context.args[0])
        plan = context.args[1].upper().strip()
        days = int(context.args[2])
        if plan not in ("FREE", "LITE", "PRO", "VIP_GOLD", "VIP_ALL", "VIP_PRO"):
            await update.message.reply_text("❌ Invalid plan.")
            return
        set_user_plan(user_id, plan, days)
        await update.message.reply_text(f"✅ Set {user_id} plan={plan} for {days} days")
    except Exception:
        await update.message.reply_text("❌ Invalid arguments.")


async def _download_image_bytes(update: Update) -> Optional[bytes]:
    # PHOTO
    if update.message.photo:
        photo = update.message.photo[-1]
        f = await photo.get_file()
        b = await f.download_as_bytearray()
        return bytes(b)

    # DOCUMENT image (Send as file)
    doc = update.message.document
    if doc and doc.mime_type and doc.mime_type.startswith("image/"):
        f = await doc.get_file()
        b = await f.download_as_bytearray()
        return bytes(b)

    return None


async def handle_image(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    plan = get_user_plan(uid)

    if plan == "FREE":
        if free_remaining(uid) <= 0:
            await update.message.reply_text("🔒 Free trial ended.\nUpgrade: /plans")
            return

    img = await _download_image_bytes(update)
    if not img:
        await update.message.reply_text("❌ Please send a chart image (photo or image file).")
        return

    await update.message.reply_text("📸 Received. Analyzing...")

    try:
        j, raw = await asyncio.to_thread(call_openai_vision, img, plan)
        if not j:
            await update.message.reply_text("❌ Analysis failed. Try a clearer screenshot (zoom candles).")
            if uid == ADMIN_USER_ID:
                await update.message.reply_text(f"Debug:\n{raw[:1200]}")
            return

        res = sanitize_res(j, plan)

        trial_left = None
        if plan == "FREE":
            free_inc(uid)
            trial_left = free_remaining(uid)

        msg = fmt_image_result(res, plan, trial_left)
        await update.message.reply_text(msg)

    except Exception as e:
        logger.exception(e)
        await update.message.reply_text("❌ Error while processing image. Please try again.")


# =========================
# FastAPI (TradingView Webhook)
# =========================
app = FastAPI()


@app.get("/")
def root():
    return {"ok": True, "service": "trading-ai-bot"}


def format_tv_message(payload: Dict[str, Any]) -> str:
    """
    TradingView JSON Examples:

    NEW SIGNAL:
    {
      "secret":"TV_SECRET",
      "plan":"VIP_ALL",
      "signal_id":"XAUUSD-M5-breakout",
      "symbol":"XAUUSD",
      "timeframe":"M5",
      "action":"BUY",
      "market_bias":"Bullish",
      "confidence":78,
      "entry_zone":"4423-4426",
      "sl":4412,
      "tp1":4432,
      "tp2":4440,
      "tp3":4448,
      "caution":"..."
    }

    UPDATE:
    {
      "secret":"TV_SECRET",
      "plan":"VIP_ALL",
      "signal_id":"XAUUSD-M5-breakout",
      "symbol":"XAUUSD",
      "timeframe":"M5",
      "update":"TP1_HIT",
      "note":"Move SL to BE"
    }
    """
    symbol = str(payload.get("symbol", "N/A")).upper()
    tf = str(payload.get("timeframe", "N/A")).upper()

    update = str(payload.get("update", "") or "").upper().strip()
    note = str(payload.get("note", "") or "").strip()

    if update:
        icon = "✅" if "TP" in update else ("🛑" if "SL" in update else "ℹ️")
        lines = [
            f"{icon} UPDATE | {symbol} {tf}",
            "",
            f"🔔 Event: {update}",
        ]
        if note:
            lines += ["", f"🧠 Note: {note[:180]}"]
        lines += ["", DISCLAIMER]
        return "\n".join(lines).strip()

    action = str(payload.get("action", "SETUP")).upper()
    bias = str(payload.get("market_bias", "Neutral")).title()
    conf = int(float(payload.get("confidence", 0) or 0))

    entry_zone = payload.get("entry_zone", "")
    sl = payload.get("sl")
    tp1 = payload.get("tp1")
    tp2 = payload.get("tp2")
    tp3 = payload.get("tp3")
    caution = str(payload.get("caution", "")).strip()

    icon = "🟢" if action == "BUY" else ("🔴" if action == "SELL" else "🟡")

    lines = [
        f"{icon} {action} | {symbol} {tf} | {conf}%",
        "",
        f"📌 Market Bias: {bias}",
        "",
        f"🎯 Entry Zone: {entry_zone}",
        f"🛑 SL: {sl}",
        f"✅ TP1: {tp1}",
        f"✅ TP2: {tp2}",
        f"✅ TP3: {tp3}",
    ]
    if caution:
        lines += ["", f"⚠️ Caution: {caution[:180]}"]
    lines += ["", DISCLAIMER]
    return "\n".join(lines).strip()


@app.post("/tv")
async def tradingview_webhook(req: Request):
    if not TV_WEBHOOK_SECRET:
        raise HTTPException(status_code=500, detail="TV_WEBHOOK_SECRET not set")

    try:
        payload = await req.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    if payload.get("secret") != TV_WEBHOOK_SECRET:
        raise HTTPException(status_code=401, detail="Unauthorized")

    plan = str(payload.get("plan", "VIP_ALL")).upper().strip()
    symbol = str(payload.get("symbol", "N/A")).upper()
    tf = str(payload.get("timeframe", "N/A")).upper()
    action = str(payload.get("action", "SETUP")).upper()
    update = str(payload.get("update", "") or "").upper().strip()

    signal_id = str(payload.get("signal_id", "")).strip()
    if not signal_id:
        signal_id = f"{symbol}:{tf}:{action}:{update or 'NEW'}"

    # Dedupe (avoid spam)
    dedupe_key = f"{plan}|{signal_id}"
    if not dedupe_ok(dedupe_key):
        return {"ok": True, "skipped": "duplicate", "key": dedupe_key}

    save_signal_state(signal_id, symbol, tf, action, update or "NEW")

    msg = format_tv_message(payload)

    # Send targets
    t = targets_for_plan(plan)
    channel_id = int(t.get("channel_id") or 0)

    if channel_id:
        asyncio.create_task(tg_send_to_channel(channel_id, msg))

    dm_plans = t.get("dm_plans") or []
    if dm_plans:
        users = list_paid_users(dm_plans)
        for u in users:
            asyncio.create_task(tg_send(u, msg))

    return {"ok": True, "plan": plan, "channel": channel_id, "signal_id": signal_id}


# =========================
# Startup: run Telegram polling inside FastAPI
# =========================
@app.on_event("startup")
async def on_startup():
    global tg_app
    init_db()

    if not BOT_TOKEN:
        logger.error("Missing BOT_TOKEN")
        return

    tg_app = ApplicationBuilder().token(BOT_TOKEN).build()

    tg_app.add_handler(CommandHandler("start", start_cmd))
    tg_app.add_handler(CommandHandler("help", help_cmd))
    tg_app.add_handler(CommandHandler("plans", plans_cmd))
    tg_app.add_handler(CommandHandler("status", status_cmd))
    tg_app.add_handler(CommandHandler("myid", myid_cmd))
    tg_app.add_handler(CommandHandler("setplan", setplan_cmd))

    # Images
    tg_app.add_handler(MessageHandler(filters.PHOTO, handle_image))
    tg_app.add_handler(MessageHandler(filters.Document.IMAGE, handle_image))

    await tg_app.initialize()
    await tg_app.start()

    # Start polling in background
    try:
        asyncio.create_task(tg_app.updater.start_polling(drop_pending_updates=True))
        logger.info("Telegram polling started + FastAPI webhook ready at /tv")
    except Exception as e:
        logger.exception(e)
        logger.error("Failed to start polling. Make sure python-telegram-bot version matches requirements.")


@app.on_event("shutdown")
async def on_shutdown():
    global tg_app
    try:
        if tg_app:
            await tg_app.stop()
            await tg_app.shutdown()
    except Exception:
        pass
