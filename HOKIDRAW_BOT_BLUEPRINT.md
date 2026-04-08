# CLAUDE CODE BLUEPRINT: Hokidraw 2D Auto-Betting Bot

## PROJECT OVERVIEW

Bangun bot Python untuk otomatis memasang taruhan **2D Belakang (Besar/Kecil/Genap/Ganjil)** pada pasaran **Hokidraw** di situs partai34848.com. Bot menganalisis history hasil draw menggunakan OpenRouter LLM, lalu memilih apakah akan bet BESAR, KECIL, GENAP, atau GANJIL. Notifikasi via Telegram.

---

## TARGET PASARAN

- **Nama**: HOKIDRAW POOLS
- **Pool ID**: `p76368`
- **Internal Code**: `OKAQ`
- **Jadwal**: 24 draw per hari (setiap 1 jam)
- **Draw dimulai**: menit :01 setiap jam
- **Hasil masuk ke situs**: sekitar menit :20 (delay dari penyelenggara)
- **Situs resmi draw**: https://hokidraw.com
- **Game type**: `quick_2d` — posisi `belakang` (2 digit terakhir)

---

## MEKANISME PERMAINAN (PENTING — BACA DULU!)

### Cara Baca Hasil Draw
Hokidraw menghasilkan 4 digit angka, dipecah menjadi 3 pasang 2D:
```
Contoh hasil: 1295
  2D Depan   = 12 (digit ke-1 dan ke-2)
  2D Tengah  = 29 (digit ke-2 dan ke-3)
  2D Belakang = 95 (digit ke-3 dan ke-4) ← FOKUS BOT INI
```

### Klasifikasi 2D Belakang
Dari angka 2D Belakang (misal "95"), ada 2 dimensi klasifikasi:

**Dimensi 1 — Besar/Kecil (berdasar digit PERTAMA dari 2D):**
- KECIL = digit pertama 0,1,2,3,4 → angka 00-49
- BESAR = digit pertama 5,6,7,8,9 → angka 50-99

**Dimensi 2 — Genap/Ganjil (berdasar digit KEDUA dari 2D):**
- GENAP = digit kedua 0,2,4,6,8 → angka 00,02,04,...98
- GANJIL = digit kedua 1,3,5,7,9 → angka 01,03,05,...99

**Contoh**: Hasil 95 → Besar (9≥5) + Ganjil (5 ganjil)

### 4 Pilihan Taruhan Bot
Bot memilih SATU atau DUA dari ini setiap periode:

| Tebakan | Kode API | Angka yang di-cover | Win Rate |
|---------|----------|---------------------|----------|
| BESAR   | BE       | 50,51,52,...,99 (50 angka) | 50% |
| KECIL   | KE       | 00,01,02,...,49 (50 angka) | 50% |
| GANJIL  | GA       | 01,03,05,...,99 (50 angka) | 50% |
| GENAP   | GE       | 00,02,04,...,98 (50 angka) | 50% |

### Ekonomi Per Taruhan (BET FULL type=B)
```
Bet BESAR dengan nominal Rp 100/angka:
  Modal  = Rp 100 × 50 angka = Rp 5.000
  Menang = Rp 100 × 100 (hadiah x100) = Rp 10.000
  Profit jika win = Rp 10.000 - Rp 5.000 = +Rp 5.000
  Loss jika kalah = -Rp 5.000
  Win rate = 50% (binary outcome)
```

### Strategi Bot: 2 Bet Per Periode
Bot bisa memasang **2 taruhan per periode** (1 dari dimensi Besar/Kecil + 1 dari dimensi Genap/Ganjil):
```
Contoh: Bot pilih BESAR + GANJIL
  Modal total: 2 × Rp 5.000 = Rp 10.000
  Outcome:
  - 2 menang (25%): +Rp 10.000
  - 1 menang (50%): Rp 0 (impas)
  - 0 menang (25%): -Rp 10.000
```

---

## API ENDPOINTS (CONFIRMED FROM HAR)

