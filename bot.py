   import os
import re
import json
import time
import base64
import asyncio
from io import BytesIO

import requests
from PIL import Image
from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import (
    Application, CommandHandler, MessageHandler, ContextTypes, filters
)

# =========================
# Config
# =========================
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
MODEL_VISION = os.getenv("MODEL_VISION", "gpt-4.1-mini").strip()

FREE_TRIAL_LIMIT = int(os.getenv("FREE_TRIAL_LIMIT", "5"))

# --- TP rules (points -> price)
POINT_VALUE = float(os.getenv("POINT_VALUE", "0.01"))  # 0.01 = 1 point
CONF_STRONG = int(os.getenv("CONF_STRONG", "70"))

# ✅ TP1 ثابت دائمًا (Marketing rule)
TP1_FIXED_POINTS = int(os.getenv("TP1_FIXED_POINTS", "200"))

# TP2/TP3 weak vs strong
TP2_WEAK_POINTS = int(os.getenv("TP2_WEAK_POINTS", "400"))
TP3_WEAK_POINTS = int(os.getenv("TP3_WEAK_POINTS", "600"))

TP2_STRONG_POINTS = int(os.getenv("TP2_STRONG_POINTS", "500"))
TP3_STRONG_POINTS = int(os.getenv("TP3_STRONG_POINTS", "700"))

# Admin IDs: "7269750900,123"
ADMIN_IDS = set()
_admin_raw = os.getenv("ADMIN_IDS", "").strip()
if _admin_raw:
    for x in re.split(r"[,\s]+", _admin_raw):
        x = x.strip()
        if x.isdigit():
            ADMIN_IDS.add(int(x))

DB_FILE = "db.json"
DB_LOCK = asyncio.Lock()

# ✅ Plans: فقط Free + Paid (Lifetime)
PLANS = ["FREE", "PAID"]  # PAID = $49 Lifetime

WELCOME_TEXT = (
    "🤖 Trading AI Bot\n\n"
    "Send a CLEAR chart screenshot (zoom on candles) and you will get:\n"
    "• Market State (Bullish/Bearish/Neutral)\n"
    "• Signal (BUY/SELL) + Entry Zone\n"
    "• TP1/TP2/TP3 + SL\n\n"
    f"🆓 Free Trial: {FREE_TRIAL_LIMIT} image analyses.\n"
    "💎 After trial: $49 Lifetime (one-time) — Unlimited photos & unlimited time.\n\n"
    "Commands:\n"
    "/myid - Show your ID\n"
    "/plans - Subscription info\n"
)

PLANS_TEXT = (
    "💎 Trading AI Subscription\n\n"
    f"• FREE: {FREE_TRIAL_LIMIT} image analyses trial\n"
    "• LIFETIME: $49 (one-time payment)\n"
    "  - Unlimited photos\n"
    "  - Unlimited time\n\n"
    "To subscribe, contact support/admin.\n"
)

# =========================
# DB helpers
# =========================
def _now_ts() -> int:
    return int(time.time())

def _default_user():
    return {
        "plan": "FREE",
        "expires_at": 0,   # not used for PAID
        "trial_used": 0,
        "created_at": _now_ts(),
    }

