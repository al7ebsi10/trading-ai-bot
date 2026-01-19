import os
import re
import json
import time
import base64
import asyncio
from io import BytesIO

import requests
from PIL import Image
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ChatAction
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    ContextTypes, filters
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

# ✅ TP1 fixed always (Marketing rule) - KEEP AS-IS
TP1_FIXED_POINTS = int(os.getenv("TP1_FIXED_POINTS", "200"))

# TP2/TP3 weak vs strong - KEEP AS-IS
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

# ✅ Plans: ONLY FREE + PAID (Lifetime)
PLANS = ["FREE", "PAID"]  # PAID = $49 Lifetime

# =========================
# Marketing + Gumroad
# =========================
GUMROAD_URL = "https://6864159013627.gumroad.com/l/vrjql"
ADMIN_EMAIL = "Al7ebsi17@gmail.com"  # مرجعي فقط

# Pending email step (no webhook)
PENDING_EMAIL = {}  # user_id -> True


# =========================
# i18n + UI (Noro-style)
# =========================
LANGS = ["en", "ar", "fr"]

TEXT = {
    "choose_lang": {
        "en": "🌍 Please choose your language:",
        "ar": "🌍 اختر لغتك:",
        "fr": "🌍 Veuillez choisir votre langue :",
    },
    "lang_set": {
        "en": "✅ Language set to English.",
        "ar": "✅ تم ضبط اللغة على العربية.",
        "fr": "✅ Langue définie sur le Français.",
    },
    "welcome_short": {
        "en": (
            "🤖 <b>Trading AI Bot</b>\n\n"
            "Send a <b>clear chart screenshot</b> (zoom on candles) and you will get:\n"
            "• Market State (Bullish/Bearish/Neutral)\n"
            "• Signal (BUY/SELL) + Entry Zone\n"
            "• TP1/TP2/TP3 + SL\n\n"
            f"🆓 Free Trial: <b>{FREE_TRIAL_LIMIT}</b> analyses.\n"
        ),
        "ar": (
            "🤖 <b>Trading AI Bot</b>\n\n"
            "أرسل <b>صورة شارت واضحة</b> (قرّب الشموع) وستحصل على:\n"
            "• حالة السوق (صاعد/هابط/محايد)\n"
            "• توصية (شراء/بيع) + منطقة دخول\n"
            "• TP1/TP2/TP3 + وقف الخسارة\n\n"
            f"🆓 التجربة المجانية: <b>{FREE_TRIAL_LIMIT}</b> تحليلات.\n"
        ),
        "fr": (
            "🤖 <b>Trading AI Bot</b>\n\n"
            "Envoyez une <b>capture claire</b> (zoom sur les bougies) et vous recevrez:\n"
            "• État du marché (Haussier/Baissier/Neutre)\n"
            "• Signal (ACHAT/VENTE) + Zone d’entrée\n"
            "• TP1/TP2/TP3 + SL\n\n"
            f"🆓 Essai gratuit : <b>{FREE_TRIAL_LIMIT}</b> analyses.\n"
        ),
    },
    "menu_hint": {
        "en": "Choose an option 👇",
        "ar": "اختر خيار 👇",
        "fr": "Choisissez une option 👇",
    },
    "send_photo": {
        "en": "📸 Please send a chart screenshot for analysis.\n\nTip: Add symbol & timeframe in caption (e.g., <b>EURUSD M5</b>).",
        "ar": "📸 أرسل صورة الشارت للتحليل.\n\nنصيحة: اكتب اسم الأصل والفريم في الكابشن (مثال: <b>EURUSD M5</b>).",
        "fr": "📸 Envoyez une capture du graphique.\n\nAstuce : Ajoutez le symbole et le timeframe (ex : <b>EURUSD M5</b>).",
    },
    "analyzing": {
        "en": "🔍 Analyzing…",
        "ar": "🔍 جاري التحليل…",
        "fr": "🔍 Analyse…",
    },
    "help": {
        "en": "❓ <b>Help</b>\n1) Choose language\n2) Send chart screenshot\n3) Follow risk 1–2%",
        "ar": "❓ <b>مساعدة</b>\n1) اختر اللغة\n2) أرسل صورة الشارت\n3) التزم بالمخاطرة 1–2%",
        "fr": "❓ <b>Aide</b>\n1) Choisir la langue\n2) Envoyer une capture\n3) Risque 1–2%",
    },
    "trial_left": {
        "en": "🧪 Free Trial remaining: {rem}/{limit}\nSubscribe: /plans ($49 lifetime)",
        "ar": "🧪 المتبقي من التجربة: {rem}/{limit}\nللاشتراك: /plans (49$ مدى الحياة)",
        "fr": "🧪 Essai restant: {rem}/{limit}\nAbonnement: /plans (49$ à vie)",
    },
    "trial_ended": {
        "en": "🔒 <b>Free trial ended.</b>\n\n",
        "ar": "🔒 <b>انتهت التجربة المجانية.</b>\n\n",
        "fr": "🔒 <b>Essai gratuit terminé.</b>\n\n",
    },
    "paid_ask_email": {
        "en": "✉️ Please send the email you used for Gumroad payment.",
        "ar": "✉️ اكتب الإيميل اللي استخدمته في الدفع (Gumroad).",
        "fr": "✉️ Veuillez envoyer l’e-mail utilisé pour le paiement Gumroad.",
    },
    "email_invalid": {
        "en": "❌ Please send a valid email address.",
        "ar": "❌ اكتب إيميل صحيح.",
        "fr": "❌ Veuillez envoyer une adresse e-mail valide.",
    },
    "email_received": {
        "en": "✅ Thanks! We received your email.\nYour subscription will be activated after verification.",
        "ar": "✅ شكرًا! تم استلام الإيميل.\nسيتم تفعيل اشتراكك بعد التأكد.",
        "fr": "✅ Merci ! E-mail reçu.\nVotre abonnement sera activé après vérification.",
    },
    "admin_not_configured": {
        "en": "⚠️ Admin not configured.\nPlease set ADMIN_IDS in server env.",
        "ar": "⚠️ الأدمن غير مُعرّف.\nأضف ADMIN_IDS في إعدادات السيرفر.",
        "fr": "⚠️ Admin non configuré.\nVeuillez définir ADMIN_IDS.",
    },
    "analysis_failed": {
        "en": "❌ Analysis failed.\nTry a clearer screenshot (zoom candles) and make sure price/symbol/TF are visible.\n\nDebug: {err}",
        "ar": "❌ فشل التحليل.\nجرّب صورة أوضح (قرّب الشموع) وتأكد إن السعر/الرمز/الفريم واضح.\n\nDebug: {err}",
        "fr": "❌ Échec de l’analyse.\nEssayez une capture plus claire (zoom bougies) et assurez-vous que le prix/symbole/TF est visible.\n\nDebug: {err}",
    },
}