### Base URL: `https://partai34848.com`

### 1. Authentication
```
POST /json/post/ceklogin-ts
Content-Type: application/json
X-Requested-With: XMLHttpRequest

Body:
{
  "entered_login": "<username>",
  "entered_password": "<password>",
  "liteMode": "",
  "_token": "<csrf_token>"
}

Note: _token (CSRF) harus diambil dari halaman utama sebelum login.
Note: Password dikirim plain text (tidak di-hash).
```

### 2. Place Bet (ENDPOINT UTAMA)
```
POST /games/4d/send
Content-Type: application/x-www-form-urlencoded; charset=UTF-8
X-Requested-With: XMLHttpRequest

CARA KERJA:
Situs meng-expand pilihan BESAR/KECIL/GENAP/GANJIL menjadi 50 angka individual.
Bot harus generate angka-angka tersebut dan mengirimnya sebagai form data.

Angka per tebakan:
  BESAR (BE): [50, 51, 52, ..., 99]  → 50 angka
  KECIL (KE): [00, 01, 02, ..., 49]  → 50 angka
  GANJIL (GA): [01, 03, 05, ..., 99] → 50 angka
  GENAP (GE): [00, 02, 04, ..., 98]  → 50 angka

Body (URL-encoded form data):
  type=B                  # B=BET FULL (hadiah x100), D=BET DISKON, A=BET BB
  ganti=F                 # Fixed value, selalu "F"
  game=quick_2d           # Tipe game
  bet=0.1                 # Nominal PER ANGKA dalam ribuan (0.1 = Rp 100)
  posisi=belakang         # Posisi 2D belakang
  cek1=1                  # Flag angka ke-1 aktif
  tebak1=50               # Angka ke-1 (contoh: bet BESAR dimulai dari 50)
  cek2=1
  tebak2=51
  ...                     # (total 50 pasang cek/tebak)
  cek50=1
  tebak50=99
  sar=p76368              # Pool ID Hokidraw

Response (JSON):
{
  "status": true,
  "transaksi": "50@@C@@100@@0.00@@0@@100@@100@@10000@@B//51@@C@@...",
  "error": "",
  "periode": "11037 - HOKIDRAW",
  "balance": "40013.00"
}

Format transaksi: angka@@status@@taruhan@@diskon@@?@@bayar@@?@@potensi_menang@@type//

Konversi bet (nominal per angka):
  bet=0.1 → Rp 100/angka × 50 = Rp 5.000 total
  bet=1   → Rp 1.000/angka × 50 = Rp 50.000 total
  bet=5   → Rp 5.000/angka × 50 = Rp 250.000 total

Limits 2D: min=100 (Rp 100/angka), max=2.000.000
Hadiah BET FULL 2D: x100 per angka yang cocok
```

### Helper Function untuk Generate Angka
```python
def generate_numbers(choice: str) -> list[str]:
    """Generate 50 angka berdasarkan pilihan BE/KE/GA/GE"""
    if choice == "BE":  # BESAR
        return [str(i) for i in range(50, 100)]
    elif choice == "KE":  # KECIL
        return [f"{i:02d}" for i in range(0, 50)]
    elif choice == "GA":  # GANJIL
        return [f"{i:02d}" for i in range(0, 100) if i % 2 == 1]
    elif choice == "GE":  # GENAP
        return [f"{i:02d}" for i in range(0, 100) if i % 2 == 0]

def classify_result(number_2d: str) -> dict:
    """Klasifikasi hasil 2D ke besar/kecil dan genap/ganjil"""
    num = int(number_2d)
    digit_first = int(number_2d[0])   # Digit puluhan
    digit_second = int(number_2d[1])  # Digit satuan
    return {
        "besar_kecil": "BE" if num >= 50 else "KE",
        "genap_ganjil": "GE" if digit_second % 2 == 0 else "GA",
        "besar_kecil_label": "BESAR" if num >= 50 else "KECIL",
        "genap_ganjil_label": "GENAP" if digit_second % 2 == 0 else "GANJIL",
    }
```

