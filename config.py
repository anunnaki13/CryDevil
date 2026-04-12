"""
Konfigurasi bot — semua nilai dibaca dari file .env.
Edit file .env untuk mengubah setting, jangan ubah file ini.
"""

import os
import sys
from dotenv import load_dotenv

load_dotenv()


def _require(key: str) -> str:
    val = os.getenv(key, "").strip()
    if not val:
        _errors.append(f"  [WAJIB] {key} belum diisi di .env")
    return val


def _optional(key: str, default: str) -> str:
    return os.getenv(key, default).strip() or default


def _int(key: str, default: int) -> int:
    try:
        return int(os.getenv(key, str(default)).strip())
    except ValueError:
        _warnings.append(f"  [WARNING] {key} harus berupa angka bulat, pakai default {default}")
        return default


def _float(key: str, default: float) -> float:
    try:
        return float(os.getenv(key, str(default)).strip())
    except ValueError:
        _warnings.append(f"  [WARNING] {key} harus berupa angka desimal, pakai default {default}")
        return default


def _int_list(key: str, default: list[int]) -> list[int]:
    raw = os.getenv(key, "").strip()
    if not raw:
        return default
    try:
        return [int(x.strip()) for x in raw.split(",") if x.strip()]
    except ValueError:
        _warnings.append(
            f"  [WARNING] {key} harus berupa angka dipisah koma (contoh: 100,200,400), "
            f"pakai default {default}"
        )
        return default


def _str_list(key: str, default: list[str]) -> list[str]:
    raw = os.getenv(key, "").strip()
    if not raw:
        return default
    return [x.strip() for x in raw.split(",") if x.strip()]


_errors: list[str] = []
_warnings: list[str] = []

# ─── 0. Instance identity ─────────────────────────────────────────────────────
INSTANCE_NAME = _optional("INSTANCE_NAME", "bot-1")
INSTANCE_LABEL = _optional("INSTANCE_LABEL", INSTANCE_NAME)


# ─── 1. Website ───────────────────────────────────────────────────────────────
BASE_URL  = _optional("SITE_URL", "https://partai34848.com").rstrip("/")
POOL_ID   = _optional("POOL_ID",  "p76368")
GAME_TYPE = "quick_2d"
BET_TARGET = _optional("BET_TARGET", "belakang").lower()
BET_POSISI = BET_TARGET

# ─── 2. Kredensial ────────────────────────────────────────────────────────────
USERNAME = _require("PARTAI_USERNAME")
PASSWORD = _require("PARTAI_PASSWORD")

# ─── 3. OpenRouter LLM ───────────────────────────────────────────────────────
OPENROUTER_API_KEY  = _require("OPENROUTER_API_KEY")
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
LLM_PRIMARY         = _optional("LLM_MODEL",         "minimax/minimax-m2.5")
LLM_FALLBACK        = _optional("LLM_FALLBACK_MODEL", "anthropic/claude-sonnet-4-20250514")
LLM_TEMPERATURE     = _float("LLM_TEMPERATURE", 0.3)
LLM_MAX_TOKENS      = 1000
HISTORY_WINDOW      = 200  # jumlah periode untuk analisis LLM

# ─── 4. Nominal bet ──────────────────────────────────────────────────────────
# BASE_BET = nominal per ANGKA (IDR).
#
# Cara hitung API param `bet`:
#   Panel situs: IDR 1.000 = 1  →  bet = BASE_BET / 1000
#   Contoh BASE_BET=100: bet=0.1, total = 0.1 × 1.000 × 50 angka = Rp 5.000
#   Contoh BASE_BET=200: bet=0.2, total = 0.2 × 1.000 × 50 angka = Rp 10.000
#
BASE_BET   = _int("BASE_BET",  100)   # Rp/angka (min Rp 100)
MIN_BET    = 100
MAX_BET_2D = 2_000_000

# Tipe bet — SELALU Bet Full (type=B), payout x100.
# Tidak diambil dari .env agar tidak bisa diubah tidak sengaja.
# B = Bet Full | D = Bet Diskon | A = Bet BB (Bolak-Balik)
BET_TYPE   = "B"

# Mode bet per periode:
#   "double" = 2 bet (BK + GJ) → total 2 × Rp5.000 = Rp10.000
#   "single" = 1 bet (hanya yang confidence tertinggi)
BET_MODE = _optional("BET_MODE", "double")
MIN_CONFIDENCE_TO_BET = _float("MIN_CONFIDENCE_TO_BET", 0.60)

