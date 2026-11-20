import asyncio
import base64
from datetime import datetime
import io
import json
import logging
import os
import random
import re
import time
import urllib.parse
from dotenv import load_dotenv
import httpx
from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    Update,
)
from telegram.constants import ChatAction
from telegram.error import RetryAfter
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    ChatJoinRequestHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# Load environment variables
load_dotenv()

# Setup logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


def get_int_env(key: str, default: int) -> int:
    val = os.getenv(key)
    if not val or not val.strip():
        return default
    try:
        return int(val.strip())
    except ValueError:
        return default


# ==================== CONFIGURATION ====================
BOT_TOKEN = os.getenv("BOT_TOKEN") or base64.b64decode(b"ODk1MDUzODM5OTpBQUhkeTFHWV8tb3hmc0hOZi1VYVQ4Q01ITklubHpiTDhqWQ==").decode("utf-8")
ADMIN_ID = get_int_env("ADMIN_ID", 8710308658)
STORAGE_CHAT_ID = get_int_env("STORAGE_CHAT_ID", -1003946288718)

BASE = "https://altaf-api-maker.base44.app/functions/lookup"
PAK_VEH = "https://proxy-sparkle-shield.lovable.app/api/public/vehicle"
PAK_VEH_KEY = os.getenv("PAK_VEH_KEY", "vk_870dfb1483e573b46732b260cfb1ada78405bf5d3f81840a")
TIMEOUT = 60.0

HIDE_KEYS = {"notice", "developer", "credit", "credits", "watermark", "ad", "ads", "promo"}
META_KEYS = {
    "success", "tool", "query", "key", "response_format",
    "credits", "file", "result_type", "duration_ms",
}

WATERMARK_PATTERNS = [
    re.compile(r"^.*@?dead_bans.*$", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^.*@?your_father_786k.*$", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^\s*🎯\s*target\s*:\s*\*+.*$", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^\s*⏳\s*wipe\s*:\s*\d+s.*$", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^\s*⚠️\s*data from public sources.*$", re.IGNORECASE | re.MULTILINE),
]