async def load_db() -> dict:
    async with DB_LOCK:
        if not os.path.exists(DB_FILE):
            return {"users": {}}
        try:
            with open(DB_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {"users": {}}

async def save_db(db: dict):
    async with DB_LOCK:
        with open(DB_FILE, "w", encoding="utf-8") as f:
            json.dump(db, f, ensure_ascii=False, indent=2)

def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS

def plan_active(u: dict) -> bool:
    p = (u.get("plan", "FREE") or "FREE").upper()
    if p == "PAID":
        return True  # Lifetime
    # FREE is handled via trial counter
    return True

async def get_user(db: dict, user_id: int) -> dict:
    uid = str(user_id)
    if uid not in db["users"]:
        db["users"][uid] = _default_user()
        await save_db(db)
    return db["users"][uid]

async def set_plan(db: dict, user_id: int, plan: str):
    plan = (plan or "").strip().upper()
    if plan not in PLANS:
        raise ValueError("Invalid plan")
    u = await get_user(db, user_id)
    u["plan"] = plan
    # Lifetime: no expiry needed
    u["expires_at"] = 0
    await save_db(db)

async def trial_remaining(u: dict) -> int:
    used = int(u.get("trial_used", 0) or 0)
    return max(0, FREE_TRIAL_LIMIT - used)

# =========================
# TP enforcement helpers
# =========================
_NUM_RE = re.compile(r"(-?\d+(?:\.\d+)?)")

def _extract_floats(text: str) -> list[float]:
    if not text:
        return []
    return [float(x) for x in _NUM_RE.findall(text)]

def _detect_decimals(text: str, default: int = 1) -> int:
    if not text:
        return default
    m = re.search(r"\d+\.(\d+)", text)
    if m:
        return min(4, max(0, len(m.group(1))))
    return default

def _format_price(x: float, decimals: int) -> str:
    fmt = f"{{:.{decimals}f}}"
    return fmt.format(x)

def _parse_entry_anchor(entry_zone: str) -> float | None:
    nums = _extract_floats(entry_zone or "")
    if not nums:
        return None
    if len(nums) >= 2 and ("-" in (entry_zone or "") or "–" in (entry_zone or "")):
        return (nums[0] + nums[1]) / 2.0
    return nums[0]

def enforce_tp_rules(result: dict) -> dict:
    """
    ✅ Marketing rule:
    - TP1 fixed always
    - TP2/TP3 based on confidence (weak/strong)
    """
    try:
        conf = int(result.get("confidence", 50) or 50)
    except Exception:
        conf = 50

    entry_zone = str(result.get("entry_zone", "") or "")
    anchor = _parse_entry_anchor(entry_zone)
    if anchor is None:
        return result

    decimals = _detect_decimals(entry_zone, default=1)

    sig = str(result.get("signal", "BUY") or "BUY").upper()
    if sig not in ("BUY", "SELL"):
        sig = "BUY"

    strong = conf >= CONF_STRONG

    p1 = TP1_FIXED_POINTS
    p2 = TP2_STRONG_POINTS if strong else TP2_WEAK_POINTS
    p3 = TP3_STRONG_POINTS if strong else TP3_WEAK_POINTS

    d1 = p1 * POINT_VALUE
    d2 = p2 * POINT_VALUE
    d3 = p3 * POINT_VALUE

    if sig == "BUY":
        tp1 = anchor + d1
        tp2 = anchor + d2
        tp3 = anchor + d3
    else:
        tp1 = anchor - d1
        tp2 = anchor - d2
        tp3 = anchor - d3

    result["tp1"] = _format_price(tp1, decimals)
    result["tp2"] = _format_price(tp2, decimals)
    result["tp3"] = _format_price(tp3, decimals)
    return result

# =========================
# Confidence Messaging (EN only)
# =========================
def confidence_profile(conf: int) -> tuple[str, str]:
    """
    Returns EN-only:
      market_label: Neutral / Mild momentum / Strong momentum
      note: safe note (no promises)
    """
    try:
        c = int(conf)
    except Exception:
        c = 50

    if c >= 80:
        return (
            "Strong momentum",
            "Price is approaching potential exhaustion. Quick targets recommended."
        )
    if 70 <= c < 80:
        return (
            "Mild momentum",
            "Trend is active. Watch price reaction near key levels."
        )
    if 60 <= c < 70:
        return (
            "Neutral",
            "Structure is forming. Momentum is building. Partial profits recommended."
        )
    return (
        "Low conviction",
        "Low clarity. Wait for confirmation and manage risk carefully."
    )

def apply_confidence_messaging(result: dict) -> dict:
    conf = int(result.get("confidence", 50) or 50)
    market_label, note = confidence_profile(conf)

    result["market_label"] = market_label
    result["note_en"] = note

    # Force EN clean output (avoid Arabic/over-promising from model)
    result["caution"] = "Educational only. Use risk management."
    result["reasoning_short"] = ""
    return result

# =========================
# OpenAI vision call (Responses API)
# =========================
def image_to_base64_jpeg(image_bytes: bytes, max_side: int = 1024, quality: int = 85) -> str:
    img = Image.open(BytesIO(image_bytes)).convert("RGB")
    w, h = img.size
    scale = min(1.0, float(max_side) / float(max(w, h)))
    if scale < 1.0:
        img = img.resize((int(w * scale), int(h * scale)))
    out = BytesIO()
    img.save(out, format="JPEG", quality=quality, optimize=True)
    return base64.b64encode(out.getvalue()).decode("utf-8")

def openai_analyze_chart(b64jpeg: str) -> dict:
    if not OPENAI_API_KEY:
        raise RuntimeError("Missing OPENAI_API_KEY")

    prompt = (
        "You are a trading assistant analyzing a chart screenshot.\n"
        "Return STRICT JSON ONLY (no markdown, no extra text) with these keys:\n"
        "market_state: one of ['Bullish','Bearish','Neutral']\n"
        "signal: one of ['BUY','SELL'] (NEVER return WAIT)\n"
        "confidence: integer 0-100\n"
        "entry_zone: string like '4420.0 - 4424.0' or 'Breakout above 4435.0'\n"
        "tp1,tp2,tp3: strings (price levels)\n"
        "sl: string (price level)\n"
        "caution: short string\n"
        "reasoning_short: short 1-2 lines\n\n"
        "Rules:\n"
        "- If chart is unclear, still give a CONDITIONAL setup (breakout/breakdown) and lower confidence.\n"
        "- Use visible prices from chart when possible.\n"
        "- Keep TP/SL realistic relative to entry.\n"
        "- Do NOT mention policy, do NOT mention that you are an AI.\n"
    )

    url = "https://api.openai.com/v1/responses"
    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json",
    }

    payload = {
        "model": MODEL_VISION,
        "input": [
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": prompt},
                    {"type": "input_image", "image_url": f"data:image/jpeg;base64,{b64jpeg}"}
                ],
            }
        ],
        "max_output_tokens": 500,
    }

    r = requests.post(url, headers=headers, json=payload, timeout=60)
    if r.status_code >= 400:
        raise RuntimeError(f"OpenAI error {r.status_code}: {r.text}")

    data = r.json()

    out_text = ""
    for item in data.get("output", []):
        for c in item.get("content", []):
            if c.get("type") in ("output_text", "text") and "text" in c:
                out_text += c["text"]

    out_text = out_text.strip()
    if not out_text:
        raise RuntimeError("Empty OpenAI output")

    try:
        parsed = json.loads(out_text)
    except Exception:
        m = re.search(r"\{.*\}", out_text, re.S)
        if not m:
            raise RuntimeError(f"Invalid JSON from model: {out_text[:300]}")
        parsed = json.loads(m.group(0))

    parsed.setdefault("market_state", "Neutral")
    parsed.setdefault("signal", "BUY")
    parsed.setdefault("confidence", 50)
    parsed.setdefault("entry_zone", "N/A")
    parsed.setdefault("tp1", "N/A")
    parsed.setdefault("tp2", "N/A")
    parsed.setdefault("tp3", "N/A")
    parsed.setdefault("sl", "N/A")
    parsed.setdefault("caution", "Use risk management.")
    parsed.setdefault("reasoning_short", "")

    try:
        parsed["confidence"] = int(parsed["confidence"])
    except Exception:
        parsed["confidence"] = 50

    sig = str(parsed.get("signal", "BUY")).upper()
    parsed["signal"] = "BUY" if sig not in ("BUY", "SELL") else sig

    ms = str(parsed.get("market_state", "Neutral")).capitalize()
    if ms not in ("Bullish", "Bearish", "Neutral"):
        ms = "Neutral"
    parsed["market_state"] = ms

    return parsed