OFFER_TEXT = {
    "en": (
        "🔥 <b>LIMITED OFFER</b> 🔥\n\n"
        "💎 Trading AI – <b>ULTIMATE</b> (Lifetime)\n"
        "<s>$149</s> ➜ <b>$49</b>\n\n"
        "✅ Unlimited image analysis\n"
        "✅ Unlimited signals\n"
        "✅ Priority support\n\n"
        "⬇️ Click to subscribe 👇"
    ),
    "ar": (
        "🔥 <b>عرض محدود</b> 🔥\n\n"
        "💎 Trading AI – <b>ULTIMATE</b> (مدى الحياة)\n"
        "<s>149$</s> ➜ <b>49$</b>\n\n"
        "✅ تحليل غير محدود\n"
        "✅ إشارات غير محدودة\n"
        "✅ دعم أولوية\n\n"
        "⬇️ اضغط للاشتراك 👇"
    ),
    "fr": (
        "🔥 <b>OFFRE LIMITÉE</b> 🔥\n\n"
        "💎 Trading AI – <b>ULTIMATE</b> (À vie)\n"
        "<s>149$</s> ➜ <b>49$</b>\n\n"
        "✅ Analyses illimitées\n"
        "✅ Signaux illimités\n"
        "✅ Support prioritaire\n\n"
        "⬇️ Cliquez pour vous abonner 👇"
    ),
}


def _default_user():
    return {
        "plan": "FREE",
        "expires_at": 0,   # not used for PAID
        "trial_used": 0,
        "created_at": int(time.time()),
        "lang": "en",
    }


def get_lang(u):
    lang = (u.get("lang") or "en").lower()
    return lang if lang in LANGS else "en"


def t(u, key):
    lang = get_lang(u)
    return TEXT[key].get(lang, TEXT[key]["en"])


def offer_text_html(u):
    return OFFER_TEXT.get(get_lang(u), OFFER_TEXT["en"])


def lang_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🇬🇧 English", callback_data="lang_en"),
            InlineKeyboardButton("🇸🇦 العربية", callback_data="lang_ar"),
            InlineKeyboardButton("🇫🇷 Français", callback_data="lang_fr"),
        ]
    ])