### 3. Game Data & Timer
```
GET /games/4d/p76368
→ Parse hidden field "timerpools" = detik tersisa sampai betting ditutup
→ Parse hidden field "periode" = nomor periode aktif

GET /games/4d/load/quick_2d/p76368
→ HTML form Quick 2D betting panel
→ Berisi info periode aktif

GET https://jampasaran.smbgroup.io/pasaran (PUBLIC, no auth needed)
→ JSON response:
{
  "status": "success",
  "data": {
    "hokidraw": 1138,  ← detik sampai draw berikutnya
    ...
  }
}
```

### 4. History / Results
```
GET /history/detail/data/p76368-1
→ JSON data history keluaran (perlu dicoba langsung, body kosong di HAR karena lazy-load)

GET /games/4d/history/quick_2d/p76368
→ HTML table bet history: No, Tebakan, Taruhan, Diskon, Bayar, Type, Menang
```

### 5. Balance
```
POST /request-balance
X-Requested-With: XMLHttpRequest
→ Response: saldo current (angka, misal "40013.00")
```

### 6. WebSocket (Real-time results)
```
wss://sbstat.hokibagus.club/smb/az2/socket.io/?EIO=3&transport=websocket
→ Socket.io v3, push event hasil draw
```

### 7. Session Check
```
GET /json/post/validate-login
→ Check apakah session masih aktif
```

---

## ARCHITECTURE

```
hokidraw-bot/
├── config.py              # Semua konfigurasi
├── main.py                # Entry point + scheduler
├── modules/
│   ├── auth.py            # Login, CSRF token, session management
│   ├── scraper.py         # Ambil history results + detect new draw
│   ├── predictor.py       # Analisis statistik + LLM call via OpenRouter
│   ├── bettor.py          # Submit taruhan ke /games/4d/send
│   ├── money_manager.py   # Soft Martingale logic + daily limits
│   ├── notifier.py        # Telegram bot notifications
│   └── database.py        # SQLite operations
├── data/
│   └── hokidraw.db        # SQLite database
├── logs/
│   └── bot.log            # Rotating log files
├── .env                   # Environment variables (credentials)
└── requirements.txt
```

---

## CONFIG.PY STRUCTURE

```python
# .env file:
PARTAI_USERNAME=xxx
PARTAI_PASSWORD=xxx
OPENROUTER_API_KEY=sk-or-xxx
TELEGRAM_BOT_TOKEN=xxx
TELEGRAM_CHAT_ID=xxx

# config.py constants:
BASE_URL = "https://partai34848.com"
POOL_ID = "p76368"
GAME_TYPE = "quick_2d"
BET_TYPE = "B"  # BET FULL
BET_POSITION = "belakang"

# Timing
POLL_INTERVAL_SECONDS = 120  # Check every 2 minutes for new results
POLL_START_MINUTE = 5        # Start polling at :05 each hour
BET_DEADLINE_MINUTES = 50    # Stop betting at :50 (safety margin)

# Money Management - Soft Martingale (50% win rate)
BASE_BET = 0.1        # Rp 100 per angka (× 50 angka = Rp 5.000 total)
BET_MODE = "double"   # "single" = 1 bet, "double" = 2 bets (BK + GJ)
MARTINGALE_MULTIPLIER = 2.0
MARTINGALE_STEP_LOSSES = 3  # Naik level setiap 3 consecutive losses
MARTINGALE_CEILING = 5      # Max level
DAILY_LOSS_LIMIT = 200000   # Rp 200.000 per hari

# Martingale Levels (bet per angka dalam ribuan, untuk API)
# Total per bet = level × 50 angka × 1000
MARTINGALE_LEVELS = [0.1, 0.2, 0.4, 0.8, 1.6]  # Rp 100 → Rp 1.600 per angka

# LLM
OPENROUTER_MODEL = "google/gemini-2.0-flash-001"
OPENROUTER_FALLBACK = "anthropic/claude-sonnet-4-20250514"
HISTORY_WINDOW = 200  # Jumlah periode terakhir untuk analisis
```