def sanitize_text(text: str) -> str:
    """Removes API developer branding, banners, and watermarks (e.g. @Dead_bans)."""
    if not isinstance(text, str):
        return text
    cleaned = text
    for pat in WATERMARK_PATTERNS:
        cleaned = pat.sub("", cleaned)
    cleaned = re.sub(r"@?dead_bans", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"@?your_father_786k", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def sanitize_obj(obj):
    """Recursively removes watermark keys, mentions, and values from dicts and lists."""
    if isinstance(obj, str):
        return sanitize_text(obj)
    if isinstance(obj, dict):
        return {
            k: sanitize_obj(v)
            for k, v in obj.items()
            if str(k).lower() not in HIDE_KEYS and "@dead_bans" not in str(k).lower()
        }
    if isinstance(obj, list):
        return [sanitize_obj(elem) for elem in obj]
    return obj

# ---------------- BUTTON HELPER (NATIVE TELEGRAM STYLES) ----------------
def btn(text: str, style: str = None) -> KeyboardButton:
    """
    Creates a KeyboardButton with Telegram Bot API native styling.
    Supported styles: 'primary' (Blue), 'success' (Green), 'danger' (Red).
    """
    if style in ("primary", "success", "danger"):
        return KeyboardButton(text, api_kwargs={"style": style})
    return KeyboardButton(text)


def format_pts(val) -> str:
    """Formats decimal points nicely (e.g. 1.5 -> 1.5, 2.0 -> 2, 0.5 -> 0.5)."""
    try:
        r = round(float(val), 2)
        if r.is_integer():
            return str(int(r))
        return f"{r:.2f}".rstrip("0").rstrip(".")
    except Exception:
        return str(val)


# ---------------- SMALL CAPS ----------------
_SC_MAP = str.maketrans(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ",
    "ᴀʙᴄᴅᴇꜰɢʜɪᴊᴋʟᴍɴᴏᴘqʀꜱᴛᴜᴠᴡxʏᴢᴀʙᴄᴅᴇꜰɢʜɪᴊᴋʟᴍɴᴏᴘqʀꜱᴛᴜᴠᴡxʏᴢ",
)


def sc(text: str) -> str:
    return text.translate(_SC_MAP)


# ---------------- IN-MEMORY DB (ZERO STORAGE ON RENDER) ----------------
BANNED = set()
CREDITS = {}          # user_id -> float points
GIFTS = {}
CUSTOM_APIS = []
MAINTENANCE = False
USERS = set()
USAGE = {}
ADMIN_STATE = {}
REFERRALS = {}        # user_id -> list of referred user_ids
LAST_SPIN = {}        # user_id -> timestamp of last daily spin
USER_SEARCHES = {}    # user_id -> count of successful searches used
DEFAULT_FORCE_CHANNELS = {
    -1003346418146: {
        "channel_id": -1003346418146,
        "channel_title": "TOXIC ADMIN",
        "channel_username": "TOXIC_ADMINN",
        "channel_type": "Public",
        "join_url": "https://t.me/TOXIC_ADMINN",
        "enabled": True,
        "added_time": "2026-11-16 17:30:00 UTC",
    },
    -1003760598940: {
        "channel_id": -1003760598940,
        "channel_title": "Bot admin",
        "channel_username": "botadminshere",
        "channel_type": "Public",
        "join_url": "https://t.me/botadminshere",
        "enabled": True,
        "added_time": "2026-11-16 17:30:00 UTC",
    },
    -1003985707693: {
        "channel_id": -1003985707693,
        "channel_title": "Toxic",
        "channel_username": "hackerjantaparty",
        "channel_type": "Public",
        "join_url": "https://t.me/hackerjantaparty",
        "enabled": True,
        "added_time": "2026-11-16 17:30:00 UTC",
    },
    -1003831039531: {
        "channel_id": -1003831039531,
        "channel_title": "🤖 MORE BOT",
        "channel_username": "BotXHere",
        "channel_type": "Public",
        "join_url": "https://t.me/BotXHere",
        "enabled": True,
        "added_time": "2026-11-16 17:30:00 UTC",
    },
    -1004341391561: {
        "channel_id": -1004341391561,
        "channel_title": "💰 Toxic Earn",
        "channel_username": "ToxicEarnTrick",
        "channel_type": "Public",
        "join_url": "https://t.me/ToxicEarnTrick",
        "enabled": True,
        "added_time": "2026-11-16 17:30:00 UTC",
    },
    -1003401025877: {
        "channel_id": -1003401025877,
        "channel_title": "Private Channel 1",
        "channel_username": None,
        "channel_type": "Private",
        "join_url": "https://t.me/+w3srvIZXZaI5MWI1",
        "enabled": True,
        "added_time": "2026-11-16 17:30:00 UTC",
    },
    -1003856206139: {
        "channel_id": -1003856206139,
        "channel_title": "Private Channel 2",
        "channel_username": None,
        "channel_type": "Private",
        "join_url": "https://t.me/+v3trFLwO_ApiYjBl",
        "enabled": True,
        "added_time": "2026-11-16 17:30:00 UTC",
    },
    -1004307092136: {
        "channel_id": -1004307092136,
        "channel_title": "Private Request Channel",
        "channel_username": None,
        "channel_type": "Join Request",
        "join_url": "https://t.me/+PGWjFRPgke43MjM1",
        "enabled": True,
        "added_time": "2026-11-16 17:30:00 UTC",
    },
}
FORCE_CHANNELS = {k: dict(v) for k, v in DEFAULT_FORCE_CHANNELS.items()}
JOIN_REQUESTS = set() # set of (user_id, channel_id) tuples
PENDING_PAYMENTS = {} # pay_id -> payment details dict
STATE_MSG_ID = None   # Pinned state message id in storage channel

# Configurable Point & Search Settings
SETTINGS = {
    "spin_min": 0.5,
    "spin_max": 2.0,
    "free_search_limit": 2,
    "normal_search_cost": 1.5,
    "tg_search_cost": 2.0,
    "referral_reward": 10.0,
    "points_per_rupee": 10.0,  # 10 Points = 1 INR (Default)
    "upi_id": os.getenv("UPI_ID", "pawank63599-1@okicici"),  # Configurable from Admin Panel
}

# ---------------- TOOLS MATCHING SCREENSHOT EXACTLY ----------------
TOOLS = {
    "num":         ("🇮🇳 Number",          "num",          "Send the *phone number* (e.g. 9876543210):"),
    "veh":         ("🏎️ Vehicle",         "veh-2-info",   "Send the *vehicle plate* (e.g. DL01AB1234):"),
    "ifsc":        ("🏦 IFSC",            "ifsc",         "Send the *IFSC code* (e.g. SBIN0001234):"),
    "ip":          ("🌐 IP",              "ip",           "Send the *IP address* (e.g. 8.8.8.8):"),
    "pincode":     ("📮 PIN",             "pincode",      "Send the *Pincode* (e.g. 110001):"),
    "gst":         ("🏬 GST",             "gst",          "Send the *GST number*:"),
    "aadhar":      ("🆔 Aadhaar",         "aadhar",       "Send the *Aadhaar number*:"),
    "github":      ("🐙 GitHub",          "github",       "Send the *GitHub username*:"),
    "snapchat":    ("👻 Snapchat",        "snapchat",     "Send the *Snapchat username*:"),
    "pakinfo":     ("🇵🇰 Pakistan No",     "pak-info",     "Send the *Pakistan number* (e.g. 03001234567):"),
    "veh2num":     ("🔢 Vehicle to Num",  "veh2num",      "Send the *vehicle plate* (e.g. DL01AB1234):"),
    "tg":          ("🆔 TG to Number",     "tgnum",        "Send the *Telegram username or ID*:"),
    "upi":         ("💰 UPI Info",        "upi2info",     "Send the *UPI VPA / ID* (e.g. name@bank):"),
    "aadhar2pan":  ("💳 Aadhaar to PAN",  "aadhar2pan",   "Send the *Aadhaar number* to find PAN:"),
}

LABEL_TO_KEY = {}
for k, (label, _, _) in TOOLS.items():
    LABEL_TO_KEY[label] = k
    LABEL_TO_KEY[f"🔵 {label}"] = k
    LABEL_TO_KEY[f"🔴 {label}"] = k
    LABEL_TO_KEY[f"🟢 {label}"] = k

LABEL_TO_KEY["📞 Number"] = "num"
LABEL_TO_KEY["🇮🇳 Number"] = "num"
LABEL_TO_KEY["🔵 🇮🇳 Number"] = "num"
LABEL_TO_KEY["🔴 🏎️ Vehicle"] = "veh"
LABEL_TO_KEY["🟢 🏦 IFSC"] = "ifsc"
LABEL_TO_KEY["🔵 🌐 IP"] = "ip"
LABEL_TO_KEY["🔴 📮 PIN"] = "pincode"
LABEL_TO_KEY["🟢 🏬 GST"] = "gst"
LABEL_TO_KEY["🔵 🆔 Aadhaar"] = "aadhar"
LABEL_TO_KEY["🔴 🐙 GitHub"] = "github"
LABEL_TO_KEY["🟢 👻 Snapchat"] = "snapchat"
LABEL_TO_KEY["🔵 🇵🇰 Pak Info"] = "pakinfo"
LABEL_TO_KEY["🔴 🇵🇰 Pak Info"] = "pakinfo"
LABEL_TO_KEY["🔴 🇵🇰 Pakistan No"] = "pakinfo"
LABEL_TO_KEY["🔴 🔢 Veh to Num"] = "veh2num"
LABEL_TO_KEY["🟢 🔢 Veh to Num"] = "veh2num"
LABEL_TO_KEY["🟢 🔢 Vehicle to Num"] = "veh2num"
LABEL_TO_KEY["🔵 🆔 TG to Num"] = "tg"
LABEL_TO_KEY["🟢 🆔 TG to Num"] = "tg"
LABEL_TO_KEY["🔵 🆔 TG to Number"] = "tg"
LABEL_TO_KEY["🔵 💰 UPI Info"] = "upi"
LABEL_TO_KEY["🔴 💰 UPI Info"] = "upi"
LABEL_TO_KEY["🟢 💳 Aadhaar ➔ PAN"] = "aadhar2pan"
LABEL_TO_KEY["🟢 💳 Aadhaar to PAN"] = "aadhar2pan"

RAW_ADMIN_LABELS = {
    "🚫 BAN USER": "adm_ban",
    "✅ UNBAN USER": "adm_unban",
    "🔓 UNBAN ALL": "adm_unbanall",
    "📋 BANNED LIST": "adm_bannedlist",
    "➕ ADD CREDIT": "adm_addcredit",
    "➖ REMOVE CREDIT": "adm_rmcredit",
    "⚙️ SET CREDIT": "adm_setcredit",
    "💰 CHECK USER CREDIT": "adm_checkcredit",
    "🎁 CREATE GIFT": "adm_creategift",
    "📋 GIFT LIST": "adm_giftlist",
    "📢 BROADCAST": "adm_broadcast",
    "👥 USER LIST": "adm_userlist",
    "🔧 API SETTINGS": "adm_apis",
    "🗑 CLEAR APIS": "adm_clearapis",
    "🛠 MAINTENANCE": "adm_maint",
    "📊 STATS": "adm_stats",
    "⚙️ POINT SETTINGS": "adm_point_settings",
    "📢 FORCE CHANNELS": "adm_force_channels",
    "🔵 📢 FORCE CHANNELS": "adm_force_channels",
    "💾 SAVE STATE": "adm_save",
    "⬅️ BACK": "adm_back",
    "🔙 BACK": "adm_back",
    "🔙 Back": "adm_back",
    "🏠 MAIN MENU": "adm_mainmenu",
    "🏠 Main Menu": "adm_mainmenu",
    "👑 ADMIN PANEL": "adm_panel",
    "🔴 👑 ADMIN PANEL": "adm_panel",
}

ADMIN_LABELS = {}
for k, v in RAW_ADMIN_LABELS.items():
    ADMIN_LABELS[k] = v
    ADMIN_LABELS[sc(k)] = v

CANCEL_LABEL = "⬅️ Cancel"
BACK_LABEL = "🔙 Back"
MAINMENU_LABEL = "🏠 Main Menu"


# ---------------- HELPERS ----------------
def _try_json(s: str):
    try:
        return json.loads(s)
    except (json.JSONDecodeError, ValueError, TypeError):
        return None


def clean_result(raw: str) -> str:
    data = _try_json(raw)
    if data is None:
        return sanitize_text(raw.strip())
    if not isinstance(data, dict):
        sanitized_data = sanitize_obj(data)
        return json.dumps(sanitized_data, indent=2, ensure_ascii=False)
    for k in HIDE_KEYS:
        data.pop(k, None)
    for key in ("result", "data", "info", "response", "message"):
        val = data.get(key)
        if isinstance(val, str) and val.strip():
            val = sanitize_text(val)
            inner = _try_json(val)
            if isinstance(inner, (dict, list)):
                sanitized_inner = sanitize_obj(inner)
                return json.dumps(sanitized_inner, indent=2, ensure_ascii=False)
            return val.strip()
        if isinstance(val, (dict, list)) and val:
            sanitized_val = sanitize_obj(val)
            return json.dumps(sanitized_val, indent=2, ensure_ascii=False)
    for k in META_KEYS:
        data.pop(k, None)
    sanitized_data = sanitize_obj(data)
    if not sanitized_data:
        return "⚠️ No data returned."
    return json.dumps(sanitized_data, indent=2, ensure_ascii=False)


def is_successful_result(raw: str) -> bool:
    """
    Checks if an API response actually contains valid data.
    Returns False if empty, error, not found, or no records returned.
    """
    if not raw or not raw.strip():
        return False
    data = _try_json(raw)
    if data is not None:
        if isinstance(data, dict):
            if data.get("success") is False:
                return False
            if str(data.get("status", "")).lower() in ("error", "failed", "404", "false"):
                return False
            for k in ("result", "data", "info", "response", "message"):
                if k in data:
                    v = data[k]
                    if v is None:
                        return False
                    if isinstance(v, (dict, list)) and not v:
                        return False
                    if isinstance(v, str):
                        s = sanitize_text(v).strip().lower()
                        if not s or s in ("{}", "[]", "null", "none", "no data", "not found", "no record", "no records found", "✖️ no data found"):
                            return False
                        if "no data found" in s or "no record" in s:
                            return False
                    return True
            meaningful = {k: v for k, v in data.items() if k not in META_KEYS and k not in HIDE_KEYS}
            if not meaningful:
                return False
            return True
        elif isinstance(data, list):
            return len(data) > 0

    cleaned = clean_result(raw)
    if not cleaned or "⚠️ No data returned." in cleaned or "Empty result" in cleaned:
        return False
    low = cleaned.lower().strip()
    if low in ("no data", "not found", "no record", "no records found", "null", "none", "error", "✖️ no data found"):
        return False
    if "no data found" in low or "no record" in low:
        return False
    return True


async def log_to_channel(ctx: ContextTypes.DEFAULT_TYPE, text: str):
    try:
        if len(text) <= 3900:
            await ctx.bot.send_message(chat_id=STORAGE_CHAT_ID, text=text)
        else:
            for i in range(0, len(text), 3900):
                await ctx.bot.send_message(chat_id=STORAGE_CHAT_ID, text=text[i:i+3900])
    except Exception as e:
        logger.error(f"[log] failed: {e}")


async def log_admin_action(ctx: ContextTypes.DEFAULT_TYPE, admin_id: int, cid: int, name: str, ctype: str, action: str):
    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
    log_text = (
        f"📢 *FORCE CHANNELS ADMIN ACTION*\n"
        f"• Action: `{action}`\n"
        f"• Admin ID: `{admin_id}`\n"
        f"• Channel ID: `{cid}`\n"
        f"• Channel Name: {name}\n"
        f"• Channel Type: {ctype}\n"
        f"• Timestamp: `{now}`"
    )
    await log_to_channel(ctx, log_text)


def serialize_state() -> str:
    return "STATE:" + json.dumps({
        "banned": list(BANNED),
        "credits": {str(k): v for k, v in CREDITS.items()},
        "gifts": {k: {"credits": v["credits"], "max": v["max"], "used": list(v["used"])}
                  for k, v in GIFTS.items()},
        "custom_apis": CUSTOM_APIS,
        "maintenance": MAINTENANCE,
        "users": list(USERS),
        "usage": USAGE,
        "referrals": {str(k): v for k, v in REFERRALS.items()},
        "last_spin": {str(k): v for k, v in LAST_SPIN.items()},
        "user_searches": {str(k): v for k, v in USER_SEARCHES.items()},
        "settings": SETTINGS,
        "force_channels": {str(k): v for k, v in FORCE_CHANNELS.items()},
        "force_channels_initialized": True,
        "join_requests": [list(x) for x in JOIN_REQUESTS],
        "pending_payments": PENDING_PAYMENTS,
    }, ensure_ascii=False)


def load_state_from_text(txt: str) -> bool:
    global MAINTENANCE
    if not txt or not txt.startswith("STATE:"):
        return False
    try:
        d = json.loads(txt[len("STATE:"):])
    except Exception as e:
        logger.error(f"Error parsing state JSON: {e}")
        return False

    BANNED.clear()
    for x in d.get("banned", []):
        try:
            BANNED.add(int(x))
        except Exception:
            pass

    CREDITS.clear()
    for k, v in d.get("credits", {}).items():
        try:
            CREDITS[int(k)] = float(v)
        except Exception:
            pass

    GIFTS.clear()
    for k, v in d.get("gifts", {}).items():
        try:
            GIFTS[k] = {"credits": float(v["credits"]), "max": int(v["max"]), "used": set(int(u) for u in v.get("used", []))}
        except Exception:
            pass

    CUSTOM_APIS.clear()
    CUSTOM_APIS.extend(d.get("custom_apis", []))

    MAINTENANCE = d.get("maintenance", False)

    USERS.clear()
    for x in d.get("users", []):
        try:
            USERS.add(int(x))
        except Exception:
            pass

    USAGE.clear()
    USAGE.update(d.get("usage", {}))

    REFERRALS.clear()
    for k, v in d.get("referrals", {}).items():
        try:
            REFERRALS[int(k)] = [int(x) for x in v if str(x).isdigit()]
        except Exception:
            pass

    LAST_SPIN.clear()
    for k, v in d.get("last_spin", {}).items():
        try:
            LAST_SPIN[int(k)] = float(v)
        except Exception:
            pass

    USER_SEARCHES.clear()
    for k, v in d.get("user_searches", {}).items():
        try:
            USER_SEARCHES[int(k)] = int(v)
        except Exception:
            pass

    if "settings" in d and isinstance(d["settings"], dict):
        SETTINGS.update(d["settings"])
        if SETTINGS.get("upi_id") in ("pawan@upi", "", None):
            SETTINGS["upi_id"] = os.getenv("UPI_ID", "pawank63599-1@okicici")

    FORCE_CHANNELS.clear()
    if "force_channels_initialized" in d:
        for k, v in d.get("force_channels", {}).items():
            FORCE_CHANNELS[int(k)] = v
    else:
        for k, v in DEFAULT_FORCE_CHANNELS.items():
            FORCE_CHANNELS[int(k)] = dict(v)

    JOIN_REQUESTS.clear()
    for item in d.get("join_requests", []):
        if len(item) == 2:
            JOIN_REQUESTS.add((int(item[0]), int(item[1])))

    PENDING_PAYMENTS.clear()
    PENDING_PAYMENTS.update(d.get("pending_payments", {}))

    return True


async def save_state(ctx: ContextTypes.DEFAULT_TYPE):
    """Saves state to Telegram Storage Channel (Document fallback for >3800 chars)."""
    global STATE_MSG_ID
    payload = serialize_state()

    try:
        if len(payload) <= 3800:
            if STATE_MSG_ID:
                try:
                    await ctx.bot.edit_message_text(
                        chat_id=STORAGE_CHAT_ID,
                        message_id=STATE_MSG_ID,
                        text=payload,
                    )
                    return
                except Exception:
                    pass
            m = await ctx.bot.send_message(chat_id=STORAGE_CHAT_ID, text=payload)
            STATE_MSG_ID = m.message_id
        else:
            bio = io.BytesIO(payload.encode("utf-8"))
            bio.name = "bot_state.json"
            m = await ctx.bot.send_document(
                chat_id=STORAGE_CHAT_ID,
                document=bio,
                caption="STATE:📦 Database Snapshot (Auto-saved)",
            )
            STATE_MSG_ID = m.message_id

        try:
            await ctx.bot.pin_chat_message(
                chat_id=STORAGE_CHAT_ID,
                message_id=STATE_MSG_ID,
                disable_notification=True,
            )
        except Exception:
            pass

    except Exception as e:
        logger.error(f"[save_state] failed: {e}")


async def load_state_on_start(bot):
    """Restores database state from pinned channel message on startup."""
    global STATE_MSG_ID
    try:
        chat = await bot.get_chat(STORAGE_CHAT_ID)
        pinned = getattr(chat, "pinned_message", None)
        if pinned:
            if pinned.document:
                file_obj = await bot.get_file(pinned.document.file_id)
                data_bytes = await file_obj.download_as_bytearray()
                txt = data_bytes.decode("utf-8")
                if load_state_from_text(txt):
                    STATE_MSG_ID = pinned.message_id
                    logger.info("✅ State successfully restored from pinned document!")
                    return

            txt = pinned.text or ""
            if load_state_from_text(txt):
                STATE_MSG_ID = pinned.message_id
                logger.info("✅ State successfully restored from pinned text!")
                return
    except Exception as e:
        logger.warning(f"[load_state_on_start] {e}")

    logger.warning("⚠️ No pinned state found. Starting with fresh state.")


# ---------------- RENDER HTTP HEALTH CHECK SERVER ----------------
async def handle_http_health(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
    await reader.read(1024)
    body = b"OK - Telegram Bot is Active"
    response = (
        b"HTTP/1.1 200 OK\r\n"
        b"Content-Type: text/plain\r\n"
        b"Content-Length: " + str(len(body)).encode("ascii") + b"\r\n"
        b"Connection: close\r\n\r\n" + body
    )
    writer.write(response)
    await writer.drain()
    writer.close()
    await writer.wait_closed()


async def post_init(app: Application):
    port = int(os.environ.get("PORT", 8080))
    try:
        server = await asyncio.start_server(handle_http_health, "0.0.0.0", port)
        app.bot_data["render_server"] = server
        logger.info(f"🚀 Render HTTP Health server listening on port {port}")
    except OSError as e:
        logger.warning(f"Could not bind HTTP health server to port {port}: {e}")
    await load_state_on_start(app.bot)


async def post_shutdown(app: Application):
    server = app.bot_data.get("render_server")
    if server:
        server.close()
        await server.wait_closed()
        logger.info("Render HTTP server stopped.")


# ---------------- USER FORCE-JOIN VERIFICATION ----------------
async def _check_single_channel(bot, uid: int, ch: dict) -> bool:
    cid = ch["channel_id"]
    ctype = ch.get("channel_type", "Public")

    # Join Request channel: if user has already requested to join (pending in queue) -> INSTANT PASS!
    if ctype == "Join Request":
        if (uid, cid) in JOIN_REQUESTS:
            return True

    try:
        member = await bot.get_chat_member(chat_id=cid, user_id=uid)
        if member.status in ("creator", "administrator", "member"):
            return True
        elif member.status == "restricted" and getattr(member, "is_member", False):
            return True
    except Exception as e:
        logger.warning(f"Bot membership check in {cid} ({ctype}): {e}")
        return False
    return False


async def check_user_force_channels(bot, uid: int):
    if is_admin(uid):
        return True, []

    enabled_channels = [ch for ch in FORCE_CHANNELS.values() if ch.get("enabled", True)]
    if not enabled_channels:
        return True, []

    sem = asyncio.Semaphore(4)

    async def _sem_check(ch):
        async with sem:
            return await _check_single_channel(bot, uid, ch)

    results = await asyncio.gather(*[_sem_check(ch) for ch in enabled_channels])
    missing = [ch for ch, ok in zip(enabled_channels, results) if not ok]
    return len(missing) == 0, missing


def force_join_markup() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [[btn("✨ 👑 🔵 Check Membership ⚡", "primary")]],
        resize_keyboard=True,
    )


def build_force_join_ui():
    """
    User-facing force-join UI matching prompt specification:
    - Text:
      <code>🔒 ACCESS RESTRICTED

      To use this bot, please join all the channels below.

      👇 JOIN ALL CHANNELS FIRST</code>
    - Exactly 8 clickable channel Join buttons arranged in a 3 × 3 grid.
    - Exactly ONE single '🔵 Check Membership' inline button below the grid.
    """
    text = (
        "⚡️ <b>VIP NETWORK VERIFICATION</b> ⚡️\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "<code>🔒 ACCESS RESTRICTED\n\n"
        "To use this bot, please join all the channels below.\n\n"
        "👇 JOIN ALL CHANNELS FIRST</code>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "💎 <i>Status: Verification Required</i>"
    )

    enabled_channels = [ch for ch in FORCE_CHANNELS.values() if ch.get("enabled", True)]

    col_styles = [
        ("💎 🔵 Join ⚡", "primary"),   # Left: Premium Diamond (Primary Blue)
        ("🔥 🔴 Join ⚡", "danger"),    # Middle: Animated Flame (Danger Red)
        ("🌟 🟢 Join ⚡", "success"),   # Right: Golden Star (Success Green)
    ]

    inline_rows = []
    current_row = []
    for ch in enabled_channels:
        url = ch.get("join_url") or ""
        if not url.startswith("http://") and not url.startswith("https://"):
            uname = ch.get("channel_username")
            if uname:
                url = f"https://t.me/{uname.lstrip('@')}"
            else:
                url = "https://t.me/"

        col_idx = len(current_row)
        btn_text, btn_style = col_styles[col_idx]

        current_row.append(InlineKeyboardButton(text=btn_text, url=url, api_kwargs={"style": btn_style}))
        if len(current_row) == 3:
            inline_rows.append(current_row)
            current_row = []
    if current_row:
        inline_rows.append(current_row)

    inline_rows.append([InlineKeyboardButton("✨ 👑 🔵 Check Membership ⚡", callback_data="check_fsub", api_kwargs={"style": "primary"})])
    inline_kb = InlineKeyboardMarkup(inline_rows)

    return text, inline_kb


# ---------------- KEYBOARDS (100% MATCHING SCREENSHOT) ----------------
def main_menu(uid: int = None) -> ReplyKeyboardMarkup:
    """
    User Panel Menu Colors & Animated Emojis (Per 3-button Row):
    • Left (Col 1): 🔵 PRIMARY (Blue)
    • Middle (Col 2): 🔴 DANGER (Red)
    • Right (Col 3): 🟢 SUCCESS (Green)
    """
    keyboard = [
        # Row 1: Left=Primary 🔵, Middle=Danger 🔴, Right=Success 🟢
        [btn("🔵 🇮🇳 Number", "primary"), btn("🔴 🏎️ Vehicle", "danger"), btn("🟢 🏦 IFSC", "success")],
        # Row 2: Left=Primary 🔵, Middle=Danger 🔴, Right=Success 🟢
        [btn("🔵 🌐 IP", "primary"), btn("🔴 📮 PIN", "danger"), btn("🟢 🏬 GST", "success")],
        # Row 3: Left=Primary 🔵, Middle=Danger 🔴, Right=Success 🟢
        [btn("🔵 🆔 Aadhaar", "primary"), btn("🔴 🐙 GitHub", "danger"), btn("🟢 👻 Snapchat", "success")],
        # Row 4: Left=Primary 🔵, Middle=Danger 🔴, Right=Success 🟢
        [btn("🔵 🇵🇰 Pak Info", "primary"), btn("🔴 🔢 Veh to Num", "danger"), btn("🟢 🆔 TG to Num", "success")],
        # Row 5: Primary (Blue) 🔵 | Success (Green) 🟢
        [btn("🔵 💰 UPI Info", "primary"), btn("🟢 💳 Aadhaar ➔ PAN", "success")],
        # Row 6: Left=Primary 🔵, Middle=Danger 🔴, Right=Success 🟢
        [btn("🔵 💳 My Credits", "primary"), btn("🔴 👤 Profile", "danger"), btn("🟢 🆘 Support", "success")],
        # Row 7: Left=Primary 🔵, Middle=Danger 🔴, Right=Success 🟢
        [btn("🔵 🎰 Daily Spin", "primary"), btn("🔴 🔗 Referral", "danger"), btn("🟢 🎁 Claim Gift", "success")],
        # Row 8: Primary (Blue) 🔵 | Success (Green) 🟢
        [btn("🔵 💳 Buy Points", "primary"), btn("🟢 🏆 Leaderboard", "success")],
    ]
    if uid and is_admin(uid):
        keyboard.append([btn("🔴 👑 ADMIN PANEL", "danger")])
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)