def main_menu_keyboard(u):
    lang = get_lang(u)
    if lang == "ar":
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("📸 تحليل صورة شارت", callback_data="menu_analyze")],
            [InlineKeyboardButton("💳 الاشتراك / الخطط", callback_data="menu_plans"),
             InlineKeyboardButton("❓ مساعدة", callback_data="menu_help")],
            [InlineKeyboardButton("🌐 تغيير اللغة", callback_data="menu_lang")]
        ])
    if lang == "fr":
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("📸 Analyser le graphique", callback_data="menu_analyze")],
            [InlineKeyboardButton("💳 Abonnement / Offres", callback_data="menu_plans"),
             InlineKeyboardButton("❓ Aide", callback_data="menu_help")],
            [InlineKeyboardButton("🌐 Langue", callback_data="menu_lang")]
        ])
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📸 Analyze Chart", callback_data="menu_analyze")],
        [InlineKeyboardButton("💳 Subscribe / Plans", callback_data="menu_plans"),
         InlineKeyboardButton("❓ Help", callback_data="menu_help")],
        [InlineKeyboardButton("🌐 Language", callback_data="menu_lang")]
    ])


def offer_keyboard(u):
    lang = get_lang(u)
    paid_label = {"en": "✅ I Paid / Activate", "ar": "✅ دفعت / تفعيل", "fr": "✅ J’ai payé / Activer"}[lang]
    sub_label = {"en": "💳 Subscribe – $49 (ULTIMATE)", "ar": "💳 اشتراك – 49$ (ULTIMATE)", "fr": "💳 S’abonner – 49$ (ULTIMATE)"}[lang]
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(sub_label, url=GUMROAD_URL)],
        [InlineKeyboardButton(paid_label, callback_data="paid_activate")]
    ])


