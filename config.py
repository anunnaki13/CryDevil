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


_errors: list[str] = []
_warnings: list[str] = []


INSTANCE_NAME = _optional("INSTANCE_NAME", "bot-1")
INSTANCE_LABEL = _optional("INSTANCE_LABEL", INSTANCE_NAME)


BASE_URL = _optional("SITE_URL", "https://partai34848.com").rstrip("/")
POOL_ID = _optional("POOL_ID", "p76368")
GAME_TYPE = "quick_2d"


USERNAME = _require("PARTAI_USERNAME")
PASSWORD = _require("PARTAI_PASSWORD")


OPENROUTER_API_KEY = _require("OPENROUTER_API_KEY")
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
LLM_PRIMARY = _optional("LLM_MODEL", "minimax/minimax-m2.5")
LLM_FALLBACK = _optional("LLM_FALLBACK_MODEL", "anthropic/claude-sonnet-4-20250514")
LLM_TEMPERATURE = _float("LLM_TEMPERATURE", 0.3)
LLM_MAX_TOKENS = 1400
HISTORY_WINDOW = 200
BACKLOG_RECOVERY_LIMIT = _int("BACKLOG_RECOVERY_LIMIT", 1000)
HISTORY_FETCH_MAX_PAGES = _int("HISTORY_FETCH_MAX_PAGES", 100)
PREDICTION_EVAL_WINDOW = _int("PREDICTION_EVAL_WINDOW", 30)
KNOWLEDGE_BASE_HISTORY_LIMIT = _int("KNOWLEDGE_BASE_HISTORY_LIMIT", 50)
ADAPTIVE_SELECTION_WINDOW = _int("ADAPTIVE_SELECTION_WINDOW", 20)
LOW_CONFIDENCE_CUTOFF = _float("LOW_CONFIDENCE_CUTOFF", 0.60)
AUTO_RELEARN_LOSS_STREAK = _int("AUTO_RELEARN_LOSS_STREAK", 4)


BASE_BET = _int("BASE_BET", 100)
MIN_BET = 100
MAX_BET_2D = 2_000_000
BET_TYPE = "B"
BET_MODE = "single"
MIN_CONFIDENCE_TO_BET = _float("MIN_CONFIDENCE_TO_BET", 0.60)
DEFAULT_OPERATION_MODE = _optional("OPERATION_MODE", "sedang").lower()
STRATEGY_THRESHOLD_AUTO = _float("STRATEGY_THRESHOLD_AUTO", -1.0)
STRATEGY_THRESHOLD_ZIGZAG = _float("STRATEGY_THRESHOLD_ZIGZAG", -1.0)
STRATEGY_THRESHOLD_TREND = _float("STRATEGY_THRESHOLD_TREND", -1.0)
STRATEGY_THRESHOLD_HEURISTIC = _float("STRATEGY_THRESHOLD_HEURISTIC", -1.0)
STRATEGY_THRESHOLD_LLM = _float("STRATEGY_THRESHOLD_LLM", -1.0)
STRATEGY_THRESHOLD_HYBRID = _float("STRATEGY_THRESHOLD_HYBRID", -1.0)


POSITIONS = ("depan", "tengah", "belakang")
DIMENSIONS = ("besar_kecil", "genap_ganjil")
DIMENSION_CODES = {
    "besar_kecil": ("BE", "KE"),
    "genap_ganjil": ("GE", "GA"),
}
SLOTS = tuple(
    f"{position}_{'bk' if dimension == 'besar_kecil' else 'gj'}"
    for position in POSITIONS
    for dimension in DIMENSIONS
)


MARTINGALE_LEVELS = _int_list("MARTINGALE_LEVELS", [100, 200, 400, 800, 1600])
MARTINGALE_STEP_LOSSES = _int("MARTINGALE_STEP_LOSSES", 3)
MAX_MARTINGALE_LEVEL = len(MARTINGALE_LEVELS) - 1
DAILY_LOSS_LIMIT = _int("DAILY_LOSS_LIMIT", 200_000)


