# Hokidraw 2D Auto-Betting Bot

Bot otomatis untuk memasang taruhan **2D Belakang (Besar/Kecil + Genap/Ganjil)** pada pasaran Hokidraw.
Menggunakan **OpenRouter LLM** untuk analisis pola dan prediksi, **Telegram** untuk notifikasi.

---

## Mekanisme Permainan

Hokidraw menghasilkan 4 digit angka per draw. Bot fokus pada **2D Belakang** (2 digit terakhir).

```
Contoh hasil: 1 2 9 5
                    └─┘ ← 2D Belakang = 95  ← FOKUS BOT
```

Angka 2D Belakang diklasifikasikan ke 2 dimensi:

| Dimensi | Aturan | Contoh "95" |
|---|---|---|
| **Besar/Kecil** | Nilai keseluruhan: 00–49 = KECIL, 50–99 = BESAR | 95 ≥ 50 → **BESAR** |
| **Genap/Ganjil** | Digit terakhir: 0,2,4,6,8 = GENAP; 1,3,5,7,9 = GANJIL | digit terakhir 5 → **GANJIL** |

### 4 Pilihan Taruhan

| Kode | Nama | Angka yang Dicakup | Win Rate |
|---|---|---|---|
| `BE` | BESAR | 50, 51, 52, ..., 99 (50 angka) | 50% |
| `KE` | KECIL | 00, 01, 02, ..., 49 (50 angka) | 50% |
| `GE` | GENAP | 00, 02, 04, ..., 98 (50 angka) | 50% |
| `GA` | GANJIL | 01, 03, 05, ..., 99 (50 angka) | 50% |

### Ekonomi Bet (default `BASE_BET=100`, mode `double`)

```
Bet BESAR  = 50 angka × Rp 100 = Rp 5.000  →  menang: Rp 10.000  (+Rp 5.000)
Bet GANJIL = 50 angka × Rp 100 = Rp 5.000  →  menang: Rp 10.000  (+Rp 5.000)
─────────────────────────────────────────────────────────────────────────────
Total modal per periode = Rp 10.000

Outcome (tiap dimensi independen, 50% win rate):
  Keduanya menang (25%)  → +Rp 10.000
  Satu menang (50%)      →  Rp 0 (impas)
  Keduanya kalah (25%)   → -Rp 10.000
```

---

## Fitur Utama

- **Prediksi LLM** — analisis 200 hasil terakhir via OpenRouter (Gemini 2.0 Flash / Claude)
- **Betting BK + GJ** — 2 bet independen per periode: Besar/Kecil + Genap/Ganjil
- **Martingale Terpisah** — level BK dan GJ dikelola **sendiri-sendiri**
- **5 Level Martingale** — Rp100 → Rp200 → Rp400 → Rp800 → Rp1.600/angka
- **Daily Loss Limit** — bot berhenti otomatis jika rugi harian melebihi batas
- **Notifikasi Telegram** — bet, hasil, dan summary harian
- **Cloudflare Bypass** — httpx primary + Playwright headless sebagai fallback
- **Dry-run Mode** — simulasi penuh tanpa bet sungguhan
- **Domain Fleksibel** — ganti `SITE_URL` di `.env` jika domain berubah, tanpa ubah kode

---

## Struktur Proyek

```
barbatos/
├── main.py                  # Entry point + APScheduler
├── config.py                # Baca semua setting dari .env
├── .env.example             # Template konfigurasi lengkap
├── requirements.txt         # Dependensi Python
├── setup.sh                 # Installer otomatis Ubuntu VPS
├── hokidraw-bot.service     # Systemd unit file
├── modules/
│   ├── auth.py              # Login, CSRF token, session, Playwright CF-bypass
│   ├── scraper.py           # Ambil history draw, periode aktif, timer
│   ├── categories.py        # Klasifikasi BE/KE/GE/GA + generator 50 angka
│   ├── predictor.py         # Analisis LLM → prediksi BK + GJ
│   ├── bettor.py            # Submit 50 angka per kategori, cek menang
│   ├── money_manager.py     # Soft martingale terpisah BK/GJ + daily limit
│   ├── notifier.py          # Notifikasi Telegram
│   └── database.py          # SQLite: results, bets, daily_stats, bot_state
├── data/                    # SQLite DB (dibuat saat runtime)
└── logs/                    # Log file (dibuat saat runtime)
```

---

## Cara Install di VPS Ubuntu