# =========================
# DB helpers
# =========================
async def load_db():
    async with DB_LOCK:
        if not os.path.exists(DB_FILE):
            return {"users": {}}
        try:
            with open(DB_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {"users": {}}


async def save_db(db):
    async with DB_LOCK:
        with open(DB_FILE, "w", encoding="utf-8") as f:
            json.dump(db, f, ensure_ascii=False, indent=2)


async def get_user(db, user_id):
    uid = str(user_id)
    if uid not in db["users"]:
        db["users"][uid] = _default_user()
        await save_db(db)
    return db["users"][uid]


def is_admin(user_id):
    return user_id in ADMIN_IDS


async def set_plan(db, user_id, plan):
    plan = (plan or "").strip().upper()
    if plan not in PLANS:
        raise ValueError("Invalid plan")
    u = await get_user(db, user_id)
    u["plan"] = plan
    u["expires_at"] = 0
    await save_db(db)


async def trial_remaining(u):
    used = int(u.get("trial_used", 0) or 0)
    return max(0, FREE_TRIAL_LIMIT - used)


# =========================
# TP enforcement helpers (KEEP AS-IS)
# =========================
_NUM_RE = re.compile(r"(-?\d+(?:\.\d+)?)")


def _extract_floats(text):
    if not text:
        return []
    return [float(x) for x in _NUM_RE.findall(text)]


def _detect_decimals(text, default=1):
    if not text:
        return default
    m = re.search(r"\d+\.(\d+)", text)
    if m:
        return min(4, max(0, len(m.group(1))))
    return default


def _format_price(x, decimals):
    fmt = "{:." + str(decimals) + "f}"
    return fmt.format(x)


def _parse_entry_anchor(entry_zone):
    nums = _extract_floats(entry_zone or "")
    if not nums:
        return None
    if len(nums) >= 2 and ("-" in (entry_zone or "") or "–" in (entry_zone or "")):
        return (nums[0] + nums[1]) / 2.0
    return nums[0]


def enforce_tp_rules(result):
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
# Confidence Messaging (KEEP LOGIC)
# =========================
def confidence_profile(conf):
    try:
        c = int(conf)
    except Exception:
        c = 50

    if c >= 80:
        return ("Strong momentum",
                "Price is approaching potential exhaustion. Quick targets recommended.")
    if 70 <= c < 80:
        return ("Mild momentum",
                "Trend is active. Watch price reaction near key levels.")
    if 60 <= c < 70:
        return ("Neutral",
                "Structure is forming. Momentum is building. Partial profits recommended.")
    return ("Low conviction",
            "Low clarity. Wait for confirmation and manage risk carefully.")


def apply_confidence_messaging(result):
    conf = int(result.get("confidence", 50) or 50)
    market_label, note = confidence_profile(conf)
    result["market_label"] = market_label
    result["note_en"] = note
    result["caution"] = "Educational only. Use risk management."
    result["reasoning_short"] = ""
    return result


# =========================
# Symbol/TF normalize helpers
# =========================
def normalize_symbol(sym: str) -> str:
    s = (sym or "").strip().upper()
    # quick normalizations
    if s in ("GOLD", "XAU"):
        return "XAUUSD"
    return s


def normalize_tf(x: str) -> str:
    x = (x or "").strip().upper().replace(" ", "")
    if not x:
        return ""
    # Common forms: "5m", "M5", "5", "H1", "1H"
    x = x.replace("MIN", "M").replace("MINS", "M").replace("MINUTE", "M").replace("MINUTES", "M")
    x = x.replace("HOUR", "H").replace("HOURS", "H")
    x = x.replace("D", "D")  # keep

    # 5M -> M5
    if re.match(r"^\d+M$", x):
        return "M" + x[:-1]
    # 1H -> H1
    if re.match(r"^\d+H$", x):
        return "H" + x[:-1]
    # '5' -> M5
    if x.isdigit():
        return "M" + x

    # keep standard tokens
    for tf in ("M1", "M5", "M15", "M30", "H1", "H4", "D1"):
        if x == tf:
            return x
    # sometimes comes as "5M" already handled, or "15MIN" -> "15M" -> "M15"
    m = re.match(r"^(\d+)(M|H|D)$", x)
    if m:
        n, unit = m.group(1), m.group(2)
        if unit == "M":
            return "M" + n
        if unit == "H":
            return "H" + n
        if unit == "D":
            return "D" + n
    return x


# =========================
# OpenAI vision call (Responses API) - SAME analysis, + symbol/timeframe keys
# =========================
def image_to_base64_jpeg(image_bytes, max_side=1024, quality=85):
    img = Image.open(BytesIO(image_bytes)).convert("RGB")
    w, h = img.size
    scale = min(1.0, float(max_side) / float(max(w, h)))
    if scale < 1.0:
        img = img.resize((int(w * scale), int(h * scale)))
    out = BytesIO()
    img.save(out, format="JPEG", quality=quality, optimize=True)
    return base64.b64encode(out.getvalue()).decode("utf-8")


def openai_analyze_chart(b64jpeg):
    if not OPENAI_API_KEY:
        raise RuntimeError("Missing OPENAI_API_KEY")

    # ✅ Only added: symbol + timeframe fields (no change to your logic)
    prompt = (
        "You are a trading assistant analyzing a chart screenshot.\n"
        "Return STRICT JSON ONLY (no markdown, no extra text) with these keys:\n"
        "symbol: string (e.g., 'XAUUSD','EURUSD','BTCUSD') if visible, else ''\n"
        "timeframe: string (e.g., 'M1','M5','M15','H1','H4','D1') if visible, else ''\n"
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
        "Authorization": "Bearer {}".format(OPENAI_API_KEY),
        "Content-Type": "application/json",
    }

    payload = {
        "model": MODEL_VISION,
        "input": [
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": prompt},
                    {"type": "input_image", "image_url": "data:image/jpeg;base64,{}".format(b64jpeg)}
                ],
            }
        ],
        "max_output_tokens": 550,
    }

    r = requests.post(url, headers=headers, json=payload, timeout=60)
    if r.status_code >= 400:
        raise RuntimeError("OpenAI error {}: {}".format(r.status_code, r.text))

    data = r.json()

    out_text = ""
    for item in data.get("output", []):
        for c in item.get("content", []):
            if c.get("type") in ("output_text", "text") and "text" in c:
                out_text += c["text"]

    out_text = (out_text or "").strip()
    if not out_text:
        raise RuntimeError("Empty OpenAI output")

    try:
        parsed = json.loads(out_text)
    except Exception:
        m = re.search(r"\{.*\}", out_text, re.S)
        if not m:
            raise RuntimeError("Invalid JSON from model: {}".format(out_text[:300]))
        parsed = json.loads(m.group(0))

    # defaults (keep your old defaults + new keys)
    parsed.setdefault("symbol", "")
    parsed.setdefault("timeframe", "")

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

    # normalize symbol/timeframe
    parsed["symbol"] = normalize_symbol(parsed.get("symbol", ""))
    parsed["timeframe"] = normalize_tf(parsed.get("timeframe", ""))

    return parsed