---

## MODULE SPECIFICATIONS

### 1. auth.py
```
class AuthManager:
  - __init__(session: httpx.AsyncClient)
  - get_csrf_token() -> str
      # GET homepage, parse _token dari HTML/meta tag
  - login(username, password) -> bool
      # POST /json/post/ceklogin-ts
  - check_session() -> bool
      # GET /json/post/validate-login
  - ensure_logged_in()
      # Check session, re-login if expired
  - get_balance() -> float
      # POST /request-balance

PENTING: Situs menggunakan Cloudflare protection.
Gunakan httpx dengan headers yang meniru browser:
  User-Agent: Chrome terbaru
  Accept, Accept-Language, etc.
Jika Cloudflare memblokir, fallback ke Playwright headless browser.
```

### 2. scraper.py
```
class Scraper:
  - __init__(session, db)
  - get_timer() -> int
      # GET https://jampasaran.smbgroup.io/pasaran
      # Return detik tersisa untuk hokidraw
  - get_current_period() -> str
      # GET /games/4d/p76368, parse hidden field "periode"
  - get_history(limit=200) -> list[dict]
      # GET /history/detail/data/p76368-1
      # Parse JSON response: [{periode, tanggal, nomor}]
      # Jika endpoint JSON kosong, fallback: GET /games/4d/history/quick_2d/p76368
      # Parse HTML table sebagai fallback
  - detect_new_result() -> Optional[dict]
      # Poll dan compare dengan last known result di DB
      # Return new result jika ada
  - get_bet_history() -> list[dict]
      # GET /games/4d/history/quick_2d/p76368
      # Parse HTML table: tebakan, taruhan, type, menang
```

### 3. predictor.py
```
class Predictor:
  - __init__(openrouter_api_key, model)
  - analyze(history: list[dict]) -> dict
      # Kirim data history ke LLM via OpenRouter
      # LLM menganalisis pola besar/kecil dan genap/ganjil
      # Return: {
      #   "besar_kecil": {"choice": "BE", "confidence": 0.65, "reason": "..."},
      #   "genap_ganjil": {"choice": "GA", "confidence": 0.72, "reason": "..."}
      # }

  Prompt template:
  """
  Kamu adalah analis statistik togel. Analisis data 2D Belakang berikut
  dan rekomendasikan taruhan untuk periode berikutnya.

  Data {n} periode terakhir pasaran Hokidraw (2D Belakang):
  {history_table}

  Kolom data: Periode | 2D Belakang | Besar/Kecil | Genap/Ganjil

  Analisis yang harus dilakukan:
  1. Hitung frekuensi BESAR vs KECIL dari {n} periode terakhir
  2. Hitung frekuensi GENAP vs GANJIL dari {n} periode terakhir
  3. Analisis streak/pola terakhir (apakah ada pola berturut-turut?)
  4. Analisis 10 dan 20 periode terakhir (trend terkini)
  5. Identifikasi apakah ada bias signifikan

  ATURAN KLASIFIKASI:
  - BESAR = angka 50-99 (digit pertama 5-9)
  - KECIL = angka 00-49 (digit pertama 0-4)
  - GENAP = digit terakhir 0,2,4,6,8
  - GANJIL = digit terakhir 1,3,5,7,9

  Respond HANYA dalam format JSON berikut, tanpa teks lain:
  {
    "besar_kecil": {
      "choice": "BE" atau "KE",
      "confidence": 0.XX,
      "reason": "penjelasan singkat"
    },
    "genap_ganjil": {
      "choice": "GA" atau "GE",
      "confidence": 0.XX,
      "reason": "penjelasan singkat"
    },
    "stats": {
      "besar_count": N,
      "kecil_count": N,
      "genap_count": N,
      "ganjil_count": N,
      "last_10_bk": "BBKBKKBKBB",
      "last_10_gj": "GGJGGGJGGG"
    }
  }
  """

  OpenRouter API call:
  POST https://openrouter.ai/api/v1/chat/completions
  Headers:
    Authorization: Bearer $OPENROUTER_API_KEY
    Content-Type: application/json
  Body:
    model: "google/gemini-2.0-flash-001"
    messages: [{role: "user", content: prompt}]
    temperature: 0.3
    max_tokens: 1000
```