def admin_menu() -> ReplyKeyboardMarkup:
    """Admin Menu with native Telegram button styles (primary, success, danger)."""
    return ReplyKeyboardMarkup([
        # Row 1: Moderation (Danger / Success)
        [btn(sc("🚫 BAN USER"), "danger"), btn(sc("✅ UNBAN USER"), "success")],
        # Row 2: Moderation Lists (Danger / Primary)
        [btn(sc("🔓 UNBAN ALL"), "danger"), btn(sc("📋 BANNED LIST"), "primary")],
        # Row 3: Credit Manage (Success / Danger)
        [btn(sc("➕ ADD CREDIT"), "success"), btn(sc("➖ REMOVE CREDIT"), "danger")],
        # Row 4: Credit Configure (Primary / Success)
        [btn(sc("⚙️ SET CREDIT"), "primary"), btn(sc("💰 CHECK USER CREDIT"), "success")],
        # Row 5: Gifts (Success / Primary)
        [btn(sc("🎁 CREATE GIFT"), "success"), btn(sc("📋 GIFT LIST"), "primary")],
        # Row 6: Broadcast & Users (Primary / Primary)
        [btn(sc("📢 BROADCAST"), "primary"), btn(sc("👥 USER LIST"), "primary")],
        # Row 7: APIs (Primary / Danger)
        [btn(sc("🔧 API SETTINGS"), "primary"), btn(sc("🗑 CLEAR APIS"), "danger")],
        # Row 8: Maintenance & Stats (Danger / Success)
        [btn(sc("🛠 MAINTENANCE"), "danger"), btn(sc("📊 STATS"), "success")],
        # Row 9: Point & Channel Settings (Primary / Primary)
        [btn(sc("⚙️ POINT SETTINGS"), "primary"), btn(sc("🔵 📢 FORCE CHANNELS"), "primary")],
        # Row 10: State & Navigation (Success / Danger)
        [btn(sc("💾 SAVE STATE"), "success"), btn(sc("🏠 MAIN MENU"), "danger")],
    ], resize_keyboard=True)


def point_settings_menu() -> ReplyKeyboardMarkup:
    """Admin Menu for managing points, costs, free searches, spin limits & UPI."""
    return ReplyKeyboardMarkup([
        [btn("💰 Rate: Pts per Rupee", "primary"), btn("🏦 Set UPI ID", "primary")],
        [btn("🎰 Spin Min Points", "primary"), btn("🎰 Spin Max Points", "primary")],
        [btn("🎁 Free Search Limit", "primary"), btn("🔗 Referral Reward", "primary")],
        [btn("🔍 Normal Search Cost", "primary"), btn("✈️ TG Search Cost", "primary")],
        [btn("📊 View Current Settings", "success")],
        [btn("🔙 Back to Admin", "danger")],
    ], resize_keyboard=True)


def force_channels_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [
            [btn("🟢 ➕ Add Channel", "success")],
            [btn("🔵 📋 List Channels", "primary")],
            [btn("🔴 🗑 Remove Channel", "danger")],
            [btn("🔵 🔁 Enable/Disable Channel", "primary")],
            [btn("🔵 🔙 Back", "primary")],
        ],
        resize_keyboard=True,
    )


def cancel_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup([[btn("⬅️ Cancel", "danger")]], resize_keyboard=True)


def back_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup([[btn("🔙 Back", "danger")]], resize_keyboard=True)


# ---------------- API CALLS ----------------
async def call_lookup(tool: str, query: str) -> str:
    if tool == "ip":
        try:
            async with httpx.AsyncClient(timeout=10.0) as c:
                r = await c.get(f"http://ip-api.com/json/{query}")
                if r.status_code == 200:
                    return r.text
        except Exception:
            pass

    if tool == "github":
        try:
            async with httpx.AsyncClient(timeout=10.0) as c:
                r = await c.get(f"https://api.github.com/users/{query}", headers={"User-Agent": "TelegramBot"})
                if r.status_code == 200:
                    return r.text
        except Exception:
            pass

    async with httpx.AsyncClient(timeout=TIMEOUT) as c:
        r = await c.get(BASE, params={"tool": tool, "key": "Demo", "q": query})
        r.raise_for_status()
        return r.text


def format_cyber_card(tool_label: str, query: str, raw_result: str, cost_note: str) -> str:
    cleaned = clean_result(raw_result)
    now_utc = datetime.utcnow().strftime("%I:%M %p UTC")

    data = _try_json(cleaned)
    body_lines = []

    if isinstance(data, dict):
        for k, v in data.items():
            k_title = str(k).replace("_", " ").title()
            if isinstance(v, (dict, list)):
                v_str = json.dumps(v, ensure_ascii=False)
            else:
                v_str = str(v).strip()
            v_str = v_str.replace("`", "'")
            body_lines.append(f"• *{k_title}:* `{v_str}`")
        body_text = "\n".join(body_lines)
    elif isinstance(data, list):
        for item in data[:15]:
            if isinstance(item, dict):
                item_parts = [f"{str(k).title()}: {str(v).replace('`', '')}" for k, v in item.items() if str(v).strip()]
                body_lines.append("• " + " | ".join(item_parts))
            else:
                safe_item = str(item).replace("`", "'")
                body_lines.append(f"• `{safe_item}`")
        body_text = "\n".join(body_lines)
    else:
        safe_cleaned = cleaned.replace("```", "'''")
        body_text = f"```\n{safe_cleaned}\n```"

    clean_q = str(query).replace("`", "")
    card = (
        "╔══════════════════════════════╗\n"
        "║   ⚡ *TOXIC INTELLIGENCE* ⚡   ║\n"
        "╚══════════════════════════════╝\n"
        f"🔍 *Tool:* `{tool_label}`\n"
        f"🎯 *Query:* `{clean_q}`\n"
        "──────────────────────────────\n"
        f"{body_text}\n"
        "──────────────────────────────\n"
        f"💎 *Charges:* `{cost_note}`\n"
        f"⏰ *Time:* `{now_utc}`"
    )
    return sanitize_text(card)


def make_result_inline_keyboard(tool_key: str, bot_username: str, result_raw: str = "", query: str = "", tool_label: str = "") -> InlineKeyboardMarkup:
    """Generates quick-action inline buttons below search result cards."""
    c_res = clean_result(result_raw) if result_raw else ""
    if len(c_res) > 350:
        c_res = c_res[:350] + "\n..."

    t_name = tool_label if tool_label else tool_key.upper()
    q_val = query if query else "Lookup"
    bot_uname = bot_username if bot_username else "ToxicBot"

    # Clean, concise share payload (plain text without heavy box-drawing characters for 100% Telegram compatibility)
    share_payload = (
        f"⚡ TOXIC OSINT INTELLIGENCE\n"
        f"🔍 Tool: {t_name}\n"
        f"🎯 Query: {q_val}\n"
        f"─────────────\n"
        f"{c_res}\n"
        f"─────────────\n"
        f"🤖 Bot: @{bot_uname}"
    )

    bot_link = f"https://t.me/{bot_uname}"
    encoded_url = urllib.parse.quote(bot_link, safe="")
    encoded_text = urllib.parse.quote(share_payload)

    # Telegram client deep link syntax: https://t.me/share/url?url=<URL>&text=<TEXT>
    forward_data_url = f"https://t.me/share/url?url={encoded_url}&text={encoded_text}"
    share_bot_url = f"https://t.me/share/url?url={encoded_url}&text=Check%20out%20Toxic%20OSINT%20Bot%20-%20Fastest%20Intelligence%20Lookup!"

    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🔄 Search Again", callback_data=f"re_tool:{tool_key}"),
            InlineKeyboardButton("🚀 Forward Data", url=forward_data_url),
        ],
        [
            InlineKeyboardButton("💳 Buy Points", callback_data="buy_points_flow"),
            InlineKeyboardButton("📤 Share Bot", url=share_bot_url),
        ],
        [
            InlineKeyboardButton("🆘 Admin Support", url=f"tg://user?id={ADMIN_ID}"),
        ],
    ])


async def send_result(msg, result: str, tool_label: str = "Lookup", query: str = "", cost_note: str = "Free", tool_key: str = "", bot_username: str = ""):
    card = format_cyber_card(tool_label, query, result, cost_note)
    inline_kb = make_result_inline_keyboard(tool_key, bot_username, result_raw=result, query=query, tool_label=tool_label) if (tool_key and bot_username) else None
    try:
        if len(card) <= 4000:
            await msg.reply_markdown(card, reply_markup=inline_kb)
        else:
            chunks = [card[i:i+3900] for i in range(0, len(card), 3900)]
            for idx, chunk in enumerate(chunks):
                kb = inline_kb if idx == len(chunks) - 1 else None
                await msg.reply_markdown(chunk, reply_markup=kb)
    except Exception as e:
        logger.error(f"Markdown send failed, falling back to plain text: {e}")
        clean_text = card.replace("*", "").replace("`", "").replace("_", "")
        if len(clean_text) <= 4000:
            await msg.reply_text(clean_text, reply_markup=inline_kb)
        else:
            chunks = [clean_text[i:i+3900] for i in range(0, len(clean_text), 3900)]
            for idx, chunk in enumerate(chunks):
                kb = inline_kb if idx == len(chunks) - 1 else None
                await msg.reply_text(chunk, reply_markup=kb)


def is_admin(uid: int) -> bool:
    return uid == ADMIN_ID


# ---------------- BUY POINTS RECHARGE SYSTEM ----------------
def buy_points_menu() -> ReplyKeyboardMarkup:
    rate = float(SETTINGS.get("points_per_rupee", 10.0))
    if rate <= 0:
        rate = 10.0
    p1, p2, p3, p4 = 50, 100, 250, 500
    r1 = round(p1 / rate, 2)
    r2 = round(p2 / rate, 2)
    r3 = round(p3 / rate, 2)
    r4 = round(p4 / rate, 2)
    return ReplyKeyboardMarkup([
        [btn(f"💎 {p1} Pts (₹{r1:g})", "primary"), btn(f"💎 {p2} Pts (₹{r2:g})", "primary")],
        [btn(f"💎 {p3} Pts (₹{r3:g})", "primary"), btn(f"💎 {p4} Pts (₹{r4:g})", "primary")],
        [btn("✏️ Custom Points", "primary")],
        [btn("⬅️ Cancel", "danger")],
    ], resize_keyboard=True)


async def start_buy_points(msg, ctx: ContextTypes.DEFAULT_TYPE, uid: int):
    ctx.user_data["awaiting"] = "buy_points_pack"
    rate = float(SETTINGS.get("points_per_rupee", 10.0))
    upi_id = SETTINGS.get("upi_id", "pawank63599-1@okicici")
    text = (
        "💳 *BUY POINTS RECHARGE*\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💰 *Current Rate:* `₹1.00 = {format_pts(rate)} Points`\n"
        f"🏦 *Payment UPI:* `{upi_id}`\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "Select a package below ya 'Custom Points' choose karein:"
    )
    await msg.reply_markdown(text, reply_markup=buy_points_menu())


async def generate_and_send_qr(msg, ctx: ContextTypes.DEFAULT_TYPE, uid: int, points: float):
    rate = float(SETTINGS.get("points_per_rupee", 10.0))
    amount_inr = round(points / rate, 2)
    if amount_inr < 1.0:
        amount_inr = 1.0

    upi_id = SETTINGS.get("upi_id", "pawank63599-1@okicici")
    upi_name = "Toxic Bot"
    note = f"Recharge_{int(points)}pts_{uid}"

    upi_uri = f"upi://pay?pa={upi_id}&pn={urllib.parse.quote(upi_name)}&am={amount_inr:.2f}&cu=INR&tn={urllib.parse.quote(note)}"
    qr_url = f"https://api.qrserver.com/v1/create-qr-code/?size=350x350&data={urllib.parse.quote(upi_uri)}"

    ctx.user_data["pending_recharge"] = {
        "points": points,
        "amount": amount_inr,
    }
    ctx.user_data["awaiting"] = "submit_screenshot"

    caption = (
        "💳 *SCAN & PAY WITH ANY UPI APP*\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🔹 *Amount to Pay:* `₹{amount_inr:.2f}`\n"
        f"🔹 *Points to Receive:* `{format_pts(points)} Points`\n"
        f"🔹 *UPI ID:* `{upi_id}`\n"
        f"🔹 *Rate:* `₹1.00 = {format_pts(rate)} Pts`\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "📲 *Payment Steps:*\n"
        f"1. Is QR Code ko apne UPI App (GPay / PhonePe / Paytm / BHIM) se scan karein.\n"
        f"2. Exact `₹{amount_inr:.2f}` pay karein.\n"
        "3. Payment complete hone ke baad uska *Screenshot* lein.\n"
        "4. *Wo Screenshot (Photo) yahan chat me bhej dein.*\n\n"
        "⏳ *Waiting for Payment Screenshot (Photo)...*"
    )

    try:
        await msg.reply_photo(photo=qr_url, caption=caption, parse_mode="Markdown", reply_markup=cancel_menu())
    except Exception as e:
        logger.error(f"Error sending QR photo: {e}")
        await msg.reply_markdown(
            f"{caption}\n\n👉 *Direct UPI Link:* `{upi_uri}`",
            reply_markup=cancel_menu()
        )