# ─── 5. Martingale ───────────────────────────────────────────────────────────
# 5 level, per ANGKA (IDR). Total/bet = level × 50 angka.
# BK dan GJ punya level TERPISAH masing-masing.
#
# Default (sesuai blueprint):
#   Level 0: Rp100/angka  → Rp5.000/bet
#   Level 1: Rp200/angka  → Rp10.000/bet   (setelah 3 loss BK atau GJ)
#   Level 2: Rp400/angka  → Rp20.000/bet
#   Level 3: Rp800/angka  → Rp40.000/bet
#   Level 4: Rp1.600/angka → Rp80.000/bet  (ceiling)
MARTINGALE_LEVELS         = _int_list("MARTINGALE_LEVELS", [100, 200, 400, 800, 1600])
MARTINGALE_STEP_LOSSES    = _int("MARTINGALE_STEP_LOSSES", 3)  # level naik setiap N loss
MAX_MARTINGALE_LEVEL      = len(MARTINGALE_LEVELS) - 1

# ─── 6. Manajemen uang ───────────────────────────────────────────────────────
DAILY_LOSS_LIMIT = _int("DAILY_LOSS_LIMIT", 200_000)

# ─── 7. Telegram ─────────────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN        = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID          = os.getenv("TELEGRAM_CHAT_ID",   "").strip()
TELEGRAM_THREAD_ID_RAW    = os.getenv("TELEGRAM_MESSAGE_THREAD_ID", "").strip()
TELEGRAM_COMMANDS_ENABLED = _optional("TELEGRAM_COMMANDS_ENABLED", "false").lower() in (
    "1", "true", "yes", "on"
)
TELEGRAM_PREDICT_COOLDOWN_SECONDS = _int("TELEGRAM_PREDICT_COOLDOWN_SECONDS", 60)
FLEET_SNAPSHOT_REFRESH_SECONDS = _int("FLEET_SNAPSHOT_REFRESH_SECONDS", 300)
FLEET_COMMAND_POLL_SECONDS = _int("FLEET_COMMAND_POLL_SECONDS", 5)

try:
    TELEGRAM_MESSAGE_THREAD_ID = int(TELEGRAM_THREAD_ID_RAW) if TELEGRAM_THREAD_ID_RAW else None
except ValueError:
    _warnings.append("  [WARNING] TELEGRAM_MESSAGE_THREAD_ID harus berupa angka bulat, diabaikan")
    TELEGRAM_MESSAGE_THREAD_ID = None

# ─── 8. Timing ───────────────────────────────────────────────────────────────
POLL_START_MINUTE         = _int("BET_START_MINUTE",    5)
BET_DEADLINE_MINUTE       = _int("BET_STOP_MINUTE",    50)
POLL_INTERVAL_SECONDS     = _int("POLL_INTERVAL",     120)
MAX_POLL_ATTEMPTS         = _int("MAX_POLL_ATTEMPTS",  10)
# Alias untuk kompatibilitas
BET_START_MINUTE          = POLL_START_MINUTE
BET_STOP_MINUTE           = BET_DEADLINE_MINUTE

# ─── 9. Session ──────────────────────────────────────────────────────────────
SESSION_VALIDATION_INTERVAL = _int("SESSION_CHECK_INTERVAL", 1_800)

# ─── External APIs ───────────────────────────────────────────────────────────
TIMER_API_URL = "https://jampasaran.smbgroup.io/pasaran"

# ─── Paths ───────────────────────────────────────────────────────────────────
STATE_DIR = _optional("STATE_DIR", ".")
DB_PATH   = _optional("DB_PATH",  os.path.join(STATE_DIR, "data", "hokidraw.db"))
LOG_PATH  = _optional("LOG_PATH", os.path.join(STATE_DIR, "logs", "bot.log"))
FLEET_SHARED_DIR = _optional("FLEET_SHARED_DIR", os.path.join(STATE_DIR, "shared"))
FLEET_SHARED_ANALYSIS = _optional("FLEET_SHARED_ANALYSIS", "false").lower() in (
    "1", "true", "yes", "on"
)
FLEET_ROLE = _optional("FLEET_ROLE", "solo").lower()
FLEET_BOT_NAMES = _str_list("FLEET_BOT_NAMES", ["bot-1", "bot-2", "bot-3"])

# ─── Browser headers (anti-Cloudflare) ───────────────────────────────────────
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "id-ID,id;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Ch-Ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"Windows"',
}

AJAX_HEADERS = {
    **HEADERS,
    "X-Requested-With": "XMLHttpRequest",
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
}


# ─── Validasi konfigurasi ────────────────────────────────────────────────────