### 4. bettor.py
```
class Bettor:
  - __init__(session, pool_id)
  - generate_numbers(choice: str) -> list[str]
      # Generate 50 angka berdasarkan pilihan
      # BE → [50,51,...,99], KE → [00,01,...,49]
      # GA → [01,03,...,99], GE → [00,02,...,98]
  - place_bet(choice: str, bet_amount_per_angka: float) -> dict
      # choice = "BE", "KE", "GA", atau "GE"
      # bet_amount_per_angka = dalam ribuan (0.1 = Rp 100)
      #
      # 1. Generate 50 angka dari choice
      # 2. Build form data:
      #    type=B, ganti=F, game=quick_2d, bet={amount}
      #    posisi=belakang, sar=p76368
      #    cek1=1, tebak1={numbers[0]}, cek2=1, tebak2={numbers[1]}, ...
      # 3. POST /games/4d/send
      # 4. Return: {"status": bool, "periode": str, "balance": float, "error": str}
  - place_double_bet(bk_choice: str, gj_choice: str, bet_amount: float) -> list[dict]
      # Pasang 2 taruhan sekaligus:
      # 1x untuk besar/kecil + 1x untuk genap/ganjil
      # Return list of 2 responses
  - validate_bet(choice, amount) -> bool
      # Validate: choice in [BE,KE,GA,GE], amount within limits
```

### 5. money_manager.py
```
class MoneyManager:
  - __init__(db, config)
  - get_current_level() -> int
      # Hitung level berdasar consecutive losses
  - get_bet_amount() -> float
      # Return nominal bet per angka dalam ribuan (untuk API)
      # Level 1: 0.1 (Rp 100/angka × 50 = Rp 5.000 total)
      # Level 2: 0.15 (Rp 150/angka × 50 = Rp 7.500 total)
      # etc.
  - record_result(period, bets_placed: list[dict], result_2d: str) -> dict
      # Evaluate win/loss untuk setiap bet (bk + gj)
      # Update consecutive loss counter
      # Return: {"bk_win": bool, "gj_win": bool, "total_profit": float}
  - on_win() -> None
      # Reset ke level 1
  - on_loss() -> None
      # Increment loss counter, check step-up
  - check_daily_limit() -> bool
      # Return False jika daily loss limit tercapai
  - get_stats() -> dict
      # Total bet, total win, profit/loss, current level, etc.

  Soft Martingale Logic (disesuaikan untuk win rate 50%):
  - consecutive_losses tracked TERPISAH untuk BK dan GJ
  - Level up setiap 3 consecutive losses (bukan 5, karena 50% win rate
    membuat 3 loss berturut = 12.5% chance, cukup jarang)
  - Max level = 5 (ceiling)
  - Win → reset ke level 1
  - Daily loss limit → bot pause sampai 00:00 WIB

  MARTINGALE LEVELS (per angka, dalam Rupiah):
  Level 1: Rp 100  (total Rp 5.000)   ← base
  Level 2: Rp 200  (total Rp 10.000)  ← setelah 3 loss
  Level 3: Rp 400  (total Rp 20.000)  ← setelah 6 loss
  Level 4: Rp 800  (total Rp 40.000)  ← setelah 9 loss
  Level 5: Rp 1.600 (total Rp 80.000) ← ceiling, setelah 12 loss

  Dengan 50% win rate, probabilitas mencapai tiap level:
  Level 2 (3 loss): 12.5%
  Level 3 (6 loss): 1.56%
  Level 4 (9 loss): 0.20%
  Level 5 (12 loss): 0.024%
```