def format_signal_message(symbol_hint: str, timeframe_hint: str, result: dict, trial_line: str) -> str:
    ms = result["market_state"]
    sig = result["signal"]
    conf = result["confidence"]
    entry = result["entry_zone"]
    tp1, tp2, tp3 = result["tp1"], result["tp2"], result["tp3"]
    sl = result["sl"]

    market_label = result.get("market_label", "Neutral")
    note_en = result.get("note_en", "")

    state_emoji = "📈" if ms == "Bullish" else ("📉" if ms == "Bearish" else "⏸️")
    sig_emoji = "🟢" if sig == "BUY" else "🔴"

    sym = symbol_hint or "SYMBOL"
    tf = timeframe_hint or "TF"

    msg = (
        f"{sig_emoji} **{sig} | {sym} | {tf} | {conf}%**\n"
        f"{state_emoji} Market State: **{ms}**\n"
        f"🧭 Market: **{market_label}**\n\n"
        f"🎯 Entry Zone: **{entry}**\n"
        f"✅ TP1: **{tp1}**\n"
        f"✅ TP2: **{tp2}**\n"
        f"✅ TP3: **{tp3}**\n"
        f"🛑 SL: **{sl}**\n\n"
        f"🧠 Note: {note_en}\n"
    )

    if trial_line:
        msg += f"\n{trial_line}\n"

    msg += "\n📌 Educational only | Risk 1–2%"
    return msg