def validate_config(exit_on_error: bool = True) -> bool:
    ok = True

    if _errors:
        print("\n" + "=" * 60)
        print("  KONFIGURASI BELUM LENGKAP — .env perlu diisi:")
        print("=" * 60)
        for e in _errors:
            print(e)
        print("\n  Salin template: cp .env.example .env && nano .env\n")
        ok = False

    if _warnings:
        print("\n  Peringatan:")
        for w in _warnings:
            print(w)
        print()

    logic_errors = []

    if BASE_BET < MIN_BET:
        logic_errors.append(f"  BASE_BET=Rp{BASE_BET} di bawah minimum Rp{MIN_BET}")

    if DAILY_LOSS_LIMIT < BASE_BET * 50 * 2:
        logic_errors.append(
            f"  DAILY_LOSS_LIMIT=Rp{DAILY_LOSS_LIMIT} terlalu kecil "
            f"(kurang dari 1 round = Rp{BASE_BET * 50 * 2:,})"
        )

    if BET_MODE not in ("single", "double"):
        logic_errors.append(f"  BET_MODE='{BET_MODE}' tidak valid. Pilih: single atau double")

    if not (0.0 <= MIN_CONFIDENCE_TO_BET <= 1.0):
        logic_errors.append(
            f"  MIN_CONFIDENCE_TO_BET={MIN_CONFIDENCE_TO_BET} tidak valid. Gunakan nilai 0.0 sampai 1.0"
        )

    if BET_TARGET not in ("depan", "tengah", "belakang"):
        logic_errors.append(
            f"  BET_TARGET='{BET_TARGET}' tidak valid. Pilih: depan, tengah, atau belakang"
        )

    if FLEET_ROLE not in ("solo", "leader", "worker"):
        logic_errors.append(
            f"  FLEET_ROLE='{FLEET_ROLE}' tidak valid. Pilih: solo, leader, atau worker"
        )

    if FLEET_SHARED_ANALYSIS and FLEET_ROLE == "solo":
        logic_errors.append(
            "  FLEET_SHARED_ANALYSIS aktif tetapi FLEET_ROLE masih 'solo'. Gunakan leader atau worker"
        )

    if FLEET_SHARED_ANALYSIS and INSTANCE_NAME not in FLEET_BOT_NAMES:
        logic_errors.append(
            f"  INSTANCE_NAME='{INSTANCE_NAME}' tidak ada di FLEET_BOT_NAMES={FLEET_BOT_NAMES}"
        )

    if FLEET_SHARED_ANALYSIS and len(set(FLEET_BOT_NAMES)) != len(FLEET_BOT_NAMES):
        logic_errors.append("  FLEET_BOT_NAMES mengandung nama bot duplikat")

    if TELEGRAM_COMMANDS_ENABLED and FLEET_SHARED_ANALYSIS and FLEET_ROLE == "worker":
        _warnings.append(
            "  [WARNING] TELEGRAM_COMMANDS_ENABLED aktif pada worker. Disarankan hanya leader yang aktif polling command Telegram"
        )

    if logic_errors:
        print("\n  Error konfigurasi:")
        for e in logic_errors:
            print(e)
        print()
        ok = False

    if ok:
        total = BASE_BET * 50 * (2 if BET_MODE == "double" else 1)
        print("\n" + "=" * 60)
        print("  KONFIGURASI AKTIF")
        print("=" * 60)
        print(f"  Instance     : {INSTANCE_LABEL} ({INSTANCE_NAME})")
        print(f"  Website      : {BASE_URL}")
        print(f"  Pool ID      : {POOL_ID}")
        print(f"  Username     : {USERNAME}")
        print(f"  LLM Model    : {LLM_PRIMARY}")
        print(f"  Posisi       : 2D {BET_TARGET.title()}")
        print(f"  Bet/angka    : Rp{BASE_BET:,}  |  bet param: {BASE_BET/1000}  |  Tipe: BET FULL (B)  |  Mode: {BET_MODE}")
        print(f"  Min Conf     : {MIN_CONFIDENCE_TO_BET:.0%}")
        print(f"  Total/round  : Rp{total:,}  (50 angka × {'2 bet' if BET_MODE=='double' else '1 bet'} × Rp{BASE_BET:,})")
        print(f"  Pot. menang  : Rp{BASE_BET * 100:,}/bet (100x)")
        lv_str = " → ".join(f"Rp{x:,}" for x in MARTINGALE_LEVELS)
        print(f"  Martingale   : {len(MARTINGALE_LEVELS)} level — {lv_str}")
        print(f"  Naik level   : setiap {MARTINGALE_STEP_LOSSES} kalah berturut (BK & GJ TERPISAH)")
        print(f"  Limit/hari   : Rp{DAILY_LOSS_LIMIT:,}")
        print(f"  Jadwal       : setiap jam di menit :{POLL_START_MINUTE:02d}")
        print(f"  DB Path      : {DB_PATH}")
        print(f"  Log Path     : {LOG_PATH}")
        print(f"  Fleet Mode   : {'aktif' if FLEET_SHARED_ANALYSIS else 'nonaktif'}")
        print(f"  Fleet Role   : {FLEET_ROLE}")
        print(f"  Shared Dir   : {FLEET_SHARED_DIR}")
        print(f"  Telegram     : {'aktif' if TELEGRAM_BOT_TOKEN else 'nonaktif'}")
        print(f"  Commands TG  : {'aktif' if TELEGRAM_COMMANDS_ENABLED else 'nonaktif'}")
        print("=" * 60 + "\n")

    if not ok and exit_on_error:
        sys.exit(1)

    return ok