### 6. notifier.py
```
class TelegramNotifier:
  - __init__(bot_token, chat_id)
  - send_message(text) -> None
  - notify_bet_placed(bk_choice, gj_choice, amount, results) -> None
      # "🎯 BET Periode 11038 - HOKIDRAW
      #  Posisi: 2D Belakang
      #  Besar/Kecil: BESAR (confidence: 65%)
      #  Genap/Ganjil: GANJIL (confidence: 72%)
      #  Bet: Rp 100/angka × 50 = Rp 5.000 per taruhan
      #  Total: Rp 10.000 (2 taruhan)
      #  Level Martingale: 1"
  - notify_result(period, result, classification, wins) -> None
      # "📊 HASIL Periode 11038: 1295 (2D=95)
      #  → BESAR ✅ + GANJIL ✅
      #  Profit: +Rp 10.000 | Saldo: Rp 50.000"
      # atau
      # "📊 HASIL Periode 11038: 3246 (2D=46)
      #  → KECIL (bet: BESAR ❌) + GENAP (bet: GANJIL ❌)
      #  Rugi: -Rp 10.000 | Level: 1→2"
  - notify_daily_summary(stats) -> None
      # "📈 Ringkasan Hari Ini
      #  Periode: 24 | BK Win: 14/24 | GJ Win: 11/24
      #  Total Bet: Rp 240.000 | Total Win: Rp 250.000
      #  Profit: +Rp 10.000 | Saldo: Rp 550.000"
  - notify_alert(message) -> None
      # Saldo rendah, error, daily limit reached, dll
```

### 7. database.py
```
SQLite schema:

CREATE TABLE results (
    id INTEGER PRIMARY KEY,
    period TEXT UNIQUE,
    draw_time DATETIME,
    full_number TEXT,       -- 4 digit: "1295"
    number_2d_depan TEXT,   -- "12"
    number_2d_tengah TEXT,  -- "29"
    number_2d_belakang TEXT,-- "95"
    belakang_bk TEXT,       -- "BE" atau "KE"
    belakang_gj TEXT,       -- "GA" atau "GE"
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE bets (
    id INTEGER PRIMARY KEY,
    period TEXT,
    bet_dimension TEXT,      -- "besar_kecil" atau "genap_ganjil"
    bet_choice TEXT,         -- "BE", "KE", "GA", "GE"
    bet_amount_per_angka REAL, -- dalam ribuan (0.1 = Rp 100)
    total_amount REAL,       -- bet × 50 × 1000 (dalam Rupiah)
    martingale_level INTEGER,
    status TEXT,             -- "placed", "won", "lost"
    win_amount REAL DEFAULT 0,
    result_2d TEXT,          -- 2D belakang yang keluar
    result_match TEXT,       -- "BE"/"KE"/"GA"/"GE" yang keluar
    api_response TEXT,       -- raw response from /games/4d/send
    confidence REAL,         -- confidence score dari LLM
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE daily_stats (
    date TEXT PRIMARY KEY,
    total_bets INTEGER DEFAULT 0,
    total_wins INTEGER DEFAULT 0,
    total_bet_amount REAL DEFAULT 0,
    total_win_amount REAL DEFAULT 0,
    profit REAL DEFAULT 0,
    ending_balance REAL DEFAULT 0
);

CREATE TABLE bot_state (
    key TEXT PRIMARY KEY,
    value TEXT,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
-- Keys: "consecutive_losses", "martingale_level", "last_period", "daily_loss"
```