# ---------------- CHAT JOIN REQUEST HANDLER ----------------
async def handle_chat_join_request(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    req = update.chat_join_request
    if req:
        JOIN_REQUESTS.add((req.from_user.id, req.chat.id))
        await save_state(ctx)


# ---------------- USER HANDLERS ----------------
async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    uid = u.id
    is_new = uid not in USERS
    USERS.add(uid)
    ctx.user_data.pop("awaiting", None)

    # Referral checking with configurable reward
    if is_new and ctx.args and ctx.args[0].startswith("ref_"):
        try:
            ref_id = int(ctx.args[0].replace("ref_", ""))
            if ref_id != uid and ref_id in USERS:
                if ref_id not in REFERRALS:
                    REFERRALS[ref_id] = []
                if uid not in REFERRALS[ref_id]:
                    REFERRALS[ref_id].append(uid)
                    rew = float(SETTINGS.get("referral_reward", 10.0))
                    CREDITS[ref_id] = round(CREDITS.get(ref_id, 0.0) + rew, 2)
                    await save_state(ctx)
                    try:
                        safe_ref_name = str(u.first_name or "A user").replace("*", "").replace("_", " ").replace("`", "")
                        await ctx.bot.send_message(
                            chat_id=ref_id,
                            text=f"🎉 *New Referral!* {safe_ref_name} joined using your link.\n💰 *+{format_pts(rew)} Points* added!",
                            parse_mode="Markdown",
                        )
                    except Exception:
                        pass
        except Exception:
            pass

    # Admin bypasses force-join checks
    if not is_admin(u.id):
        is_ok, missing = await check_user_force_channels(ctx.bot, u.id)
        if not is_ok:
            text_msg, inline_kb = build_force_join_ui()
            try:
                await update.message.reply_html(
                    text_msg,
                    reply_markup=inline_kb,
                    disable_web_page_preview=True,
                )
            except Exception:
                await update.message.reply_text(
                    "🔒 ACCESS RESTRICTED\n\nTo use this bot, please join all the channels below.\n\n👇 JOIN ALL CHANNELS FIRST",
                    reply_markup=inline_kb,
                    disable_web_page_preview=True,
                )
            return

    if MAINTENANCE and not is_admin(u.id):
        await update.message.reply_text("🛠 Bot is under maintenance. Try again later.")
        return
    if u.id in BANNED:
        await update.message.reply_text("🚫 You are banned from using this bot.")
        return

    # Live Network Stats & Cyber Banner on /start
    total_lookups = sum(USAGE.values())
    active_nodes = len(USERS)

    safe_name = str(u.first_name).replace("*", "").replace("_", " ")
    start_banner = (
        "╔══════════════════════════════════╗\n"
        "║    ⚡ *TOXIC OSINT INTELLIGENCE* ⚡    ║\n"
        "║      _System v4.5 • Status:_ 🟢      ║\n"
        "╚══════════════════════════════════╝\n"
        f"🌐 *Global Lookups:* `{total_lookups}` Queries\n"
        f"👥 *Active Network:* `{active_nodes}` Agents\n"
        f"⚡ *Server Engine :* `High-Speed Node 🚀`\n"
        "────────────────────────────────────\n"
        f"👋 Welcome, *{safe_name}*!\n\n"
        "Neeche diye modules mein se koi bhi option select karke search shuru karein:"
    )

    try:
        await update.message.reply_markdown(
            start_banner,
            reply_markup=main_menu(uid),
        )
    except Exception:
        clean_banner = start_banner.replace("*", "").replace("_", "")
        await update.message.reply_text(
            clean_banner,
            reply_markup=main_menu(uid),
        )


async def on_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    uid = u.id
    USERS.add(uid)
    text = (update.message.text or "").strip()

    # User Membership Verification Check button
    if any(k in text for k in ("Check Membership", "Verify", "/verify")):
        is_ok, missing = await check_user_force_channels(ctx.bot, uid)
        if is_ok:
            await update.message.reply_text("✨ 🟢 <b>VERIFIED SUCCESSFULLY</b> 💎\n\n🚀 <i>Welcome! All VIP tools are now unlocked.</i>", parse_mode="HTML")
            await update.message.reply_text("🤖 *TOXIC BOT READY*\n\nOption select karo:", reply_markup=main_menu(uid), parse_mode="Markdown")
        else:
            text_msg, inline_kb = build_force_join_ui()
            try:
                await update.message.reply_html(text_msg, reply_markup=inline_kb, disable_web_page_preview=True)
            except Exception:
                await update.message.reply_text("🔒 ACCESS RESTRICTED\n\nTo use this bot, please join all the channels below.\n\n👇 JOIN ALL CHANNELS FIRST", reply_markup=inline_kb, disable_web_page_preview=True)
        return

    # Check Force-Join for non-admins on any message
    if not is_admin(uid):
        is_ok, missing = await check_user_force_channels(ctx.bot, uid)
        if not is_ok:
            text_msg, inline_kb = build_force_join_ui()
            try:
                await update.message.reply_html(text_msg, reply_markup=inline_kb, disable_web_page_preview=True)
            except Exception:
                await update.message.reply_text("🔒 ACCESS RESTRICTED\n\nTo use this bot, please join all the channels below.\n\n👇 JOIN ALL CHANNELS FIRST", reply_markup=inline_kb, disable_web_page_preview=True)
            return

    # Admin state machine handling
    if is_admin(uid) and uid in ADMIN_STATE:
        if text in (BACK_LABEL, "🔙 Back", "🔵 🔙 Back", sc("⬅️ BACK"), "🔙 Back to Admin", "❌ Cancel", "Cancel", "⬅️ Cancel", CANCEL_LABEL, sc("⬅️ CANCEL"), "/cancel"):
            state = ADMIN_STATE.get(uid, {})
            step = state.get("step", "")
            ADMIN_STATE.pop(uid, None)
            if step.startswith("fc_"):
                await update.message.reply_text("📢 *FORCE CHANNELS*", reply_markup=force_channels_menu(), parse_mode="Markdown")
            elif step.startswith("set_"):
                await update.message.reply_text("⚙️ *POINT SETTINGS*", reply_markup=point_settings_menu(), parse_mode="Markdown")
            else:
                await update.message.reply_text(f"👑 {sc('ADMIN PANEL')}:", reply_markup=admin_menu())
            return
        if text in (MAINMENU_LABEL, sc("🏠 MAIN MENU")):
            ADMIN_STATE.pop(uid, None)
            await update.message.reply_text("🤖 *TOXIC BOT READY*\n\nOption select karo:", reply_markup=main_menu(uid), parse_mode="Markdown")
            return
        await handle_admin_input(update, ctx, uid, text)
        return

    # Force Channels Management Menu Actions (ADMIN-ONLY)
    if is_admin(uid):
        if text in ("📢 FORCE CHANNELS", sc("📢 FORCE CHANNELS"), "🔵 📢 FORCE CHANNELS", sc("🔵 📢 FORCE CHANNELS")):
            await update.message.reply_text("📢 *FORCE CHANNELS*", reply_markup=force_channels_menu(), parse_mode="Markdown")
            return
        if text in ("➕ Add Channel", "🟢 ➕ Add Channel"):
            ADMIN_STATE[uid] = {"step": "fc_add_ref"}
            await update.message.reply_text(
                "➕ *Add Channel*\n\n"
                "Step 1: Please send the channel reference.\n\n"
                "You can send:\n"
                "• A forwarded message from the channel\n"
                "• @channel_username\n"
                "• Numeric Chat ID (e.g. -100123456789)",
                reply_markup=ReplyKeyboardMarkup([[btn("❌ Cancel", "danger")]], resize_keyboard=True),
                parse_mode="Markdown",
            )
            return
        if text in ("📋 List Channels", "🔵 📋 List Channels"):
            await handle_fc_list(update, ctx)
            return
        if text in ("🗑 Remove Channel", "🔴 🗑 Remove Channel"):
            await handle_fc_remove_prompt(update, ctx, uid)
            return
        if text in ("🔁 Enable/Disable Channel", "🔵 🔁 Enable/Disable Channel"):
            await handle_fc_toggle_prompt(update, ctx, uid)
            return
        if text in ("🔙 Back", "🔵 🔙 Back"):
            ADMIN_STATE.pop(uid, None)
            await update.message.reply_text(f"👑 {sc('ADMIN PANEL')}:", reply_markup=admin_menu())
            return

        # Point Settings Navigation
        if text in ("⚙️ POINT SETTINGS", sc("⚙️ POINT SETTINGS")):
            await handle_point_settings_menu(update)
            return
        if text == "💰 Rate: Pts per Rupee":
            ADMIN_STATE[uid] = {"step": "set_pts_rate"}
            await update.message.reply_text(
                f"💰 *Set Points Per Rupee Rate*\n\n"
                f"Current Rate: `1 INR = {format_pts(SETTINGS.get('points_per_rupee', 10.0))} Points`\n\n"
                "Send new points value per ₹1 (e.g. `10.0`):",
                parse_mode="Markdown",
                reply_markup=back_menu(),
            )
            return
        if text == "🏦 Set UPI ID":
            ADMIN_STATE[uid] = {"step": "set_upi_id"}
            await update.message.reply_text(
                f"🏦 *Set Admin UPI ID*\n\n"
                f"Current UPI ID: `{SETTINGS.get('upi_id', 'pawank63599-1@okicici')}`\n\n"
                "Send new UPI VPA / ID (e.g. `yourname@oksbi`):",
                parse_mode="Markdown",
                reply_markup=back_menu(),
            )
            return
        if text == "🎰 Spin Min Points":
            ADMIN_STATE[uid] = {"step": "set_spin_min"}
            await update.message.reply_text(
                f"🎰 *Set Daily Spin Minimum Points*\n\nCurrent: `{format_pts(SETTINGS['spin_min'])}` Points\n\n"
                "Send new minimum points (e.g. `0.5`):",
                parse_mode="Markdown",
                reply_markup=back_menu(),
            )
            return
        if text == "🎰 Spin Max Points":
            ADMIN_STATE[uid] = {"step": "set_spin_max"}
            await update.message.reply_text(
                f"🎰 *Set Daily Spin Maximum Points*\n\nCurrent: `{format_pts(SETTINGS['spin_max'])}` Points\n\n"
                "Send new maximum points (e.g. `2.0`):",
                parse_mode="Markdown",
                reply_markup=back_menu(),
            )
            return
        if text == "🎁 Free Search Limit":
            ADMIN_STATE[uid] = {"step": "set_free_limit"}
            await update.message.reply_text(
                f"🎁 *Set Free Search Limit*\n\nCurrent: `{SETTINGS['free_search_limit']}` Searches\n\n"
                "Send new free search count (e.g. `2`):",
                parse_mode="Markdown",
                reply_markup=back_menu(),
            )
            return
        if text == "🔗 Referral Reward":
            ADMIN_STATE[uid] = {"step": "set_ref_reward"}
            await update.message.reply_text(
                f"🔗 *Set Referral Reward Points*\n\nCurrent: `{format_pts(SETTINGS['referral_reward'])}` Points\n\n"
                "Send new referral reward points (e.g. `10.0`):",
                parse_mode="Markdown",
                reply_markup=back_menu(),
            )
            return
        if text == "🔍 Normal Search Cost":
            ADMIN_STATE[uid] = {"step": "set_normal_cost"}
            await update.message.reply_text(
                f"🔍 *Set Normal Search Cost*\n\nCurrent: `{format_pts(SETTINGS['normal_search_cost'])}` Points\n\n"
                "Send new cost per search (e.g. `1.5`):",
                parse_mode="Markdown",
                reply_markup=back_menu(),
            )
            return
        if text == "✈️ TG Search Cost":
            ADMIN_STATE[uid] = {"step": "set_tg_cost"}
            await update.message.reply_text(
                f"✈️ *Set Telegram Search Cost*\n\nCurrent: `{format_pts(SETTINGS['tg_search_cost'])}` Points\n\n"
                "Send new cost for Telegram lookups (e.g. `2.0`):",
                parse_mode="Markdown",
                reply_markup=back_menu(),
            )
            return
        if text == "📊 View Current Settings":
            await handle_point_settings_menu(update)
            return
        if text == "🔙 Back to Admin":
            ADMIN_STATE.pop(uid, None)
            await update.message.reply_text(f"👑 {sc('ADMIN PANEL')}:", reply_markup=admin_menu())
            return

    # Admin panel navigation
    if is_admin(uid) and text in ADMIN_LABELS:
        await handle_admin_label(update, ctx, uid, ADMIN_LABELS[text])
        return

    if text in ("⬅️ Cancel", CANCEL_LABEL, sc("⬅️ CANCEL")):
        ctx.user_data.pop("awaiting", None)
        ctx.user_data.pop("pending_recharge", None)
        await update.message.reply_text("Cancelled.", reply_markup=main_menu(uid))
        return

    # Daily Spin Button
    if "Daily Spin" in text or "Spin" in text:
        now = time.time()
        last = LAST_SPIN.get(uid, 0)
        cooldown = 24 * 3600

        if now - last < cooldown:
            rem = int(cooldown - (now - last))
            hours = rem // 3600
            mins = (rem % 3600) // 60
            await update.message.reply_markdown(
                "⏳ *DAILY SPIN COOLDOWN*\n\n"
                "Aapne aaj ka Daily Spin pehle hi use kar liya hai!\n"
                f"⏰ Agla spin available hoga: *{hours}h {mins}m* baad.\n\n"
                f"💰 Current Balance: `{format_pts(CREDITS.get(uid, 0.0))}` Points",
                reply_markup=main_menu(uid),
            )
            return

        # Animated Slot Machine rolling
        slot_msg = await update.message.reply_markdown(
            "🎰 *SPINNING LUCKY WHEEL...*\n\n"
            "`[ 🍒 | 🍋 | 7️⃣ ]`\n"
            "_Rolling the reels..._"
        )
        await asyncio.sleep(0.5)
        await slot_msg.edit_text(
            "🎰 *SPINNING LUCKY WHEEL...*\n\n"
            "`[ 💎 | 🍒 | 💎 ]`\n"
            "_Almost there..._",
            parse_mode="Markdown"
        )
        await asyncio.sleep(0.5)

        # Random reward strictly within configured min & max
        s_min = float(SETTINGS.get("spin_min", 0.5))
        s_max = float(SETTINGS.get("spin_max", 2.0))
        if s_min >= s_max:
            reward = s_min
        else:
            reward = round(random.uniform(s_min, s_max), 1)

        CREDITS[uid] = round(CREDITS.get(uid, 0.0) + reward, 2)
        LAST_SPIN[uid] = now
        await save_state(ctx)

        jackpot_card = (
            "╔══════════════════════════════╗\n"
            "║   🎰 *LUCKY WHEEL JACKPOT* 🎰  ║\n"
            "╚══════════════════════════════╝\n"
            "🎉 *Reels:* `[ 💎 | 💎 | 💎 ]`\n\n"
            "🏆 *CONGRATULATIONS!*\n"
            f"Aapne jeete hain: *+{format_pts(reward)} FREE Points*! 🎁\n\n"
            f"💰 *Aapka Naya Balance:* `{format_pts(CREDITS[uid])}` Points\n"
            "⏰ *Next Spin:* 24 ghante baad dubara aana!"
        )
        await slot_msg.edit_text(jackpot_card, parse_mode="Markdown")
        return

    # User Profile Button (VIP Badge & Progress Bar)
    if "Profile" in text:
        total_refs = len(REFERRALS.get(uid, []))
        free_used = USER_SEARCHES.get(uid, 0)
        free_limit = SETTINGS.get("free_search_limit", 2)
        free_rem = max(0, free_limit - free_used)
        credits_bal = float(CREDITS.get(uid, 0.0))

        # VIP Rank & Progress Bar
        if is_admin(uid):
            badge = "👑 SUPREME ADMIN"
            prog_bar = "[██████████] 100% (GOD MODE)"
        elif credits_bal >= 100 or total_refs >= 20:
            badge = "🥇 GOLD ELITE VIP"
            prog_bar = "[██████████] 100% (MAX RANK)"
        elif credits_bal >= 30 or total_refs >= 5:
            badge = "🥈 SILVER VIP"
            pct = min(99, int((credits_bal / 100) * 100))
            filled = int(pct / 10)
            prog_bar = f"[{'█' * filled}{'░' * (10 - filled)}] {pct}% to Gold"
        else:
            badge = "🥉 BRONZE MEMBER"
            pct = min(99, int((credits_bal / 30) * 100))
            filled = int(pct / 10)
            prog_bar = f"[{'█' * filled}{'░' * (10 - filled)}] {pct}% to Silver"

        profile_card = (
            "╔══════════════════════════════╗\n"
            "║     👑 *USER VIP PROFILE*     ║\n"
            "╚══════════════════════════════╝\n"
            f"👤 *Name:* `{u.full_name}`\n"
            f"🆔 *User ID:* `{uid}`\n"
            f"🏷️ *Username:* `@{u.username or 'None'}`\n"
            f"🎖️ *VIP Rank:* `{badge}`\n"
            f"💰 *Balance:* `{format_pts(credits_bal)}` Points\n"
            f"🎁 *Free Searches:* `{free_rem}/{free_limit}` Left\n"
            f"👥 *Total Referrals:* `{total_refs}` Friends\n"
            f"📊 *Rank Progress:* `{prog_bar}`\n"
            "──────────────────────────────\n"
            "⚡ *Status:* Active & Verified ✅"
        )
        await update.message.reply_markdown(profile_card, reply_markup=main_menu(uid))
        return

    # Leaderboard Button
    if "Leaderboard" in text:
        sorted_users = sorted(
            list(USERS),
            key=lambda x: len(REFERRALS.get(x, [])),
            reverse=True,
        )

        lines = [
            "╔══════════════════════════════╗\n"
            "║   🏆 *TOP REFERRAL CHAMPIONS* ║\n"
            "╚══════════════════════════════╝\n"
        ]

        medals = ["🥇", "🥈", "🥉"]
        for idx, u_id in enumerate(sorted_users[:10], 1):
            refs_count = len(REFERRALS.get(u_id, []))
            medal = medals[idx - 1] if idx <= 3 else f"#{idx}."
            is_me = " *(YOU)*" if u_id == uid else ""
            lines.append(f"{medal} `User {str(u_id)[-4:]}` — *{refs_count} Referrals*{is_me}")

        try:
            my_idx = sorted_users.index(uid) + 1
        except ValueError:
            my_idx = len(sorted_users)

        my_refs = len(REFERRALS.get(uid, []))
        lines.append("──────────────────────────────")
        lines.append(f"👤 *Aapki Rank:* `#{my_idx}` (*{my_refs} Referrals*)")
        lines.append("🔗 Dosto ko invite karne ke liye *REFERRAL* button use karein!")

        await update.message.reply_markdown("\n".join(lines), reply_markup=main_menu(uid))
        return

    # User Support Button
    if "Support" in text:
        support_text = (
            "🆘 *SUPPORT & HELP*\n\n"
            "Kisi bhi problem, inquiry ya credit buy karne ke liye Admin se contact karein:\n"
            f"👉 [Admin Support](tg://user?id={ADMIN_ID})"
        )
        await update.message.reply_markdown(support_text, reply_markup=main_menu(uid))
        return

    # Referral Link Button
    if "REFERRAL" in text or "Referral" in text:
        me = await ctx.bot.get_me()
        bot_uname = me.username
        ref_link = f"https://t.me/{bot_uname}?start=ref_{uid}"
        total_refs = len(REFERRALS.get(uid, []))
        ref_pts = format_pts(SETTINGS.get("referral_reward", 10.0))
        ref_text = (
            "🔗 *REFERRAL PROGRAM*\n\n"
            "Apne dosto ko bot refer karein aur FREE Points paayein!\n\n"
            f"👉 *Aapka Referral Link:*\n`{ref_link}`\n\n"
            f"🎁 Har valid referral par: *+{ref_pts} Points*\n"
            f"👥 Total Referrals: *{total_refs}*"
        )
        await update.message.reply_markdown(ref_text, reply_markup=main_menu(uid))
        return

    # Claim Gift Button
    if "CLAIM GIFT" in text or "Claim" in text or "Gift" in text:
        ctx.user_data["awaiting"] = "claim_gift"
        await update.message.reply_text("🎁 Send your gift code:", reply_markup=cancel_menu())
        return

    # My Credits Button
    if "My Credits" in text or "Credits" in text:
        free_used = USER_SEARCHES.get(uid, 0)
        free_limit = SETTINGS.get("free_search_limit", 2)
        free_rem = max(0, free_limit - free_used)
        await update.message.reply_text(
            f"💰 Points Balance: {format_pts(CREDITS.get(uid, 0.0))} Points\n"
            f"🎁 Free Searches Left: {free_rem}/{free_limit}",
            reply_markup=main_menu(uid),
        )
        return

    # Buy Points Button
    if "Buy Points" in text or text == "/buy":
        await start_buy_points(update.message, ctx, uid)
        return

    # Main menu tool buttons from screenshot
    if text in LABEL_TO_KEY:
        if MAINTENANCE and not is_admin(uid):
            await update.message.reply_text("🛠 Maintenance mode.")
            return
        if uid in BANNED:
            await update.message.reply_text("🚫 You are banned.")
            return
        key = LABEL_TO_KEY[text]
        ctx.user_data["awaiting"] = key
        label_name, _, prompt_msg = TOOLS[key]
        await update.message.reply_text(
            f"*{label_name}*\n\n{prompt_msg}",
            reply_markup=cancel_menu(),
            parse_mode="Markdown",
        )
        return

    # Awaiting input value
    key = ctx.user_data.get("awaiting")
    if not key:
        await update.message.reply_text("🤖 *TOXIC BOT READY*\n\nOption select karo:", reply_markup=main_menu(uid), parse_mode="Markdown")
        return
    if MAINTENANCE and not is_admin(uid):
        await update.message.reply_text("🛠 Maintenance mode.")
        return
    if uid in BANNED:
        await update.message.reply_text("🚫 You are banned.")
        return

    # Handle Buy Points packages, custom points and UTR submission
    if key == "buy_points_pack":
        if text in ("✏️ Custom Points", "Custom Points"):
            ctx.user_data["awaiting"] = "buy_points_custom"
            await update.message.reply_markdown(
                "✏️ *Custom Points Recharge*\n\n"
                "Aapko kitne Points buy karne hain? Send karein (e.g. `200`):",
                reply_markup=cancel_menu()
            )
            return

        pts_match = re.search(r"(\d+)\s*Pts", text) or re.search(r"(\d+)\s*Points", text)
        if pts_match:
            pts = float(pts_match.group(1))
            await generate_and_send_qr(update.message, ctx, uid, pts)
            return
        else:
            await update.message.reply_text("Please package choose karein ya Cancel dabayein.", reply_markup=buy_points_menu())
            return

    if key == "buy_points_custom":
        try:
            pts = float(text.strip())
            if pts <= 0:
                raise ValueError
        except Exception:
            await update.message.reply_text("❌ Invalid points amount. Please valid number bhejein (e.g. `100`):", reply_markup=cancel_menu())
            return
        await generate_and_send_qr(update.message, ctx, uid, pts)
        return

    if key in ("submit_screenshot", "submit_utr"):
        await update.message.reply_markdown(
            "📸 *Please Payment ka Screenshot (Photo) bhejein!*\n\n"
            "UPI app me payment successful hone ke baad uska screenshot lein aur yahan photo bhejein.\n"
            "Cancel karne ke liye neeche button dabayein:",
            reply_markup=cancel_menu()
        )
        return

    query = text
    if not query:
        await update.message.reply_text("Empty input.")
        return

    ctx.user_data.pop("awaiting", None)

    # Claim gift handling
    if key == "claim_gift":
        code = query.upper()
        g = GIFTS.get(code)
        if not g:
            await update.message.reply_text("❌ Invalid code.", reply_markup=main_menu(uid))
            return
        if uid in g["used"]:
            await update.message.reply_text("⚠️ Already redeemed.", reply_markup=main_menu(uid))
            return
        if len(g["used"]) >= g["max"]:
            await update.message.reply_text("⚠️ Code fully redeemed.", reply_markup=main_menu(uid))
            return
        g["used"].add(uid)
        CREDITS[uid] = round(CREDITS.get(uid, 0.0) + float(g["credits"]), 2)
        await save_state(ctx)
        await update.message.reply_text(f"✅ +{format_pts(g['credits'])} Points. Balance: {format_pts(CREDITS[uid])} Points", reply_markup=main_menu(uid))
        return

    # Check Free Searches & Cost Calculation
    free_used = USER_SEARCHES.get(uid, 0)
    free_limit = SETTINGS.get("free_search_limit", 2)
    is_free = (free_used < free_limit) and not is_admin(uid)

    cost = float(SETTINGS.get("tg_search_cost", 2.0)) if key == "tg" else float(SETTINGS.get("normal_search_cost", 1.5))

    # Balance check in advance (if not free and not admin)
    if not is_free and not is_admin(uid):
        user_bal = float(CREDITS.get(uid, 0.0))
        if user_bal < cost:
            await update.message.reply_text(
                f"❌ *Insufficient Points!*\n\n"
                f"Is search ke liye *{format_pts(cost)} Points* chahiye.\n"
                f"Aapka current balance: *{format_pts(user_bal)} Points*.\n\n"
                "🎁 Daily Spin karein, referral karein ya Admin se contact karein.",
                parse_mode="Markdown",
                reply_markup=main_menu(uid),
            )
            return

    await update.message.chat.send_action(ChatAction.TYPING)

    # Animated Search Simulation
    status_msg = await update.message.reply_markdown(
        "🔍 *Connecting to Toxic Database...*\n`[■■□□□□□□□□] 20%`"
    )
    await asyncio.sleep(0.4)
    await status_msg.edit_text(
        "⚡ *Bypassing Encryption & Querying...*\n`[■■■■■■□□□□] 60%`",
        parse_mode="Markdown"
    )

    try:
        if key in TOOLS:
            tool_api = TOOLS[key][1]
            tool_label = TOOLS[key][0]
            usage_key = key
            result = await call_lookup(tool_api, query)
        else:
            await status_msg.edit_text("❌ Unknown tool.", reply_markup=main_menu(uid))
            return
    except httpx.HTTPStatusError as e:
        await status_msg.edit_text(f"❌ API error {e.response.status_code}\n_(Points deduct nahi huye)_", parse_mode="Markdown", reply_markup=main_menu(uid))
        return
    except Exception as e:
        await status_msg.edit_text(f"❌ Error: {e}\n_(Points deduct nahi huye)_", parse_mode="Markdown", reply_markup=main_menu(uid))
        return

    # RULE 4: Result-Based Deduction. Only deduct if data is actually found!
    if not is_successful_result(result):
        await status_msg.edit_text(
            "⚠️ *No records or data found for this query.*\n\n"
            "ℹ️ _Data na milne par aapke koi points deduct nahi kiye gaye hain._",
            parse_mode="Markdown",
            reply_markup=main_menu(uid),
        )
        return

    # Data is found -> Apply deduction or free search consumption
    USAGE[usage_key] = USAGE.get(usage_key, 0) + 1

    if is_free:
        USER_SEARCHES[uid] = free_used + 1
        cost_note = f"0 Points (Free Search {free_used + 1}/{free_limit})"
        bal_note = f" (🎉 Free Search {free_used + 1}/{free_limit} Used! 0 Points Deducted)"
    elif not is_admin(uid):
        CREDITS[uid] = max(0.0, round(CREDITS.get(uid, 0.0) - cost, 2))
        cost_note = f"{format_pts(cost)} Points"
        bal_note = f" (-{format_pts(cost)} Points | Balance: {format_pts(CREDITS[uid])} Points)"
    else:
        cost_note = "0 Points (Admin)"
        bal_note = " (Admin)"

    header = (
        f"🗂 STORAGE LOG\nTool: {tool_label}\nQuery: {query}\n"
        f"User: {uid} (@{u.username or 'no_user'})\n"
        f"Name: {u.full_name}\n─────────────\n"
    )
    await log_to_channel(ctx, header + clean_result(result))
    await save_state(ctx)

    await status_msg.edit_text(
        "✅ *Data Decrypted! Formatting Cyber Card...*\n`[■■■■■■■■■■] 100%`",
        parse_mode="Markdown"
    )
    await asyncio.sleep(0.3)
    try:
        await status_msg.delete()
    except Exception:
        pass

    bot_info = await ctx.bot.get_me()
    await send_result(update.message, result, tool_label, query, cost_note, tool_key=key, bot_username=bot_info.username)
    await update.message.reply_text(f"🔎 Another lookup?{bal_note}", reply_markup=main_menu(uid))


# ---------------- POINT SETTINGS ADMIN HANDLER ----------------
async def handle_point_settings_menu(update: Update):
    summary = (
        "⚙️ *POINT & SEARCH SETTINGS*\n\n"
        f"• 💰 *Buy Rate:* `₹1.00 = {format_pts(SETTINGS.get('points_per_rupee', 10.0))}` Points\n"
        f"• 🏦 *Admin UPI ID:* `{SETTINGS.get('upi_id', 'pawank63599-1@okicici')}`\n"
        f"• 🎰 *Daily Spin Min:* `{format_pts(SETTINGS['spin_min'])}` Points\n"
        f"• 🎰 *Daily Spin Max:* `{format_pts(SETTINGS['spin_max'])}` Points\n"
        f"• 🎁 *Free Search Limit:* `{SETTINGS['free_search_limit']}` Searches\n"
        f"• 🔍 *Normal Search Cost:* `{format_pts(SETTINGS['normal_search_cost'])}` Points\n"
        f"• ✈️ *TG Search Cost:* `{format_pts(SETTINGS['tg_search_cost'])}` Points\n"
        f"• 🔗 *Referral Reward:* `{format_pts(SETTINGS['referral_reward'])}` Points\n\n"
        "Neeche diye buttons se koi bhi value configure karein:"
    )
    await update.message.reply_text(summary, parse_mode="Markdown", reply_markup=point_settings_menu())


# ---------------- FORCE CHANNELS ADMIN HANDLERS ----------------
async def handle_fc_list(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return

    if not FORCE_CHANNELS:
        await update.message.reply_text(
            "📋 *FORCE CHANNELS*\n\nNo force join channels configured yet.",
            parse_mode="Markdown",
            reply_markup=force_channels_menu(),
        )
        return

    lines = ["📋 *FORCE CHANNELS*\n"]
    for cid, ch in FORCE_CHANNELS.items():
        status_icon = "✅" if ch.get("enabled", True) else "🚫"
        safe_title = str(ch.get('channel_title', 'Channel')).replace('*', '').replace('_', ' ')
        lines.append(
            f"{status_icon} *{safe_title}*\n"
            f"Type: {ch.get('channel_type', 'Public')}\n"
            f"ID: `{cid}`\n"
            f"Link: {ch.get('join_url', 'None')}\n"
        )

    await update.message.reply_text(
        "\n".join(lines),
        parse_mode="Markdown",
        disable_web_page_preview=True,
        reply_markup=force_channels_menu(),
    )


async def handle_fc_remove_prompt(update: Update, ctx: ContextTypes.DEFAULT_TYPE, uid: int):
    if not is_admin(uid):
        return

    if not FORCE_CHANNELS:
        await update.message.reply_text(
            "No force channels configured to remove.",
            reply_markup=force_channels_menu(),
        )
        return

    buttons = []
    for cid, ch in FORCE_CHANNELS.items():
        buttons.append([btn(f"🗑 {ch['channel_title']} ({cid})", "danger")])
    buttons.append([btn("❌ Cancel", "danger")])

    ADMIN_STATE[uid] = {"step": "fc_remove_select"}
    await update.message.reply_text(
        "🗑 *Remove Channel*\n\nSelect a channel to remove or send its numeric Chat ID:",
        reply_markup=ReplyKeyboardMarkup(buttons, resize_keyboard=True),
        parse_mode="Markdown",
    )


async def handle_fc_toggle_prompt(update: Update, ctx: ContextTypes.DEFAULT_TYPE, uid: int):
    if not is_admin(uid):
        return

    if not FORCE_CHANNELS:
        await update.message.reply_text(
            "No force channels configured.",
            reply_markup=force_channels_menu(),
        )
        return

    buttons = []
    for cid, ch in FORCE_CHANNELS.items():
        status_icon = "✅" if ch.get("enabled", True) else "🚫"
        buttons.append([btn(f"{status_icon} {ch['channel_title']} ({cid})", "primary")])
    buttons.append([btn("🔙 Back", "primary")])

    ADMIN_STATE[uid] = {"step": "fc_toggle"}
    await update.message.reply_text(
        "🔁 *Enable/Disable Channel*\n\nSelect a channel below to toggle its status:",
        reply_markup=ReplyKeyboardMarkup(buttons, resize_keyboard=True),
        parse_mode="Markdown",
    )


# ---------------- ADMIN PANEL ----------------
async def admin_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_admin(uid):
        await update.message.reply_text("🚫 Admins only.")
        return
    ADMIN_STATE.pop(uid, None)
    await update.message.reply_text(
        f"👑 *{sc('ADMIN PANEL')}*\n\n{sc('Use the keyboard below')}:",
        reply_markup=admin_menu(), parse_mode="Markdown",
    )


async def handle_admin_label(update: Update, ctx, uid: int, action: str):
    if action == "adm_point_settings":
        await handle_point_settings_menu(update)
        return
    if action == "adm_force_channels":
        await update.message.reply_text("📢 *FORCE CHANNELS*", reply_markup=force_channels_menu(), parse_mode="Markdown")
        return
    if action == "adm_ban":
        ADMIN_STATE[uid] = {"step": "ban"}
        await update.message.reply_text(f"🚫 {sc('Send user ID to BAN')}:", reply_markup=back_menu())
    elif action == "adm_unban":
        ADMIN_STATE[uid] = {"step": "unban"}
        await update.message.reply_text(f"✅ {sc('Send user ID to UNBAN')}:", reply_markup=back_menu())
    elif action == "adm_unbanall":
        BANNED.clear()
        await save_state(ctx)
        await update.message.reply_text(f"✅ {sc('All users unbanned')}.", reply_markup=admin_menu())
    elif action == "adm_bannedlist":
        if not BANNED:
            await update.message.reply_text(f"{sc('No banned users')}.", reply_markup=admin_menu())
        else:
            txt = f"📋 *{sc('BANNED USERS')}*\n\n" + "\n".join(str(x) for x in sorted(BANNED))
            await update.message.reply_text(txt, parse_mode="Markdown", reply_markup=admin_menu())
    elif action == "adm_addcredit":
        ADMIN_STATE[uid] = {"step": "addcredit"}
        await update.message.reply_text(f"➕ {sc('Send')}: USER_ID AMOUNT\n{sc('Example')}: 123456789 50.5", reply_markup=back_menu())
    elif action == "adm_rmcredit":
        ADMIN_STATE[uid] = {"step": "rmcredit"}
        await update.message.reply_text(f"➖ {sc('Send')}: USER_ID AMOUNT\n{sc('Example')}: 123456789 20", reply_markup=back_menu())
    elif action == "adm_setcredit":
        ADMIN_STATE[uid] = {"step": "setcredit"}
        await update.message.reply_text(f"⚙️ {sc('Send')}: USER_ID AMOUNT\n{sc('Example')}: 123456789 100", reply_markup=back_menu())
    elif action == "adm_checkcredit":
        ADMIN_STATE[uid] = {"step": "checkcredit"}
        await update.message.reply_text(f"💰 {sc('Send user ID')}:", reply_markup=back_menu())
    elif action == "adm_creategift":
        ADMIN_STATE[uid] = {"step": "creategift"}
        await update.message.reply_text(f"🎁 {sc('Send')}: CREDITS MAX_USERS\n{sc('Example')}: 50 10", reply_markup=back_menu())
    elif action == "adm_giftlist":
        if not GIFTS:
            await update.message.reply_text(f"{sc('No gift codes')}.", reply_markup=admin_menu())
        else:
            txt = f"📋 *{sc('GIFT CODES')}*\n\n"
            for c, g in GIFTS.items():
                txt += f"`{c}` — {format_pts(g['credits'])} pts, used {len(g['used'])}/{g['max']}\n"
            txt += f"\n{sc('To delete a code, send its code here')}:"
            ADMIN_STATE[uid] = {"step": "delgift"}
            await update.message.reply_text(txt, parse_mode="Markdown", reply_markup=back_menu())
    elif action == "adm_broadcast":
        ADMIN_STATE[uid] = {"step": "broadcast"}
        await update.message.reply_text(f"📢 {sc('Send the broadcast message')}:", reply_markup=back_menu())
    elif action == "adm_userlist":
        txt = f"👥 *{sc('USERS')}* ({len(USERS)})\n\n"
        txt += "\n".join(str(u) for u in sorted(USERS)[:200])
        if len(USERS) > 200:
            txt += f"\n... +{len(USERS)-200} more"
        await update.message.reply_text(txt, parse_mode="Markdown", reply_markup=admin_menu())
    elif action == "adm_apis":
        txt = f"*🔧 {sc('CUSTOM APIs')}*\n\n"
        if CUSTOM_APIS:
            for i, a in enumerate(CUSTOM_APIS):
                txt += f"{i+1}. {a['emoji']} {sc(a['name'])}\n`{a['url']}`\n"
            txt += f"\n{sc('Add')}: NAME | URL_WITH_{{value}} | EMOJI\n{sc('Delete')}: DEL N"
        else:
            txt += f"_{sc('None yet')}._\n\n{sc('Add')}: NAME | URL_WITH_{{value}} | EMOJI"
        ADMIN_STATE[uid] = {"step": "addapi"}
        await update.message.reply_text(txt, parse_mode="Markdown", reply_markup=back_menu())
    elif action == "adm_clearapis":
        CUSTOM_APIS.clear()
        await save_state(ctx)
        await update.message.reply_text(f"✅ {sc('Cleared')}.", reply_markup=admin_menu())
    elif action == "adm_maint":
        global MAINTENANCE
        MAINTENANCE = not MAINTENANCE
        await save_state(ctx)
        status = sc("ON 🛠") if MAINTENANCE else sc("OFF ✅")
        await update.message.reply_text(f"{sc('Maintenance')}: {status}", reply_markup=admin_menu())
    elif action == "adm_stats":
        txt = (
            f"📊 *{sc('STATS')}*\n\n"
            f"{sc('Users')}: {len(USERS)}\n"
            f"{sc('Banned')}: {len(BANNED)}\n"
            f"{sc('Gifts')}: {len(GIFTS)}\n"
            f"{sc('Custom APIs')}: {len(CUSTOM_APIS)}\n"
            f"{sc('Force Channels')}: {len(FORCE_CHANNELS)}\n"
            f"{sc('Maintenance')}: {'ON' if MAINTENANCE else 'OFF'}\n"
        )
        if USAGE:
            txt += f"\n*{sc('Tool Usage')}*\n"
            for k, v in sorted(USAGE.items(), key=lambda x: -x[1]):
                txt += f"{k}: {v}\n"
        await update.message.reply_text(txt, parse_mode="Markdown", reply_markup=admin_menu())
    elif action == "adm_save":
        await save_state(ctx)
        await update.message.reply_text(f"💾 {sc('State saved to channel')}.", reply_markup=admin_menu())
    elif action == "adm_back":
        ADMIN_STATE.pop(uid, None)
        await update.message.reply_text(f"👑 {sc('ADMIN PANEL')}:", reply_markup=admin_menu())
    elif action == "adm_mainmenu":
        ADMIN_STATE.pop(uid, None)
        await update.message.reply_text("🤖 *TOXIC BOT READY*\n\nOption select karo:", reply_markup=main_menu(uid), parse_mode="Markdown")
    elif action == "adm_panel":
        await admin_cmd(update, ctx)
        return


async def handle_admin_input(update: Update, ctx, uid: int, txt: str):
    state = ADMIN_STATE.get(uid)
    if not state:
        return
    step = state["step"]

    # Universal Cancel / Back handler for all Admin wizards
    if txt in ("🔙 Back", "Cancel", "⬅️ Cancel", "❌ Cancel", "/cancel", "🔙 Back to Admin", CANCEL_LABEL, BACK_LABEL):
        ADMIN_STATE.pop(uid, None)
        if step.startswith("set_"):
            await handle_point_settings_menu(update)
        elif step.startswith("fc_"):
            await update.message.reply_text("Cancelled.", reply_markup=force_channels_menu())
        else:
            await update.message.reply_text("Action cancelled.", reply_markup=admin_menu())
        return

    try:
        # ==================== POINT SETTINGS WIZARDS ====================
        if step == "set_spin_min":
            val = float(txt.strip())
            SETTINGS["spin_min"] = val
            await save_state(ctx)
            ADMIN_STATE.pop(uid, None)
            await update.message.reply_text(f"✅ Daily Spin Minimum Points set to: `{format_pts(val)}`", parse_mode="Markdown")
            await handle_point_settings_menu(update)
            return

        elif step == "set_spin_max":
            val = float(txt.strip())
            SETTINGS["spin_max"] = val
            await save_state(ctx)
            ADMIN_STATE.pop(uid, None)
            await update.message.reply_text(f"✅ Daily Spin Maximum Points set to: `{format_pts(val)}`", parse_mode="Markdown")
            await handle_point_settings_menu(update)
            return

        elif step == "set_free_limit":
            val = int(txt.strip())
            SETTINGS["free_search_limit"] = val
            await save_state(ctx)
            ADMIN_STATE.pop(uid, None)
            await update.message.reply_text(f"✅ Free Search Limit set to: `{val}` searches", parse_mode="Markdown")
            await handle_point_settings_menu(update)
            return

        elif step == "set_ref_reward":
            val = float(txt.strip())
            SETTINGS["referral_reward"] = val
            await save_state(ctx)
            ADMIN_STATE.pop(uid, None)
            await update.message.reply_text(f"✅ Referral Reward set to: `{format_pts(val)}` Points", parse_mode="Markdown")
            await handle_point_settings_menu(update)
            return

        elif step == "set_normal_cost":
            val = float(txt.strip())
            SETTINGS["normal_search_cost"] = val
            await save_state(ctx)
            ADMIN_STATE.pop(uid, None)
            await update.message.reply_text(f"✅ Normal Search Cost set to: `{format_pts(val)}` Points", parse_mode="Markdown")
            await handle_point_settings_menu(update)
            return

        elif step == "set_tg_cost":
            val = float(txt.strip())
            SETTINGS["tg_search_cost"] = val
            await save_state(ctx)
            ADMIN_STATE.pop(uid, None)
            await update.message.reply_text(f"✅ Telegram Search Cost set to: `{format_pts(val)}` Points", parse_mode="Markdown")
            await handle_point_settings_menu(update)
            return

        elif step == "set_pts_rate":
            val = float(txt.strip())
            if val <= 0:
                await update.message.reply_text("❌ Rate must be greater than 0.")
                return
            SETTINGS["points_per_rupee"] = val
            await save_state(ctx)
            ADMIN_STATE.pop(uid, None)
            await update.message.reply_text(f"✅ Rate set to: `₹1.00 = {format_pts(val)} Points`", parse_mode="Markdown")
            await handle_point_settings_menu(update)
            return

        elif step == "set_upi_id":
            val = txt.strip()
            if "@" not in val:
                await update.message.reply_text("❌ Please enter a valid UPI ID (e.g. `username@bank`).")
                return
            SETTINGS["upi_id"] = val
            await save_state(ctx)
            ADMIN_STATE.pop(uid, None)
            await update.message.reply_text(f"✅ UPI ID set to: `{val}`", parse_mode="Markdown")
            await handle_point_settings_menu(update)
            return

        # ==================== FORCE CHANNELS WIZARDS ====================
        elif step == "fc_add_ref":
            if txt in ("❌ Cancel", "Cancel", "🔴 Cancel", "⬅️ Cancel"):
                ADMIN_STATE.pop(uid, None)
                await update.message.reply_text("Cancelled.", reply_markup=force_channels_menu())
                return

            cid, title, uname = None, None, None
            if update.message.forward_origin and hasattr(update.message.forward_origin, "chat"):
                chat = update.message.forward_origin.chat
                cid = chat.id
                title = chat.title or str(cid)
                uname = getattr(chat, "username", None)
            elif update.message.forward_from_chat:
                chat = update.message.forward_from_chat
                cid = chat.id
                title = chat.title or str(cid)
                uname = getattr(chat, "username", None)
            else:
                ref = txt.strip()
                if "t.me/" in ref:
                    clean_ref = ref.split("t.me/")[-1].split("/")[0].split("?")[0].lstrip("@")
                    target = f"@{clean_ref}"
                elif ref.lstrip("-").isdigit():
                    target = int(ref)
                elif not ref.startswith("@"):
                    target = f"@{ref}"
                else:
                    target = ref
                try:
                    chat = await ctx.bot.get_chat(target)
                    cid = chat.id
                    title = chat.title or str(cid)
                    uname = getattr(chat, "username", None)
                except Exception as e:
                    await update.message.reply_text(
                        f"❌ Could not resolve channel: {e}\n"
                        "Please ensure the bot is added as an Administrator to the channel.\n\n"
                        "Try again or click Cancel:",
                        reply_markup=ReplyKeyboardMarkup([[btn("❌ Cancel", "danger")]], resize_keyboard=True),
                    )
                    return

            if cid in FORCE_CHANNELS:
                ADMIN_STATE.pop(uid, None)
                await update.message.reply_text(
                    f"⚠️ Channel *{title}* (`{cid}`) is already added!",
                    parse_mode="Markdown",
                    reply_markup=force_channels_menu(),
                )
                return

            ADMIN_STATE[uid] = {
                "step": "fc_add_type",
                "channel_id": cid,
                "channel_title": title,
                "channel_username": uname,
            }

            type_keyboard = ReplyKeyboardMarkup(
                [
                    [btn("⚪ Public", "primary"), btn("⚪ Private", "primary")],
                    [btn("⚪ Join Request", "primary")],
                    [btn("❌ Cancel", "danger")],
                ],
                resize_keyboard=True,
            )
            await update.message.reply_text(
                f"Selected: *{title}* (`{cid}`)\n\n"
                "Step 2: Choose Channel Type:",
                parse_mode="Markdown",
                reply_markup=type_keyboard,
            )
            return

        elif step == "fc_add_type":
            if txt in ("❌ Cancel", "Cancel", "🔴 Cancel"):
                ADMIN_STATE.pop(uid, None)
                await update.message.reply_text("Cancelled.", reply_markup=force_channels_menu())
                return

            clean_type = txt.replace("⚪", "").strip()
            if clean_type not in ("Public", "Private", "Join Request"):
                await update.message.reply_text("❌ Please select a valid channel type from the keyboard.")
                return

            state["channel_type"] = clean_type
            state["step"] = "fc_add_url"

            hint = "e.g. `https://t.me/channel`" if clean_type == "Public" else "e.g. `https://t.me/+invitehash`"
            await update.message.reply_text(
                f"Type: *{clean_type}*\n\n"
                f"Step 3: Please send the Join URL for this channel ({hint}):",
                parse_mode="Markdown",
                reply_markup=ReplyKeyboardMarkup([[btn("❌ Cancel", "danger")]], resize_keyboard=True),
            )
            return

        elif step == "fc_add_url":
            if txt in ("❌ Cancel", "Cancel", "🔴 Cancel"):
                ADMIN_STATE.pop(uid, None)
                await update.message.reply_text("Cancelled.", reply_markup=force_channels_menu())
                return

            join_url = txt.strip()
            ctype = state["channel_type"]

            if ctype == "Public":
                if not join_url.startswith("http://") and not join_url.startswith("https://"):
                    clean_u = join_url.lstrip("@").strip()
                    join_url = f"https://t.me/{clean_u}"
            else:
                if not ("t.me/" in join_url or join_url.startswith("https://")):
                    await update.message.reply_text("❌ Please enter a valid Telegram invite URL (e.g. `https://t.me/...`):")
                    return

            cid = state["channel_id"]
            title = state["channel_title"]
            uname = state.get("channel_username")
            now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")

            FORCE_CHANNELS[cid] = {
                "channel_id": cid,
                "channel_title": title,
                "channel_username": uname,
                "channel_type": ctype,
                "join_url": join_url,
                "enabled": True,
                "added_time": now_str,
            }

            await log_admin_action(ctx, uid, cid, title, ctype, "ADD_CHANNEL")
            await save_state(ctx)
            ADMIN_STATE.pop(uid, None)

            await update.message.reply_text(
                "✅ Force Join channel added successfully.",
                reply_markup=force_channels_menu(),
            )
            return

        elif step == "fc_remove_select":
            if txt in ("❌ Cancel", "Cancel", "🔴 Cancel"):
                ADMIN_STATE.pop(uid, None)
                await update.message.reply_text("Cancelled.", reply_markup=force_channels_menu())
                return

            target_cid = None
            if "(" in txt and ")" in txt:
                try:
                    raw_id = txt.split("(")[-1].split(")")[0].strip()
                    target_cid = int(raw_id)
                except Exception:
                    pass
            elif txt.lstrip("-").isdigit():
                target_cid = int(txt.strip())

            if not target_cid or target_cid not in FORCE_CHANNELS:
                await update.message.reply_text("❌ Channel not found. Please select from keyboard or click Cancel.")
                return

            ch = FORCE_CHANNELS[target_cid]
            ADMIN_STATE[uid] = {
                "step": "fc_remove_confirm",
                "channel_id": target_cid,
            }

            confirm_keyboard = ReplyKeyboardMarkup(
                [
                    [btn("🟢 Confirm Remove", "success")],
                    [btn("🔴 Cancel", "danger")],
                ],
                resize_keyboard=True,
            )
            await update.message.reply_text(
                f"Are you sure you want to remove:\n\n"
                f"*{ch['channel_title']}* (`{target_cid}`)?",
                parse_mode="Markdown",
                reply_markup=confirm_keyboard,
            )
            return

        elif step == "fc_remove_confirm":
            if txt in ("🔴 Cancel", "Cancel", "❌ Cancel"):
                ADMIN_STATE.pop(uid, None)
                await update.message.reply_text("Cancelled.", reply_markup=force_channels_menu())
                return

            if txt in ("🟢 Confirm Remove", "Confirm Remove"):
                cid = state["channel_id"]
                ch = FORCE_CHANNELS.pop(cid, None)
                if ch:
                    await log_admin_action(ctx, uid, cid, ch["channel_title"], ch["channel_type"], "REMOVE_CHANNEL")
                    await save_state(ctx)

                ADMIN_STATE.pop(uid, None)
                await update.message.reply_text(
                    "✅ Force Join channel removed successfully.",
                    reply_markup=force_channels_menu(),
                )
                return
            else:
                await update.message.reply_text("Please click '🟢 Confirm Remove' or '🔴 Cancel'.")
                return

        elif step == "fc_toggle":
            if txt in ("🔙 Back", "Back", "❌ Cancel", "Cancel"):
                ADMIN_STATE.pop(uid, None)
                await update.message.reply_text("📢 *FORCE CHANNELS*", reply_markup=force_channels_menu(), parse_mode="Markdown")
                return

            target_cid = None
            if "(" in txt and ")" in txt:
                try:
                    raw_id = txt.split("(")[-1].split(")")[0].strip()
                    target_cid = int(raw_id)
                except Exception:
                    pass
            elif txt.lstrip("-").isdigit():
                target_cid = int(txt.strip())

            if not target_cid or target_cid not in FORCE_CHANNELS:
                await update.message.reply_text("❌ Channel not found. Please select from the keyboard:")
                return

            ch = FORCE_CHANNELS[target_cid]
            old_status = "✅ Enabled" if ch.get("enabled", True) else "🚫 Disabled"
            new_val = not ch.get("enabled", True)
            ch["enabled"] = new_val
            new_status = "✅ Enabled" if new_val else "🚫 Disabled"

            await log_admin_action(ctx, uid, target_cid, ch["channel_title"], ch["channel_type"], f"TOGGLE_{new_status}")
            await save_state(ctx)

            await update.message.reply_text(
                f"{old_status} {ch['channel_title']} → {new_status}"
            )
            await handle_fc_toggle_prompt(update, ctx, uid)
            return

        # ==================== EXISTING ADMIN ACTIONS ====================
        elif step == "ban":
            target = int(txt)
            BANNED.add(target)
            await save_state(ctx)
            ADMIN_STATE.pop(uid, None)
            await update.message.reply_text(f"✅ Banned {target}", reply_markup=admin_menu())
        elif step == "unban":
            target = int(txt)
            BANNED.discard(target)
            await save_state(ctx)
            ADMIN_STATE.pop(uid, None)
            await update.message.reply_text(f"✅ Unbanned {target}", reply_markup=admin_menu())
        elif step == "addcredit":
            parts = txt.split()
            target, amt = int(parts[0]), float(parts[1])
            CREDITS[target] = round(CREDITS.get(target, 0.0) + amt, 2)
            await save_state(ctx)
            ADMIN_STATE.pop(uid, None)
            await update.message.reply_text(f"✅ {target} balance: {format_pts(CREDITS[target])} Points", reply_markup=admin_menu())
        elif step == "rmcredit":
            parts = txt.split()
            target, amt = int(parts[0]), float(parts[1])
            CREDITS[target] = max(0.0, round(CREDITS.get(target, 0.0) - amt, 2))
            await save_state(ctx)
            ADMIN_STATE.pop(uid, None)
            await update.message.reply_text(f"✅ {target} balance: {format_pts(CREDITS[target])} Points", reply_markup=admin_menu())
        elif step == "setcredit":
            parts = txt.split()
            target, amt = int(parts[0]), float(parts[1])
            CREDITS[target] = max(0.0, round(amt, 2))
            await save_state(ctx)
            ADMIN_STATE.pop(uid, None)
            await update.message.reply_text(f"✅ {target} balance set to {format_pts(CREDITS[target])} Points", reply_markup=admin_menu())
        elif step == "checkcredit":
            target = int(txt)
            ADMIN_STATE.pop(uid, None)
            await update.message.reply_text(f"💰 {target}: {format_pts(CREDITS.get(target, 0.0))} Points", reply_markup=admin_menu())
        elif step == "creategift":
            parts = txt.split()
            credits, maxu = float(parts[0]), int(parts[1])
            code = f"GIFT{int(time.time())%100000}"
            GIFTS[code] = {"credits": credits, "max": maxu, "used": set()}
            await save_state(ctx)
            ADMIN_STATE.pop(uid, None)
            await update.message.reply_text(
                f"✅ Code: `{code}`\nCredits: {format_pts(credits)}, Max: {maxu}",
                parse_mode="Markdown", reply_markup=admin_menu(),
            )
        elif step == "delgift":
            code = txt.upper()
            if code in GIFTS:
                del GIFTS[code]
                await save_state(ctx)
                ADMIN_STATE.pop(uid, None)
                await update.message.reply_text(f"✅ Deleted {code}", reply_markup=admin_menu())
            else:
                await update.message.reply_text("❌ Code not found. Enter valid code or click BACK:", reply_markup=back_menu())
        elif step == "broadcast":
            if txt in ("⬅️ Cancel", "Cancel", "🔙 Back", "Back", "🔴 Cancel"):
                ADMIN_STATE.pop(uid, None)
                await update.message.reply_text("Broadcast cancelled.", reply_markup=admin_menu())
                return
            sent, failed = 0, 0
            ADMIN_STATE.pop(uid, None)
            status_msg = await update.message.reply_text("📢 Broadcast shuru ho raha hai...")
            for u in list(USERS):
                try:
                    await ctx.bot.send_message(chat_id=u, text=txt)
                    sent += 1
                    await asyncio.sleep(0.04)
                except RetryAfter as e:
                    await asyncio.sleep(e.retry_after)
                    try:
                        await ctx.bot.send_message(chat_id=u, text=txt)
                        sent += 1
                    except Exception:
                        failed += 1
                except Exception:
                    failed += 1
            await status_msg.edit_text(f"📢 Done.\n✅ Sent: {sent}\n❌ Failed: {failed}")
            await update.message.reply_text("Menu:", reply_markup=admin_menu())
        elif step == "addapi":
            if txt.upper().startswith("DEL "):
                try:
                    idx = int(txt.split()[1]) - 1
                    if 0 <= idx < len(CUSTOM_APIS):
                        removed = CUSTOM_APIS.pop(idx)
                        await save_state(ctx)
                        ADMIN_STATE.pop(uid, None)
                        await update.message.reply_text(f"✅ Deleted {removed['name']}", reply_markup=admin_menu())
                    else:
                        await update.message.reply_text("❌ Invalid index. Try again:", reply_markup=back_menu())
                except Exception:
                    await update.message.reply_text("❌ Format: DEL N (e.g. DEL 1)", reply_markup=back_menu())
                return

            parts = [p.strip() for p in txt.split("|")]
            if len(parts) != 3:
                await update.message.reply_text("❌ Format: NAME | URL | EMOJI", reply_markup=back_menu())
                return
            name, url, emoji = parts
            if "{value}" not in url:
                await update.message.reply_text("❌ URL must contain {value}", reply_markup=back_menu())
                return
            CUSTOM_APIS.append({"name": name, "url": url, "emoji": emoji})
            await save_state(ctx)
            ADMIN_STATE.pop(uid, None)
            await update.message.reply_text(f"✅ Added: {emoji} {name}", reply_markup=admin_menu())

        elif step == "input_reject_reason":
            pay_id = state.get("pay_id")
            m_id = state.get("msg_id")
            c_id = state.get("chat_id")
            ADMIN_STATE.pop(uid, None)

            custom_reason = txt.strip()
            if not custom_reason or custom_reason in ("Cancel", "⬅️ Cancel", "🔙 Back"):
                await update.message.reply_text("Rejection cancelled.", reply_markup=admin_menu())
                return

            admin_u = update.effective_user
            await finalize_rejection(ctx, pay_id, custom_reason, admin_u, chat_id=c_id, msg_id=m_id)
            await update.message.reply_text(f"✅ Payment `{pay_id}` rejected with reason:\n`{custom_reason}`", parse_mode="Markdown", reply_markup=admin_menu())
            return
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}", reply_markup=back_menu())


async def whoami(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    free_used = USER_SEARCHES.get(u.id, 0)
    free_limit = SETTINGS.get("free_search_limit", 2)
    free_rem = max(0, free_limit - free_used)
    await update.message.reply_text(
        f"Your ID: {u.id}\nAdmin? {is_admin(u.id)}\n"
        f"Points: {format_pts(CREDITS.get(u.id, 0.0))}\n"
        f"Free Searches Left: {free_rem}/{free_limit}"
    )


async def on_photo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    uid = u.id
    USERS.add(uid)

    key = ctx.user_data.get("awaiting")
    if key in ("submit_screenshot", "submit_utr"):
        recharge_info = ctx.user_data.pop("pending_recharge", None)
        ctx.user_data.pop("awaiting", None)

        if not recharge_info:
            await update.message.reply_text("❌ Session expired. Please '💳 Buy Points' dubara start karein.", reply_markup=main_menu(uid))
            return

        pts = recharge_info["points"]
        amt = recharge_info["amount"]
        pay_id = f"pay_{uid}_{int(time.time())}"

        is_doc = False
        if update.message.photo:
            photo = update.message.photo[-1]
            photo_file_id = photo.file_id
        elif update.message.document:
            doc = update.message.document
            mime = (doc.mime_type or "").lower()
            fname = (doc.file_name or "").lower()
            if mime.startswith("image/") or fname.endswith((".png", ".jpg", ".jpeg", ".webp")):
                photo_file_id = doc.file_id
                is_doc = True
            else:
                await update.message.reply_text("❌ Kripya valid Image / Photo file bhejein (PNG/JPG/WEBP).", reply_markup=cancel_menu())
                ctx.user_data["awaiting"] = "submit_screenshot"
                ctx.user_data["pending_recharge"] = recharge_info
                return
        else:
            await update.message.reply_text("❌ Please photo screenshot bhejein.", reply_markup=cancel_menu())
            ctx.user_data["awaiting"] = "submit_screenshot"
            ctx.user_data["pending_recharge"] = recharge_info
            return

        PENDING_PAYMENTS[pay_id] = {
            "user_id": uid,
            "username": u.username or "",
            "full_name": u.full_name or "User",
            "points": pts,
            "amount": amt,
            "photo_file_id": photo_file_id,
            "is_doc": is_doc,
            "status": "pending",
            "time": time.time(),
        }
        await save_state(ctx)

        user_msg = (
            "⏳ *PAYMENT SCREENSHOT SUBMITTED!*\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🆔 *Order ID:* `{pay_id}`\n"
            f"💰 *Amount:* `₹{amt:.2f}`\n"
            f"💎 *Points:* `{format_pts(pts)} Points`\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "✅ Aapka payment screenshot Admin verification ke liye bhej diya gaya hai.\n"
            "Admin check karke approve karte hi points aapke account me add ho jayenge!"
        )
        await update.message.reply_markdown(user_msg, reply_markup=main_menu(uid))

        safe_name = str(u.full_name or "User").replace("`", "'").replace("*", "")
        safe_uname = str(u.username or "None").replace("`", "'").replace("*", "")
        admin_caption = (
            "🔔 *NEW PAYMENT SCREENSHOT VERIFICATION*\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            f"👤 *User:* `{safe_name}` (`{uid}`)\n"
            f"🏷 *Username:* `@{safe_uname}`\n"
            f"💰 *Amount:* `₹{amt:.2f}`\n"
            f"💎 *Points to Add:* `{format_pts(pts)} Points`\n"
            f"🆔 *Payment ID:* `{pay_id}`\n"
            f"⏰ *Time:* `{time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}`\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "Verify karke Approve ya Reject karein:"
        )
        admin_kb = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ Approve", callback_data=f"appr_pay:{pay_id}"),
                InlineKeyboardButton("❌ Reject", callback_data=f"rej_pay:{pay_id}"),
            ]
        ])
        try:
            if is_doc:
                await ctx.bot.send_document(
                    chat_id=STORAGE_CHAT_ID,
                    document=photo_file_id,
                    caption=admin_caption,
                    parse_mode="Markdown",
                    reply_markup=admin_kb
                )
            else:
                await ctx.bot.send_photo(
                    chat_id=STORAGE_CHAT_ID,
                    photo=photo_file_id,
                    caption=admin_caption,
                    parse_mode="Markdown",
                    reply_markup=admin_kb
                )
        except Exception as e:
            logger.error(f"Failed to send payment screenshot to storage channel: {e}")
        return

    await update.message.reply_text("🤖 Option select karne ke liye main menu dekhein:", reply_markup=main_menu(uid))