OPERATION_PROFILES = {
    "aman": {
        "threshold": max(MIN_CONFIDENCE_TO_BET, 0.65),
        "martingale_levels": MARTINGALE_LEVELS,
        "label": "AMAN",
    },
    "sedang": {
        "threshold": max(MIN_CONFIDENCE_TO_BET, 0.62),
        "martingale_levels": MARTINGALE_LEVELS,
        "label": "SEDANG",
    },
    "agresif": {
        "threshold": max(0.58, min(0.82, MIN_CONFIDENCE_TO_BET - 0.02)),
        "martingale_levels": [max(MIN_BET, level) for level in MARTINGALE_LEVELS],
        "label": "AGRESIF",
    },
}


def normalize_operation_mode(value: str | None) -> str:
    mode = (value or DEFAULT_OPERATION_MODE or "sedang").strip().lower()
    return mode if mode in OPERATION_PROFILES else "sedang"


def get_operation_profile(mode: str | None = None) -> dict:
    normalized = normalize_operation_mode(mode)
    profile = OPERATION_PROFILES[normalized]
    return {
        "key": normalized,
        "label": profile["label"],
        "threshold": float(profile["threshold"]),
        "martingale_levels": list(profile["martingale_levels"]),
    }


def get_strategy_threshold(strategy: str | None, base_threshold: float) -> float:
    normalized = (strategy or "auto").strip().lower()
    defaults = {
        "auto": max(0.58, min(0.82, base_threshold)),
        "zigzag": max(0.56, min(0.80, base_threshold - 0.04)),
        "trend": max(0.58, min(0.82, base_threshold - 0.02)),
        "heuristic": max(0.58, min(0.82, base_threshold - 0.01)),
        "llm": max(0.60, min(0.84, base_threshold)),
        "hybrid": max(0.59, min(0.84, base_threshold - 0.01)),
    }
    configured = {
        "auto": STRATEGY_THRESHOLD_AUTO,
        "zigzag": STRATEGY_THRESHOLD_ZIGZAG,
        "trend": STRATEGY_THRESHOLD_TREND,
        "heuristic": STRATEGY_THRESHOLD_HEURISTIC,
        "llm": STRATEGY_THRESHOLD_LLM,
        "hybrid": STRATEGY_THRESHOLD_HYBRID,
    }
    explicit = configured.get(normalized, -1.0)
    if 0.0 <= explicit <= 1.0:
        return explicit
    return defaults.get(normalized, base_threshold)


TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
TELEGRAM_THREAD_ID_RAW = os.getenv("TELEGRAM_MESSAGE_THREAD_ID", "").strip()
TELEGRAM_COMMANDS_ENABLED = _optional("TELEGRAM_COMMANDS_ENABLED", "false").lower() in (
    "1", "true", "yes", "on"
)
TELEGRAM_PREDICT_COOLDOWN_SECONDS = _int("TELEGRAM_PREDICT_COOLDOWN_SECONDS", 60)

try:
    TELEGRAM_MESSAGE_THREAD_ID = int(TELEGRAM_THREAD_ID_RAW) if TELEGRAM_THREAD_ID_RAW else None
except ValueError:
    _warnings.append("  [WARNING] TELEGRAM_MESSAGE_THREAD_ID harus berupa angka bulat, diabaikan")
    TELEGRAM_MESSAGE_THREAD_ID = None


POLL_START_MINUTE = _int("BET_START_MINUTE", 5)
BET_DEADLINE_MINUTE = _int("BET_STOP_MINUTE", 50)
POLL_INTERVAL_SECONDS = _int("POLL_INTERVAL", 120)
MAX_POLL_ATTEMPTS = _int("MAX_POLL_ATTEMPTS", 10)
BET_START_MINUTE = POLL_START_MINUTE
BET_STOP_MINUTE = BET_DEADLINE_MINUTE


SESSION_VALIDATION_INTERVAL = _int("SESSION_CHECK_INTERVAL", 1_800)
TIMER_API_URL = "https://jampasaran.smbgroup.io/pasaran"