### 8. main.py (Entry Point)
```
import asyncio
from apscheduler.schedulers.asyncio import AsyncIOScheduler

async def main():
    # 1. Initialize all modules
    # 2. Login
    # 3. Start scheduler

    scheduler = AsyncIOScheduler()

    # Run bot cycle every hour
    # Trigger at :05, :07, :09... until new result detected
    # Then: predict → bet → wait for next hour

    async def hourly_cycle():
        """Main bot cycle - runs each hour"""
        # Step 1: Ensure logged in
        await auth.ensure_logged_in()

        # Step 2: Poll for new result (from previous period)
        result = None
        for attempt in range(10):  # Max 10 attempts (20 minutes)
            result = await scraper.detect_new_result()
            if result:
                break
            await asyncio.sleep(120)  # Wait 2 minutes

        if result:
            # Step 3: Classify result and record
            # result = "1295" → 2D belakang = "95" → BE + GA
            classification = classify_result(result["number_2d_belakang"])
            was_win = money_manager.record_result(result, classification)
            await notifier.notify_result(result, classification, was_win)

        # Step 4: Check daily limit
        if not money_manager.check_daily_limit():
            await notifier.notify_alert("Daily loss limit reached. Bot paused.")
            return

        # Step 5: Get history & predict
        history = await scraper.get_history(limit=200)
        prediction = await predictor.analyze(history)
        # prediction = {
        #   "besar_kecil": {"choice": "BE", "confidence": 0.65},
        #   "genap_ganjil": {"choice": "GA", "confidence": 0.72}
        # }

        # Step 6: Get bet amount from martingale
        bet_amount = money_manager.get_bet_amount()

        # Step 7: Place bets (1 or 2 depending on config)
        bk_choice = prediction["besar_kecil"]["choice"]   # "BE" or "KE"
        gj_choice = prediction["genap_ganjil"]["choice"]   # "GA" or "GE"

        results = await bettor.place_double_bet(bk_choice, gj_choice, bet_amount)
        await notifier.notify_bet_placed(bk_choice, gj_choice, bet_amount, results)

    # Schedule: run at :05 past every hour
    scheduler.add_job(hourly_cycle, 'cron', minute=5)

    # Daily summary at 23:55
    scheduler.add_job(daily_summary, 'cron', hour=23, minute=55)

    scheduler.start()
    await asyncio.Event().wait()  # Run forever

asyncio.run(main())
```

---

## REQUIREMENTS.TXT

```
httpx[http2]>=0.27.0
beautifulsoup4>=4.12.0
apscheduler>=3.10.0
python-dotenv>=1.0.0
python-telegram-bot>=21.0
openai>=1.30.0       # Compatible with OpenRouter
aiosqlite>=0.20.0
lxml>=5.0.0
```

---

## GIT & GITHUB WORKFLOW

### Repository Structure
```
hokidraw-bot/
├── .github/
│   └── workflows/         # (optional) CI checks
├── modules/
│   ├── __init__.py
│   ├── auth.py
│   ├── scraper.py
│   ├── predictor.py
│   ├── bettor.py
│   ├── money_manager.py
│   ├── notifier.py
│   └── database.py
├── data/                  # .gitkeep only, DB created at runtime
│   └── .gitkeep
├── logs/                  # .gitkeep only, logs created at runtime
│   └── .gitkeep
├── config.py
├── main.py
├── requirements.txt
├── .env.example           # Template tanpa credentials
├── .gitignore
├── README.md
└── HOKIDRAW_BOT_BLUEPRINT.md  # Dokumen ini
```

### .gitignore (PENTING — jangan commit credentials!)
```
.env
data/*.db
logs/*.log
__pycache__/
*.pyc
venv/
.vscode/
```

### .env.example (commit ini sebagai template)
```
PARTAI_USERNAME=your_username_here
PARTAI_PASSWORD=your_password_here
OPENROUTER_API_KEY=sk-or-your_key_here
TELEGRAM_BOT_TOKEN=your_bot_token_here
TELEGRAM_CHAT_ID=your_chat_id_here
```

### Claude Code Git Commands
Saat bekerja di Claude Code dengan GitHub repo:
```bash
# Clone / init repo
git init
git remote add origin https://github.com/<username>/hokidraw-bot.git

# Setelah selesai build setiap modul
git add -A
git commit -m "feat: add auth module with CSRF + login"
git push origin main
```