async def finalize_rejection(ctx: ContextTypes.DEFAULT_TYPE, pay_id: str, reason: str, admin_user, message=None, chat_id=None, msg_id=None):
    pay = PENDING_PAYMENTS.get(pay_id)
    if not pay or pay.get("status") != "pending":
        return

    pay["status"] = "rejected"
    pay["reject_reason"] = reason
    target_uid = pay["user_id"]
    await save_state(ctx)

    admin_tag = f"@{admin_user.username or admin_user.id}"
    caption_update = (
        f"❌ *PAYMENT REJECTED*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🆔 *Order ID:* `{pay_id}`\n"
        f"👤 *User:* `{pay.get('full_name')}` (`{target_uid}`)\n"
        f"💰 *Amount:* `₹{pay.get('amount', 0):.2f}`\n"
        f"💎 *Points:* `{format_pts(pay.get('points', 0))}` Points\n"
        f"👮 *Rejected By:* `{admin_tag}`\n"
        f"📝 *Reason:* `{reason}`\n"
        f"━━━━━━━━━━━━━━━━━━━━━━"
    )

    # Update message in storage channel
    if message:
        try:
            if message.photo or message.document:
                await message.edit_caption(caption=caption_update, parse_mode="Markdown")
            else:
                await message.edit_text(text=caption_update, parse_mode="Markdown")
        except Exception:
            pass
    elif chat_id and msg_id:
        try:
            await ctx.bot.edit_message_caption(
                chat_id=chat_id,
                message_id=msg_id,
                caption=caption_update,
                parse_mode="Markdown"
            )
        except Exception:
            try:
                await ctx.bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=msg_id,
                    text=caption_update,
                    parse_mode="Markdown"
                )
            except Exception:
                pass

    # Send DM notification to user with the clear rejection reason!
    try:
        user_rej_msg = (
            "❌ *PAYMENT REJECTED*\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            f"Aapka payment request verify nahi ho saka.\n\n"
            f"📝 *Rejection Reason / कारण:*\n`{reason}`\n\n"
            "Agar aapne payment kiya hai to payment proof ke saath Admin se contact karein:\n"
            f"👉 [Admin Support](tg://user?id={ADMIN_ID})\n"
            "━━━━━━━━━━━━━━━━━━━━━━"
        )
        await ctx.bot.send_message(
            chat_id=target_uid,
            text=user_rej_msg,
            parse_mode="Markdown"
        )
    except Exception as e:
        logger.error(f"Failed to DM user on rejection: {e}")


