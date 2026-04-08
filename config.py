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
            f"  [WARNING] {key} harus berupa angka dipisah koma (contoh: 1000,2000,4000), "
            f"pakai default {default}"
        )
        return default


_errors: list[str] = []
_warnings: list[str] = []

# ─── 1. Website ───────────────────────────────────────────────────────────────
BASE_URL    = _optional("SITE_URL", "https://partai34848.com").rstrip("/")
POOL_ID     = _optional("POOL_ID",  "p76368")
GAME_TYPE   = "quick_2d"
BET_POSISI  = "belakang"

# ─── 2. Kredensial ────────────────────────────────────────────────────────────
USERNAME = _require("PARTAI_USERNAME")
PASSWORD = _require("PARTAI_PASSWORD")

# ─── 3. OpenRouter LLM ───────────────────────────────────────────────────────
OPENROUTER_API_KEY  = _require("OPENROUTER_API_KEY")
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
LLM_PRIMARY         = _optional("LLM_MODEL",          "google/gemini-2.0-flash-001")
LLM_FALLBACK        = _optional("LLM_FALLBACK_MODEL",  "anthropic/claude-sonnet-4-20250514")
LLM_TEMPERATURE     = _float("LLM_TEMPERATURE", 0.3)
LLM_MAX_TOKENS      = 1000

# ─── 4. Nominal bet ──────────────────────────────────────────────────────────
# BASE_BET = nominal per NOMOR (IDR). Setiap bet kategori = 50 nomor × BASE_BET
# Contoh: BASE_BET=100 → 50 nomor × Rp100 = Rp5.000/kategori
#         Menang: 1 nomor × 100x = Rp10.000, profit bersih Rp5.000/kategori
BASE_BET    = _int("BASE_BET",   100)    # Rp/nomor di level 0 (min Rp 100)
BET_TYPE    = _optional("BET_TYPE", "B") # B=full, D=diskon, A=bolak-balik
MIN_BET     = 100
MAX_BET_2D  = 2_000_000

# Posisi yang diikutsertakan dalam bet (pilihan: depan, tengah, belakang)
_pos_raw    = _optional("BET_POSITIONS", "belakang")
BET_POSITIONS = [p.strip().lower() for p in _pos_raw.split(",") if p.strip()]

# Untuk kompatibilitas modul lain
NUM_PICKS   = len(BET_POSITIONS)  # 1 prediction object per posisi

# ─── 5. Martingale ───────────────────────────────────────────────────────────
# Level = nominal per NOMOR (IDR). Total per round = level × 50 × jumlah_posisi × 2
MARTINGALE_LEVELS          = _int_list(
    "MARTINGALE_LEVELS",
    [100, 150, 250, 400, 600, 900, 1300],  # per nomor; ×100 = total/kategori
)
MARTINGALE_LOSS_THRESHOLD  = _int("MARTINGALE_LOSS_THRESHOLD", 5)
MAX_MARTINGALE_LEVEL       = len(MARTINGALE_LEVELS) - 1

# ─── 6. Manajemen uang ───────────────────────────────────────────────────────
DAILY_LOSS_LIMIT = _int("DAILY_LOSS_LIMIT", 200_000)

# ─── 7. Telegram ─────────────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID",   "").strip()
# Telegram bersifat opsional — tidak wajib

# ─── 8. Timing ───────────────────────────────────────────────────────────────
BET_START_MINUTE      = _int("BET_START_MINUTE",    5)
BET_STOP_MINUTE       = _int("BET_STOP_MINUTE",    50)
POLL_INTERVAL_SECONDS = _int("POLL_INTERVAL",     120)
MAX_POLL_ATTEMPTS     = _int("MAX_POLL_ATTEMPTS",  10)

# ─── 9. Session ──────────────────────────────────────────────────────────────
SESSION_VALIDATION_INTERVAL = _int("SESSION_CHECK_INTERVAL", 1_800)