Commit secara incremental per modul:
1. `feat: project setup + config + database schema`
2. `feat: auth module (login, CSRF, session)`
3. `feat: scraper module (history, results, timer)`
4. `feat: predictor module (OpenRouter LLM integration)`
5. `feat: bettor module (place bet API)`
6. `feat: money manager (soft martingale)`
7. `feat: telegram notifier`
8. `feat: main.py scheduler + full integration`
9. `feat: dry-run mode for testing`

---

## DEPLOYMENT — Pull ke VPS

Setelah semua code di GitHub, tarik ke VPS:

```bash
# 1. Setup VPS
sudo apt update && sudo apt install -y python3.11 python3.11-venv git

# 2. Clone repo
cd ~
git clone https://github.com/<username>/hokidraw-bot.git
cd hokidraw-bot

# 3. Virtual environment
python3.11 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# 4. Configure credentials
cp .env.example .env
nano .env  # Isi credentials asli

# 5. Test dry-run dulu
python main.py --dry-run

# 6. Run dengan systemd (production)
sudo tee /etc/systemd/system/hokidraw-bot.service << 'EOF'
[Unit]
Description=Hokidraw 2D Auto-Betting Bot
After=network.target

[Service]
User=ubuntu
WorkingDirectory=/home/ubuntu/hokidraw-bot
Environment=PATH=/home/ubuntu/hokidraw-bot/venv/bin:$PATH
ExecStart=/home/ubuntu/hokidraw-bot/venv/bin/python main.py
Restart=always
RestartSec=30

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable hokidraw-bot
sudo systemctl start hokidraw-bot

# 7. Monitor
sudo journalctl -u hokidraw-bot -f

# 8. Update dari GitHub (kalau ada perubahan)
cd ~/hokidraw-bot
git pull origin main
sudo systemctl restart hokidraw-bot
```

---

## CLOUDFLARE HANDLING STRATEGY

Situs menggunakan Cloudflare protection. Strategi:

1. **Primary**: httpx dengan headers browser lengkap + cookie jar persisten
   - Copy semua headers dari HAR file (User-Agent, Accept, dll)
   - Maintain cookies across requests

2. **Fallback**: Jika httpx diblokir, gunakan Playwright headless browser
   ```python
   from playwright.async_api import async_playwright
   # Launch headless Chromium
   # Navigate, solve Cloudflare challenge
   # Extract cookies → transfer ke httpx untuk API calls
   ```

3. **Cookie refresh**: Setiap 30 menit, re-validate session

---

## IMPORTANT NOTES

1. **Win rate 50% per taruhan** — Ini jauh lebih baik dari menebak angka spesifik (1%). Martingale lebih viable di sini, tapi tetap ada risiko losing streak.

2. **2 taruhan per periode** — Bot memasang 2 taruhan independen (besar/kecil + genap/ganjil). Masing-masing punya win rate 50% dan martingale level terpisah.

3. **Daily loss limit WAJIB diimplementasi** — meskipun win rate 50%, bad luck streak bisa terjadi.

4. **Mulai dengan bet minimum** (Rp 100/angka = Rp 5.000/bet) untuk testing.

5. **Log SEMUA aktivitas** — setiap API call, setiap prediksi, setiap taruhan.

6. **CSRF Token** — harus diambil fresh sebelum setiap login.

7. **Bet amount format** — dikirim dalam ribuan (bet=0.1 artinya Rp 100 per angka × 50 angka = Rp 5.000 total).

8. **Test dulu dengan dry-run mode** — simulasi tanpa betting sungguhan.

9. **Klasifikasi hasil** — Bot harus parse angka 4 digit menjadi 2D belakang, lalu klasifikasi ke besar/kecil dan genap/ganjil untuk menentukan win/loss.

10. **Satu bet = 50 angka** — Setiap kali bot memasang "BESAR", dia mengirim 50 request angka (50-99) ke API. Ini normal dan sesuai cara kerja situs.