### Prasyarat
- Ubuntu 22.04+ dengan Python 3.11
- Akun aktif di situs togel (lihat `SITE_URL` di `.env`)
- API key OpenRouter → [openrouter.ai/keys](https://openrouter.ai/keys)
- (Opsional) Telegram Bot Token dari [@BotFather](https://t.me/BotFather)

### Install Otomatis

```bash
# 1. Clone repo
git clone https://github.com/anunnaki13/barbatos.git
cd barbatos

# 2. Jalankan setup
bash setup.sh
```

`setup.sh` akan otomatis:
- Install Python 3.11 + semua dependensi
- Buat virtual environment
- Install Playwright Chromium (untuk bypass Cloudflare)
- Buat file `.env` dari template
- Daftarkan systemd service

---

## Konfigurasi `.env`

**Edit satu file ini saja — tidak perlu ubah kode Python apapun.**

```bash
nano /opt/hokidraw-bot/.env
```

### Wajib Diisi

| Setting | Keterangan | Contoh |
|---|---|---|
| `SITE_URL` | Alamat website — **ganti di sini jika domain berubah** | `https://partai34848.com` |
| `POOL_ID` | ID pasaran (lihat di URL halaman game) | `p76368` |
| `PARTAI_USERNAME` | Username akun | `user123` |
| `PARTAI_PASSWORD` | Password akun | `pass123` |
| `OPENROUTER_API_KEY` | API key dari openrouter.ai | `sk-or-xxx...` |

### Betting

| Setting | Default | Keterangan |
|---|---|---|
| `BASE_BET` | `100` | Nominal per **angka** (IDR). Total/bet = BASE_BET × 50 angka |
| `BET_MODE` | `double` | `double` = 2 bet/periode (BK+GJ) · `single` = 1 bet (confidence tertinggi) |
| `BET_TYPE` | `B` | `B` = BET FULL payout ×100 · `D` = diskon |

### Martingale

> BK dan GJ punya level **terpisah** — menang salah satu tidak mereset yang lain.

| Setting | Default | Keterangan |
|---|---|---|
| `MARTINGALE_LEVELS` | `100,200,400,800,1600` | Nominal per angka tiap level (IDR), pisah koma |
| `MARTINGALE_STEP_LOSSES` | `3` | Naik 1 level setiap N kekalahan berturut-turut |
| `DAILY_LOSS_LIMIT` | `200000` | Batas rugi harian (Rp). Bot pause sampai tengah malam WIB |

Probabilitas mencapai tiap level (win rate 50%):

| Level | Bet/angka | Total/bet | Syarat (loss berturut) | Probabilitas |
|---|---|---|---|---|
| 0 | Rp 100 | Rp 5.000 | — | — |
| 1 | Rp 200 | Rp 10.000 | 3 loss | 12.5% |
| 2 | Rp 400 | Rp 20.000 | 6 loss | 1.56% |
| 3 | Rp 800 | Rp 40.000 | 9 loss | 0.20% |
| 4 | Rp 1.600 | Rp 80.000 | 12 loss | 0.024% |

### Telegram (Opsional)

| Setting | Keterangan |
|---|---|
| `TELEGRAM_BOT_TOKEN` | Token dari @BotFather |
| `TELEGRAM_CHAT_ID` | ID chat tujuan notifikasi |

> **Cara dapat CHAT_ID:** Kirim pesan ke bot Anda → buka
> `https://api.telegram.org/bot<TOKEN>/getUpdates` → cari `"chat":{"id":...}`

### LLM

| Setting | Default | Keterangan |
|---|---|---|
| `LLM_MODEL` | `google/gemini-2.0-flash-001` | Model utama (lihat model di openrouter.ai) |
| `LLM_FALLBACK_MODEL` | `anthropic/claude-sonnet-4-20250514` | Model cadangan jika utama gagal |
| `LLM_TEMPERATURE` | `0.3` | 0.0 = deterministik · 1.0 = paling acak |

---

## Menjalankan Bot

```bash
cd /opt/hokidraw-bot
source venv/bin/activate

# 1. Cek konfigurasi (tanpa jalankan bot)
python main.py --check-config

# 2. Test dry-run (simulasi penuh, tidak ada bet sungguhan)
python main.py --dry-run

# 3. Live
python main.py
```

### Sebagai Systemd Service

```bash
# Aktifkan & jalankan
sudo systemctl enable --now hokidraw-bot

# Cek status
sudo systemctl status hokidraw-bot

# Log real-time
journalctl -u hokidraw-bot -f
tail -f /opt/hokidraw-bot/logs/bot.log

# Hentikan / restart
sudo systemctl stop hokidraw-bot
sudo systemctl restart hokidraw-bot   # wajib setelah edit .env

# Update dari GitHub
cd /opt/hokidraw-bot
git pull origin main
sudo systemctl restart hokidraw-bot
```

---

## Alur Bot per Jam

```
Menit :05 setiap jam (WIB)
│
├─ [1] Login check / re-login jika sesi expired
│
├─ [2] Poll hasil draw baru (maks 10x, interval 2 menit)
│       └─ Parse 4D → simpan depan/tengah/belakang + BK/GJ ke DB
│
├─ [3] Settle pending bets
│       ├─ Cek BK: BE/KE cocok dengan belakang_bk?
│       ├─ Cek GJ: GE/GA cocok dengan belakang_gj?
│       ├─ Update martingale BK (terpisah dari GJ)
│       └─ Kirim notifikasi hasil
│
├─ [4] Cek daily loss limit → pause jika tercapai
│
├─ [5] Ambil 200 history → kirim ke LLM
│       └─ LLM analisis frekuensi, streak, trend 10 & 20 periode terakhir
│
├─ [6] LLM return: {besar_kecil: {choice:"BE", confidence:0.65}, genap_ganjil: {...}}
│
├─ [7] Ambil bet amount (per dimensi, sesuai level martingale masing-masing)
│
├─ [8] Place double bet:
│       ├─ Bet BK: POST /games/4d/send dengan 50 angka (BE atau KE)
│       └─ Bet GJ: POST /games/4d/send dengan 50 angka (GE atau GA)
│
└─ [9] Notifikasi Telegram: periode, pilihan, confidence, total taruhan

Pukul 23:55 WIB
  └─ Daily summary (total bet, win rate, profit/loss, saldo akhir)
     + Reset daily_loss counter
```

---

## Database SQLite

Semua data disimpan di `data/hokidraw.db`:

| Tabel | Isi |
|---|---|
| `results` | History draw: 4D lengkap + depan/tengah/belakang + belakang_bk/gj |
| `bets` | Setiap bet: 1 row per dimensi (BK atau GJ), ada kolom `bet_choice`, `confidence`, `result_match`, `win_amount` |
| `daily_stats` | Per tanggal: total bet, win, rugi/untung, saldo akhir |
| `bot_state` | State persisten: `consecutive_losses_bk/gj`, `martingale_level_bk/gj`, `daily_loss` |

---

## Notifikasi Telegram

**Saat bet dipasang:**
```
🎯 BET Periode 11038
Posisi: 2D Belakang
Besar/Kecil : BESAR (confidence: 65%) — Level 0
Genap/Ganjil: GANJIL (confidence: 72%) — Level 0
Bet: Rp100/angka × 50 = Rp5.000 per taruhan
Total: Rp10.000 (2 taruhan)
```

**Saat hasil keluar:**
```
📊 HASIL Periode 11038: 1295 (2D=95)
→ ✅ Besar/Kecil: BESAR (bet: BESAR)
→ ✅ Genap/Ganjil: GANJIL (bet: GANJIL)
Profit: +Rp10.000 | Saldo: Rp60.000
```

**Daily summary (23:55 WIB):**
```
📈 Ringkasan Hari Ini — 2026-04-08
Periode: 24 bet | Win: 14/24 (58.3%)
Total Bet: Rp240.000 | Total Win: Rp280.000
Profit: +Rp40.000 | Saldo: Rp590.000
```

---

## Jika Domain Website Berubah

Cukup update **satu baris** di `.env`:

```bash
# Sebelum
SITE_URL=https://partai34848.com

# Sesudah
SITE_URL=https://partaibaru99.com
```

```bash
sudo systemctl restart hokidraw-bot
```

---

## Catatan Penting

> **Win rate ~50% per dimensi** — jauh lebih baik dari tebak angka spesifik (1%). Martingale lebih viable, tapi losing streak tetap bisa terjadi.

> **Mulai dengan bet minimum** (`BASE_BET=100`) untuk testing minimal 24 jam sebelum naikkan nominal.

> **Daily Loss Limit wajib diset** sesuai modal Anda. Default Rp 200.000/hari.

> **Prediksi LLM bersifat statistik** — menganalisis pola distribusi dan streak, bukan menjamin profit.

---

## Dokumentasi Teknis

- [`HOKIDRAW_API_DOCUMENTATION.md`](HOKIDRAW_API_DOCUMENTATION.md) — API endpoints hasil reverse engineering HAR
- [`HOKIDRAW_BOT_BLUEPRINT.md`](HOKIDRAW_BOT_BLUEPRINT.md) — Blueprint dan spesifikasi lengkap bot