# =========================
# Caption fallback detection (KEEP AS-IS)
# =========================
def guess_symbol_tf(caption):
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
# Output formatting (i18n wrapper)
# =========================
def format_signal_message_i18n(u, symbol_hint, timeframe_hint, result, trial_line):
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

    lang = get_lang(u)
    L = {
        "en": {
            "market_state": "Market State",
            "market": "Market",
            "entry": "Entry Zone",
            "note": "Note",
            "edu": "📌 Educational only | Risk 1–2%",
            "buy": "BUY",
            "sell": "SELL",
        },
        "ar": {
            "market_state": "حالة السوق",
            "market": "السوق",
            "entry": "منطقة الدخول",
            "note": "ملاحظة",
            "edu": "📌 للتعليم فقط | مخاطرة 1–2%",
            "buy": "شراء",
            "sell": "بيع",
        },
        "fr": {
            "market_state": "État du marché",
            "market": "Marché",
            "entry": "Zone d’entrée",
            "note": "Note",
            "edu": "📌 Éducatif seulement | Risque 1–2%",
            "buy": "ACHAT",
            "sell": "VENTE",
        },
    }[lang]

    sig_txt = L["buy"] if sig == "BUY" else L["sell"]

    msg = (
        f"{sig_emoji} {sig_txt} | {sym} | {tf} | {conf}%\n"
        f"{state_emoji} {L['market_state']}: {ms}\n"
        f"🧭 {L['market']}: {market_label}\n\n"
        f"🎯 {L['entry']}: {entry}\n"
        f"✅ TP1: {tp1}\n"
        f"✅ TP2: {tp2}\n"
        f"✅ TP3: {tp3}\n"
        f"🛑 SL: {sl}\n\n"
        f"🧠 {L['note']}: {note_en}\n"
    )

    if trial_line:
        msg += f"\n{trial_line}\n"

    msg += f"\n{L['edu']}"
    return msg


# =========================
# Telegram Handlers
# =========================
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        TEXT["choose_lang"]["en"] + "\n" + TEXT["choose_lang"]["ar"] + "\n" + TEXT["choose_lang"]["fr"],
        reply_markup=lang_keyboard()
    )