STATE_DIR = _optional("STATE_DIR", ".")
DB_PATH = _optional("DB_PATH", os.path.join(STATE_DIR, "data", "hokidraw.db"))
LOG_PATH = _optional("LOG_PATH", os.path.join(STATE_DIR, "logs", "bot.log"))


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


def validate_config(exit_on_error: bool = True) -> bool:
    ok = True

    if _errors:
        print("\n" + "=" * 60)
        print("  KONFIGURASI BELUM LENGKAP — .env perlu diisi:")
        print("=" * 60)
        for err in _errors:
            print(err)
        print("\n  Salin template: cp .env.example .env && nano .env\n")
        ok = False

    if _warnings:
        print("\n  Peringatan:")
        for warning in _warnings:
            print(warning)
        print()

    logic_errors = []

    if BASE_BET < MIN_BET:
        logic_errors.append(f"  BASE_BET=Rp{BASE_BET} di bawah minimum Rp{MIN_BET}")

    if DAILY_LOSS_LIMIT < BASE_BET * 50:
        logic_errors.append(
            f"  DAILY_LOSS_LIMIT=Rp{DAILY_LOSS_LIMIT} terlalu kecil "
            f"(kurang dari 1 bet = Rp{BASE_BET * 50:,})"
        )

    if BET_MODE != "single":
        logic_errors.append("  BET_MODE tidak lagi didukung selain 'single'")

    if not (0.0 <= MIN_CONFIDENCE_TO_BET <= 1.0):
        logic_errors.append(
            f"  MIN_CONFIDENCE_TO_BET={MIN_CONFIDENCE_TO_BET} tidak valid. Gunakan nilai 0.0 sampai 1.0"
        )

    if len(MARTINGALE_LEVELS) == 0:
        logic_errors.append("  MARTINGALE_LEVELS tidak boleh kosong")

    if logic_errors:
        print("\n  Error konfigurasi:")
        for err in logic_errors:
            print(err)
        print()
        ok = False

    if ok:
        print("\n" + "=" * 60)
        print("  KONFIGURASI AKTIF")
        print("=" * 60)
        print(f"  Instance     : {INSTANCE_LABEL} ({INSTANCE_NAME})")
        print(f"  Website      : {BASE_URL}")
        print(f"  Pool ID      : {POOL_ID}")
        print(f"  Username     : {USERNAME}")
        print(f"  LLM Model    : {LLM_PRIMARY}")
        print("  Cakupan      : 2D depan + tengah + belakang")
        print(f"  Mode Bet     : {BET_MODE} (1 kandidat terbaik per periode)")
        print(f"  Bet/angka    : Rp{BASE_BET:,} | bet param: {BASE_BET / 1000}")
        print(f"  Min Conf     : {MIN_CONFIDENCE_TO_BET:.0%}")
        print(f"  Total/bet    : Rp{BASE_BET * 50:,}")
        lv_str = " → ".join(f"Rp{x:,}" for x in MARTINGALE_LEVELS)
        print(f"  Martingale   : 6 slot terpisah — {lv_str}")
        print(f"  Naik level   : setiap {MARTINGALE_STEP_LOSSES} kalah berturut per slot")
        print(f"  Limit/hari   : Rp{DAILY_LOSS_LIMIT:,}")
        print(f"  History LLM  : {HISTORY_WINDOW} hasil")
        print(f"  Recovery     : {BACKLOG_RECOVERY_LIMIT} hasil (max {HISTORY_FETCH_MAX_PAGES} page)")
        print(f"  Jadwal       : setiap jam di menit :{POLL_START_MINUTE:02d}")
        print(f"  DB Path      : {DB_PATH}")
        print(f"  Log Path     : {LOG_PATH}")
        print(f"  Telegram     : {'aktif' if TELEGRAM_BOT_TOKEN else 'nonaktif'}")
        print(f"  Commands TG  : {'aktif' if TELEGRAM_COMMANDS_ENABLED else 'nonaktif'}")
        print("=" * 60 + "\n")

    if not ok and exit_on_error:
        sys.exit(1)

    return ok