# ─── External APIs ───────────────────────────────────────────────────────────
TIMER_API_URL = "https://jampasaran.smbgroup.io/pasaran"

# ─── Paths ───────────────────────────────────────────────────────────────────
DB_PATH  = "data/hokidraw.db"
LOG_PATH = "logs/bot.log"

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
    """
    Cek semua konfigurasi wajib dan tampilkan pesan jelas jika ada yang kurang.
    Dipanggil otomatis saat startup.
    """
    ok = True

    if _errors:
        print("\n" + "=" * 60)
        print("  KONFIGURASI BELUM LENGKAP — .env perlu diisi:")
        print("=" * 60)
        for e in _errors:
            print(e)
        print("\n  Salin .env.example ke .env lalu isi semua nilai wajib:")
        print("  cp .env.example .env && nano .env\n")
        ok = False

    if _warnings:
        print("\n  Peringatan konfigurasi:")
        for w in _warnings:
            print(w)
        print()

    # Validasi logika tambahan
    logic_errors = []

    if NUM_PICKS < 1 or NUM_PICKS > 10:
        logic_errors.append(f"  NUM_PICKS={NUM_PICKS} harus antara 1–10")

    if BASE_BET < MIN_BET:
        logic_errors.append(
            f"  BASE_BET=Rp{BASE_BET} di bawah minimum Rp{MIN_BET}"
        )

    if DAILY_LOSS_LIMIT < BASE_BET * NUM_PICKS:
        logic_errors.append(
            f"  DAILY_LOSS_LIMIT=Rp{DAILY_LOSS_LIMIT} terlalu kecil "
            f"(kurang dari 1 round bet = Rp{BASE_BET * NUM_PICKS})"
        )

    if BET_TYPE not in ("B", "D", "A"):
        logic_errors.append(f"  BET_TYPE='{BET_TYPE}' tidak valid. Pilih: B, D, atau A")

    if not (0 <= BET_START_MINUTE <= 59):
        logic_errors.append(f"  BET_START_MINUTE={BET_START_MINUTE} harus 0–59")

    if not (0 <= BET_STOP_MINUTE <= 59):
        logic_errors.append(f"  BET_STOP_MINUTE={BET_STOP_MINUTE} harus 0–59")

    if logic_errors:
        print("\n  Error konfigurasi:")
        for e in logic_errors:
            print(e)
        print()
        ok = False

    if ok:
        total_per_round = BASE_BET * 50 * 2 * len(BET_POSITIONS)
        print("\n" + "=" * 60)
        print("  KONFIGURASI AKTIF")
        print("=" * 60)
        print(f"  Website      : {BASE_URL}")
        print(f"  Pool ID      : {POOL_ID}")
        print(f"  Username     : {USERNAME}")
        print(f"  LLM Model    : {LLM_PRIMARY}")
        print(f"  Posisi bet   : {', '.join(BET_POSITIONS)}")
        print(f"  Bet/nomor    : Rp{BASE_BET:,}  |  Tipe: {BET_TYPE}")
        print(f"  Total/round  : Rp{total_per_round:,}  "
              f"(50 nomor × 2 kategori × {len(BET_POSITIONS)} posisi × Rp{BASE_BET:,})")
        print(f"  Potensi win  : Rp{BASE_BET * 100:,}/kategori (100x)")
        print(f"  Martingale   : {len(MARTINGALE_LEVELS)} level — " +
              " → ".join(f"Rp{x:,}" for x in MARTINGALE_LEVELS))
        print(f"  Naik level   : setiap {MARTINGALE_LOSS_THRESHOLD} kalah berturut-turut")
        print(f"  Limit/hari   : Rp{DAILY_LOSS_LIMIT:,}")
        print(f"  Jadwal       : setiap jam di menit :{BET_START_MINUTE:02d}")
        print(f"  Telegram     : {'aktif' if TELEGRAM_BOT_TOKEN else 'nonaktif'}")
        print("=" * 60 + "\n")

    if not ok and exit_on_error:
        sys.exit(1)

    return ok