async def set_language(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    uid = query.from_user.id
    data = query.data  # lang_en / lang_ar / lang_fr
    lang = data.replace("lang_", "").strip()

    db = await load_db()
    u = await get_user(db, uid)
    u["lang"] = lang
    await save_db(db)

    await query.message.reply_text(
        t(u, "lang_set"),
        reply_markup=main_menu_keyboard(u)
    )

    await query.message.reply_text(
        t(u, "welcome_short") + "\n\n" + offer_text_html(u),
        parse_mode="HTML",
        reply_markup=offer_keyboard(u)
    )

    await query.message.reply_text(
        t(u, "menu_hint"),
        reply_markup=main_menu_keyboard(u)
    )


async def menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id

    db = await load_db()
    u = await get_user(db, uid)

    if query.data == "menu_lang":
        await query.message.reply_text(
            TEXT["choose_lang"]["en"] + "\n" + TEXT["choose_lang"]["ar"] + "\n" + TEXT["choose_lang"]["fr"],
            reply_markup=lang_keyboard()
        )
        return

    if query.data == "menu_help":
        await query.message.reply_text(t(u, "help"), parse_mode="HTML", reply_markup=main_menu_keyboard(u))
        return

    if query.data == "menu_plans":
        await query.message.reply_text(
            offer_text_html(u),
            parse_mode="HTML",
            reply_markup=offer_keyboard(u)
        )
        return

    if query.data == "menu_analyze":
        await query.message.reply_text(t(u, "send_photo"), parse_mode="HTML", reply_markup=main_menu_keyboard(u))
        return


async def cmd_myid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    await update.message.reply_text("✅ Your ID: {}".format(uid))


async def cmd_plans(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db = await load_db()
    u = await get_user(db, update.effective_user.id)
    await update.message.reply_text(
        offer_text_html(u),
        parse_mode="HTML",
        reply_markup=offer_keyboard(u)
    )


async def cmd_setplan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid not in ADMIN_IDS:
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
    await update.message.reply_text("✅ Set {} plan={}".format(target_id, plan))


async def paid_activate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id

    db = await load_db()
    u = await get_user(db, uid)

    PENDING_EMAIL[uid] = True
    await query.message.reply_text(t(u, "paid_ask_email"))


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    user_id = update.effective_user.id

    db = await load_db()
    u = await get_user(db, user_id)

    plan = (u.get("plan", "FREE") or "FREE").upper()

    if plan == "FREE":
        rem = await trial_remaining(u)
        if rem <= 0:
            await msg.reply_text(
                t(u, "trial_ended") + offer_text_html(u),
                parse_mode="HTML",
                reply_markup=offer_keyboard(u)
            )
            return

    await msg.chat.send_action(ChatAction.TYPING)
    await msg.reply_text(t(u, "analyzing"), reply_markup=main_menu_keyboard(u))

    photo = msg.photo[-1]
    file = await context.bot.get_file(photo.file_id)
    b = await file.download_as_bytearray()

    caption = msg.caption or ""
    sym_hint, tf_hint = guess_symbol_tf(caption)  # fallback from caption

    try:
        b64 = image_to_base64_jpeg(bytes(b), max_side=1100, quality=85)
        result = await asyncio.to_thread(openai_analyze_chart, b64)

        # ✅ Prefer model-detected symbol/tf if available
        model_sym = normalize_symbol(result.get("symbol", ""))
        model_tf = normalize_tf(result.get("timeframe", ""))

        if model_sym:
            sym_hint = model_sym
        if model_tf:
            tf_hint = model_tf

        # keep your enforcement + messaging as-is
        result = enforce_tp_rules(result)
        result = apply_confidence_messaging(result)

        trial_line = ""
        if plan == "FREE":
            u["trial_used"] = int(u.get("trial_used", 0) or 0) + 1
            await save_db(db)
            rem_after = await trial_remaining(u)
            trial_line = t(u, "trial_left").format(rem=rem_after, limit=FREE_TRIAL_LIMIT)

        text = format_signal_message_i18n(u, sym_hint, tf_hint, result, trial_line)

        await msg.reply_text(text, reply_markup=main_menu_keyboard(u))

    except Exception as e:
        await msg.reply_text(
            t(u, "analysis_failed").format(err=str(e)[:300]),
            reply_markup=main_menu_keyboard(u)
        )


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_text = (update.message.text or "").strip()
    uid = update.effective_user.id

    db = await load_db()
    u = await get_user(db, uid)

    # If waiting for payment email
    if uid in PENDING_EMAIL:
        if "@" not in user_text or "." not in user_text:
            await update.message.reply_text(t(u, "email_invalid"))
            return

        del PENDING_EMAIL[uid]

        username = update.effective_user.username or "NoUsername"
        cmd_ready = f"/setplan {uid} PAID"

        msg_admin = (
            "💰 Payment Request\n\n"
            f"👤 User: @{username}\n"
            f"🆔 ID: {uid}\n"
            f"📧 Email: {user_text}\n\n"
            "✅ Verify in Gumroad → Sales (search by email)\n\n"
            f"⚡ Activate command (copy/paste):\n{cmd_ready}\n\n"
            f"(Admin email ref: {ADMIN_EMAIL})"
        )

        if not ADMIN_IDS:
            await update.message.reply_text(t(u, "admin_not_configured"))
            return

        for admin_id in ADMIN_IDS:
            try:
                await context.bot.send_message(chat_id=admin_id, text=msg_admin)
            except Exception:
                pass

        await update.message.reply_text(t(u, "email_received"), reply_markup=main_menu_keyboard(u))
        return

    if user_text.startswith("/"):
        return

    await update.message.reply_text(t(u, "send_photo"), parse_mode="HTML", reply_markup=main_menu_keyboard(u))


# =========================
# Main
# =========================
async def main():
    if not BOT_TOKEN:
        raise RuntimeError("Missing TELEGRAM_BOT_TOKEN")

    if not ADMIN_IDS:
        print("WARNING: ADMIN_IDS is empty. /setplan will not work and payment requests won't reach you.")

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("myid", cmd_myid))
    app.add_handler(CommandHandler("plans", cmd_plans))
    app.add_handler(CommandHandler("setplan", cmd_setplan))

    # ✅ language selection
    app.add_handler(CallbackQueryHandler(set_language, pattern=r"^lang_(en|ar|fr)$"))

    # ✅ menu buttons
    app.add_handler(CallbackQueryHandler(menu_handler, pattern=r"^menu_(analyze|plans|help|lang)$"))

    # ✅ button callback for "I Paid / Activate"
    app.add_handler(CallbackQueryHandler(paid_activate, pattern=r"^paid_activate$"))

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