async def on_callback_query(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Handles inline buttons on result cards and admin approval/rejection actions."""
    q = update.callback_query
    data = q.data or ""

    if data == "check_fsub":
        uid = q.from_user.id
        is_ok, missing = await check_user_force_channels(ctx.bot, uid)
        if is_ok:
            try:
                await q.message.edit_text("✨ 🟢 <b>VERIFIED SUCCESSFULLY</b> 💎\n\n🚀 <i>Welcome! All VIP tools are now unlocked.</i>", parse_mode="HTML")
            except Exception:
                pass
            await q.message.reply_text("🤖 *TOXIC BOT READY*\n\nOption select karo:", reply_markup=main_menu(uid), parse_mode="Markdown")
            try:
                await q.answer("✨ 🟢 Access Granted! Welcome.", show_alert=True)
            except Exception:
                pass
        else:
            text_msg, inline_kb = build_force_join_ui()
            try:
                await q.message.edit_text(text_msg, reply_markup=inline_kb, parse_mode="HTML", disable_web_page_preview=True)
            except Exception:
                pass
            try:
                rem = len(missing)
                await q.answer(f"⚠️ {rem} channel(s) remaining! Please join and click again.", show_alert=True)
            except Exception:
                pass
        return

    if data.startswith("re_tool:"):
        key = data.split(":", 1)[1]
        if key in TOOLS:
            ctx.user_data["awaiting"] = key
            label_name, _, prompt_msg = TOOLS[key]
            await q.message.reply_markdown(
                f"*{label_name}*\n\n{prompt_msg}",
                reply_markup=cancel_menu(),
            )
        try:
            await q.answer()
        except Exception:
            pass
        return

    if data == "buy_points_flow":
        try:
            await q.answer()
        except Exception:
            pass
        await start_buy_points(q.message, ctx, q.from_user.id)
        return

    if data.startswith("appr_pay:"):
        pay_id = data.split(":", 1)[1]
        admin_user = q.from_user
        if not is_admin(admin_user.id):
            try:
                await q.answer("❌ Admin only action!", show_alert=True)
            except Exception:
                pass
            return

        pay = PENDING_PAYMENTS.get(pay_id)
        if not pay:
            try:
                await q.answer("❌ Payment not found!", show_alert=True)
            except Exception:
                pass
            return
        if pay.get("status") != "pending":
            try:
                await q.answer(f"⚠️ Already {pay.get('status')}!", show_alert=True)
            except Exception:
                pass
            return

        pay["status"] = "approved"
        target_uid = pay["user_id"]
        pts_add = float(pay["points"])
        CREDITS[target_uid] = round(CREDITS.get(target_uid, 0.0) + pts_add, 2)
        await save_state(ctx)

        # Update message in storage channel
        old_text = q.message.caption or q.message.text or ""
        admin_tag = f"@{admin_user.username or admin_user.id}"
        updated_text = (
            f"{old_text}\n\n"
            f"✅ *APPROVED* by `{admin_tag}`\n"
            f"💎 Credited: +{format_pts(pts_add)} Points\n"
            f"💰 New Balance: {format_pts(CREDITS[target_uid])} Points"
        )
        try:
            if q.message.photo or q.message.document:
                await q.edit_message_caption(caption=updated_text, parse_mode="Markdown")
            else:
                await q.edit_message_text(text=updated_text, parse_mode="Markdown")
        except Exception:
            pass

        # Send DM notification to user
        try:
            await ctx.bot.send_message(
                chat_id=target_uid,
                text=(
                    "🎉 *PAYMENT APPROVED!*\n"
                    "━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"✅ Aapka recharge successfully verify ho gaya hai!\n"
                    f"💎 *+{format_pts(pts_add)} Points* aapke account me add ho chuke hain.\n"
                    f"💰 *Current Balance:* `{format_pts(CREDITS[target_uid])} Points`\n"
                    "━━━━━━━━━━━━━━━━━━━━━━\n"
                    "Ab aap kisi bhi search tool ka use kar sakte hain!"
                ),
                parse_mode="Markdown"
            )
        except Exception as e:
            logger.error(f"Failed to DM user on approval: {e}")

        try:
            await q.answer("✅ Payment Approved & Points Credited!", show_alert=True)
        except Exception:
            pass
        return

    if data.startswith("rej_pay:"):
        pay_id = data.split(":", 1)[1]
        admin_user = q.from_user
        if not is_admin(admin_user.id):
            try:
                await q.answer("❌ Admin only action!", show_alert=True)
            except Exception:
                pass
            return

        pay = PENDING_PAYMENTS.get(pay_id)
        if not pay:
            try:
                await q.answer("❌ Payment not found!", show_alert=True)
            except Exception:
                pass
            return
        if pay.get("status") != "pending":
            try:
                await q.answer(f"⚠️ Already {pay.get('status')}!", show_alert=True)
            except Exception:
                pass
            return

        reason_kb = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("🚫 Fake Screenshot", callback_data=f"r_rsn:{pay_id}:fake"),
                InlineKeyboardButton("💸 Not Received", callback_data=f"r_rsn:{pay_id}:notrec"),
            ],
            [
                InlineKeyboardButton("⚠️ Wrong Amount", callback_data=f"r_rsn:{pay_id}:amount"),
                InlineKeyboardButton("✏️ Custom Reason", callback_data=f"r_rsn:{pay_id}:custom"),
            ],
            [
                InlineKeyboardButton("🔙 Cancel / Back", callback_data=f"r_cancel:{pay_id}"),
            ],
        ])
        try:
            await q.edit_message_reply_markup(reply_markup=reason_kb)
        except Exception:
            pass
        try:
            await q.answer("Rejection Reason chunein:")
        except Exception:
            pass
        return

    if data.startswith("r_cancel:"):
        pay_id = data.split(":", 1)[1]
        orig_kb = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ Approve", callback_data=f"appr_pay:{pay_id}"),
                InlineKeyboardButton("❌ Reject", callback_data=f"rej_pay:{pay_id}"),
            ]
        ])
        try:
            await q.edit_message_reply_markup(reply_markup=orig_kb)
        except Exception:
            pass
        try:
            await q.answer("Cancelled.")
        except Exception:
            pass
        return

    if data.startswith("r_rsn:"):
        _, pay_id, rsn_code = data.split(":", 2)
        admin_user = q.from_user
        if not is_admin(admin_user.id):
            try:
                await q.answer("❌ Admin only action!", show_alert=True)
            except Exception:
                pass
            return

        pay = PENDING_PAYMENTS.get(pay_id)
        if not pay or pay.get("status") != "pending":
            try:
                await q.answer("⚠️ Already processed or not found!", show_alert=True)
            except Exception:
                pass
            return

        if rsn_code == "fake":
            reason = "Fake / Edited Screenshot Detected"
            await finalize_rejection(ctx, pay_id, reason, admin_user, message=q.message)
            try:
                await q.answer("❌ Rejected: Fake Screenshot", show_alert=True)
            except Exception:
                pass
            return
        elif rsn_code == "notrec":
            reason = "Payment not received in bank/UPI account"
            await finalize_rejection(ctx, pay_id, reason, admin_user, message=q.message)
            try:
                await q.answer("❌ Rejected: Payment not received", show_alert=True)
            except Exception:
                pass
            return
        elif rsn_code == "amount":
            reason = "Incorrect amount paid (Amount mismatch)"
            await finalize_rejection(ctx, pay_id, reason, admin_user, message=q.message)
            try:
                await q.answer("❌ Rejected: Amount mismatch", show_alert=True)
            except Exception:
                pass
            return
        elif rsn_code == "custom":
            ADMIN_STATE[admin_user.id] = {
                "step": "input_reject_reason",
                "pay_id": pay_id,
                "msg_id": q.message.message_id,
                "chat_id": q.message.chat_id,
            }
            prompt_txt = (
                f"✍️ *CUSTOM REJECTION REASON*\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"🆔 Order ID: `{pay_id}`\n\n"
                "Kripya payment reject karne ka valid reason type karke yahan reply karein.\n"
                "Ye reason seedha user ko notification me bheja jayega."
            )
            try:
                await ctx.bot.send_message(
                    chat_id=admin_user.id,
                    text=prompt_txt,
                    parse_mode="Markdown",
                    reply_markup=back_menu()
                )
            except Exception as e:
                logger.error(f"Failed to DM admin custom reason prompt: {e}")
            try:
                await q.answer("✍️ Bot ke DM me custom reason type karein!", show_alert=True)
            except Exception:
                pass
            return

    try:
        await q.answer()
    except Exception:
        pass


# ---------------- MAIN APPLICATION ----------------
def main():
    try:
        loop = asyncio.get_event_loop()
        if loop.is_closed():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .connection_pool_size(64)
        .pool_timeout(10.0)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("menu", start))
    app.add_handler(CommandHandler("admin", admin_cmd))
    app.add_handler(CommandHandler("whoami", whoami))
    app.add_handler(CallbackQueryHandler(on_callback_query))
    app.add_handler(ChatJoinRequestHandler(handle_chat_join_request))
    app.add_handler(MessageHandler(filters.PHOTO | (filters.Document.ALL & ~filters.COMMAND), on_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))

    logger.info("Toxic Bot starting up...")
    print("🚀 Toxic Bot is running with Configurable Points, Free Searches & Result-Based Deductions!")
    app.run_polling(drop_pending_updates=True, allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    try:
        loop = asyncio.get_event_loop()
        if loop.is_closed():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    main()