def guess_symbol_tf(caption: str) -> tuple[str, str]:
    if not caption:
        return "", ""
    cap = caption.upper()
    sym = ""
    tf = ""
    for s in ["XAUUSD", "GOLD", "BTCUSD", "EURUSD", "GBPUSD", "USDJPY"]:
        if s in cap:
            sym = "XAUUSD" if s == "GOLD" else s
            break
    m = re.search(r"\b(M1|M5|M15|M30|H1|H4|D1)\b", cap)
    if m:
        tf = m.group(1)
    return sym, tf

# =========================
# Telegram Handlers
# =========================
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(WELCOME_TEXT)

async def cmd_myid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    await update.message.reply_text(f"✅ Your ID: {uid}")

async def cmd_plans(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(PLANS_TEXT)

async def cmd_setplan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Admin only:
      /setplan <user_id> FREE
      /setplan <user_id> PAID
    """
    uid = update.effective_user.id
    if not is_admin(uid):
        await update.message.reply_text("⛔ Admin only.")
        return

    parts = (update.message.text or "").split()
    if len(parts) != 3:
        await update.message.reply_text("Usage:\n/setplan <user_id> FREE\n/setplan <user_id> PAID")
        return

    target_id = parts[1].strip()
    plan = parts[2].strip().upper()

    if not target_id.isdigit():
        await update.message.reply_text("❌ user_id must be numeric.")
        return
    if plan not in PLANS:
        await update.message.reply_text("❌ Invalid plan. Use FREE or PAID.")
        return

    db = await load_db()
    await set_plan(db, int(target_id), plan)
    await update.message.reply_text(f"✅ Set {target_id} plan={plan}")

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    user_id = update.effective_user.id

    db = await load_db()
    u = await get_user(db, user_id)

    plan = (u.get("plan", "FREE") or "FREE").upper()

    # FREE: limited trial
    if plan == "FREE":
        rem = await trial_remaining(u)
        if rem <= 0:
            await msg.reply_text("🔒 Free trial ended.\nType /plans to subscribe ($49 lifetime).")
            return
    else:
        # PAID: always active
        if not plan_active(u):
            # Should not happen, but fallback
            u["plan"] = "FREE"
            u["expires_at"] = 0
            await save_db(db)

    await msg.chat.send_action(ChatAction.TYPING)

    # Download best photo size
    photo = msg.photo[-1]
    file = await context.bot.get_file(photo.file_id)
    b = await file.download_as_bytearray()

    caption = msg.caption or ""
    sym_hint, tf_hint = guess_symbol_tf(caption)

    try:
        b64 = image_to_base64_jpeg(bytes(b), max_side=1100, quality=85)
        result = await asyncio.to_thread(openai_analyze_chart, b64)

        # ✅ TP rules
        result = enforce_tp_rules(result)

        # ✅ EN-only confidence messaging
        result = apply_confidence_messaging(result)

        # decrement trial only on success
        trial_line = ""
        if plan == "FREE":
            u["trial_used"] = int(u.get("trial_used", 0) or 0) + 1
            await save_db(db)
            rem_after = await trial_remaining(u)
            trial_line = f"🧪 Free Trial remaining: {rem_after}/{FREE_TRIAL_LIMIT}\nSubscribe: /plans ($49 lifetime)"

        text = format_signal_message(sym_hint, tf_hint, result, trial_line)
        await msg.reply_text(text, parse_mode="Markdown")

    except Exception as e:
        await msg.reply_text(
            "❌ Analysis failed.\n"
            "Try a clearer screenshot (zoom candles) and make sure price/symbol/TF are visible.\n\n"
            f"Debug: {str(e)[:300]}"
        )

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    t = (update.message.text or "").strip()
    if t.startswith("/"):
        return
    await update.message.reply_text("📌 Send a chart screenshot for analysis.\nCommands: /start /myid /plans")

# =========================
# Main
# =========================
async def main():
    if not BOT_TOKEN:
        raise RuntimeError("Missing TELEGRAM_BOT_TOKEN")
    if not ADMIN_IDS:
        print("WARNING: ADMIN_IDS is empty. /setplan will not work for anyone.")

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("myid", cmd_myid))
    app.add_handler(CommandHandler("plans", cmd_plans))
    app.add_handler(CommandHandler("setplan", cmd_setplan))

    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    print("✅ Bot starting (Polling)...")
    await app.initialize()
    await app.start()
    await app.updater.start_polling(drop_pending_updates=True)

    while True:
        await asyncio.sleep(3600)

if __name__ == "__main__":
    asyncio.run(main())
