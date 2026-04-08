# Hokidraw 2D Lottery Bot

Bot otomatis untuk pasang, kelola, dan prediksi betting 2D Togel di pasar Hokidraw.  
Menggunakan **OpenRouter LLM** untuk prediksi nomor dan **Telegram** untuk notifikasi.

---

## Cara Main (Mekanisme 2D)

Setiap hasil draw Hokidraw adalah 4 digit, dibagi 3 pasangan posisi:

```
Contoh hasil: 1 2 9 5
              ├─┤ ├─┤ ← Depan=12, Tengah=29, Belakang=95
                  ├─┤ ← Tengah=29
                  └─┤ ← Belakang=95 (overlap Tengah)
```

Setiap pasangan diklasifikasikan:

| Pasangan | Besar/Kecil (digit pertama) | Genap/Ganjil (digit kedua) |
|---|---|---|
| `12` | **Kecil** (1 < 5) | **Genap** (2 → 0,2,4,6,8) |
| `29` | **Kecil** (2 < 5) | **Ganjil** (9 → 1,3,5,7,9) |
| `95` | **Besar** (9 ≥ 5) | **Ganjil** (5 → 1,3,5,7,9) |

Bot memprediksi kategori untuk draw berikutnya dan memasang:
- **Besar/Kecil** → 50 angka yang memenuhi kategori
- **Genap/Ganjil** → 50 angka yang memenuhi kategori

**Ekonomi bet** (BASE_BET=100):
| | Jumlah Nomor | Taruhan | Potensi Win | Profit Bersih |
|---|---|---|---|---|
| Besar atau Kecil | 50 | Rp 5.000 | Rp 10.000 | Rp 5.000 |
| Genap atau Ganjil | 50 | Rp 5.000 | Rp 10.000 | Rp 5.000 |
| **Total/periode** | 100 | **Rp 10.000** | maks Rp 20.000 | maks Rp 10.000 |

---

## Fitur Utama

- **Prediksi LLM** — analisis 200 hasil terakhir via OpenRouter (Gemini / Claude)
- **Betting BK/GJ** — Besar/Kecil + Genap/Ganjil, bukan tebak angka spesifik
- **Multi-posisi** — bisa bet Depan, Tengah, Belakang, atau kombinasinya
- **Soft Martingale** — 7 level, naik otomatis setelah N kekalahan berturut-turut
- **Daily Loss Limit** — bot berhenti otomatis jika rugi harian melebihi batas
- **Notifikasi Telegram** — setiap bet, hasil menang/kalah, dan summary harian
- **Cloudflare Bypass** — httpx primary, Playwright headless sebagai fallback
- **Dry-run Mode** — test semua alur tanpa bet sungguhan
- **Domain fleksibel** — ganti `SITE_URL` di `.env` jika domain web berubah

---

## Struktur Proyek

```
barbatos/
├── main.py                  # Entry point & scheduler (APScheduler)
├── config.py                # Baca semua setting dari .env
├── .env.example             # Template konfigurasi (salin ke .env)
├── requirements.txt         # Dependensi Python
├── setup.sh                 # Installer otomatis untuk Ubuntu VPS
├── hokidraw-bot.service     # Systemd unit file
├── modules/
│   ├── auth.py              # Login, CSRF, validasi sesi, Playwright fallback
│   ├── scraper.py           # Ambil history draw, periode, timer
│   ├── categories.py        # Parser BK/GJ dari hasil 4D + generator angka
│   ├── predictor.py         # Prediksi BK/GJ via OpenRouter LLM
│   ├── bettor.py            # Submit bet 50 angka per kategori, cek menang
│   ├── money_manager.py     # Martingale & daily loss limit
│   ├── notifier.py          # Notifikasi Telegram
│   └── database.py          # SQLite (results, bets, daily_stats, bot_state)
├── data/                    # Database SQLite (auto-dibuat)
└── logs/                    # File log (auto-dibuat)
```

---

## Cara Install di VPS Ubuntu

### Prasyarat
- Ubuntu 22.04+ dengan Python 3.11
- Akun di [partai34848.com](https://partai34848.com) (atau domain aktif saat ini)
- API key OpenRouter: [openrouter.ai/keys](https://openrouter.ai/keys)
- (Opsional) Telegram Bot Token dari [@BotFather](https://t.me/BotFather)

### Install

```bash
# 1. Clone repo
git clone https://github.com/anunnaki13/barbatos.git
cd barbatos

# 2. Jalankan setup otomatis
bash setup.sh
```

Setup akan otomatis:
- Install Python 3.11 + dependencies
- Buat virtual environment
- Install Playwright Chromium (Cloudflare bypass)
- Buat file `.env` dari template
- Daftarkan systemd service

---

## Konfigurasi

Edit satu file ini saja — **tidak perlu ubah kode Python apapun**:

```bash
nano /opt/hokidraw-bot/.env
```

### Isi Wajib

| Setting | Keterangan | Contoh |
|---|---|---|
| `SITE_URL` | Alamat web (ganti jika domain berubah) | `https://partai34848.com` |
| `POOL_ID` | ID pasaran di URL game | `p76368` |
| `PARTAI_USERNAME` | Username akun | `user123` |
| `PARTAI_PASSWORD` | Password akun | `pass123` |
| `OPENROUTER_API_KEY` | API key OpenRouter | `sk-or-xxx...` |

### Betting

| Setting | Default | Keterangan |
|---|---|---|
| `BASE_BET` | `100` | Nominal bet **per nomor** (Rupiah). Total/round = BASE_BET × 50 × 2 × jumlah posisi |
| `BET_POSITIONS` | `belakang` | Posisi yang dibet. Pilihan: `depan`, `tengah`, `belakang`, atau kombinasi pisah koma |
| `BET_TYPE` | `B` | `B`=full (payout 100x), `D`=diskon |

### Martingale

| Setting | Default | Keterangan |
|---|---|---|
| `MARTINGALE_LEVELS` | `100,150,250,400,600,900,1300` | Nominal **per nomor** tiap level (pisah koma). Total/round = level × 50 × 2 × posisi |
| `MARTINGALE_LOSS_THRESHOLD` | `5` | Naik 1 level setiap berapa kekalahan berturut-turut |
| `DAILY_LOSS_LIMIT` | `200000` | Batas rugi harian (Rp) sebelum bot berhenti |

### Telegram (Opsional)

| Setting | Keterangan |
|---|---|
| `TELEGRAM_BOT_TOKEN` | Token dari @BotFather |
| `TELEGRAM_CHAT_ID` | ID chat tujuan notifikasi |

> **Cara dapat CHAT_ID:** Kirim pesan ke bot Anda, buka  
> `https://api.telegram.org/bot<TOKEN>/getUpdates` → cari `"chat":{"id":...}`

### LLM Model

| Setting | Default | Keterangan |
|---|---|---|
| `LLM_MODEL` | `google/gemini-2.0-flash-001` | Model utama (lihat daftar di openrouter.ai) |
| `LLM_FALLBACK_MODEL` | `anthropic/claude-sonnet-4-20250514` | Model cadangan jika utama gagal |
| `LLM_TEMPERATURE` | `0.3` | 0.0 = deterministik, 1.0 = paling random |

---

## Menjalankan Bot

```bash
cd /opt/hokidraw-bot
source venv/bin/activate

# Cek konfigurasi saja
python main.py --check-config

# Test tanpa bet sungguhan
python main.py --dry-run

# Jalankan live
python main.py
```

### Sebagai Service (Otomatis di Background)

```bash
# Aktifkan dan jalankan
sudo systemctl enable --now hokidraw-bot

# Cek status
sudo systemctl status hokidraw-bot

# Lihat log real-time
journalctl -u hokidraw-bot -f
tail -f /opt/hokidraw-bot/logs/bot.log

# Hentikan sementara
sudo systemctl stop hokidraw-bot

# Restart setelah edit .env
sudo systemctl restart hokidraw-bot
```

---

## Alur Bot per Jam

```
Menit :05 tiap jam
  │
  ├─ Cek daily loss limit
  ├─ Validasi sesi login
  │
  ├─ [Poll] Tunggu result baru (maks 10x, interval 2 menit)
  │     └─ Simpan result ke DB
  │
  ├─ Settle pending bet → catat menang/kalah → update martingale
  │
  ├─ Cek daily limit lagi
  │
  ├─ Fetch 200 history draw
  ├─ Kirim ke LLM → dapat 5 nomor + analisis
  │
  ├─ Hitung nominal bet (sesuai level martingale)
  ├─ Submit bet ke website
  │
  └─ Kirim notifikasi Telegram

Menit 23:55 WIB
  └─ Daily summary + reset counter harian
```

---

## Database

Bot menyimpan semua data di `data/hokidraw.db` (SQLite):

| Tabel | Isi |
|---|---|
| `results` | History semua hasil draw |
| `bets` | Semua bet yang dipasang + status menang/kalah |
| `daily_stats` | Statistik harian (total bet, wagered, won, net) |
| `bot_state` | State persisten (consecutive_losses, martingale_level, dll) |

---

## Jika Domain Web Berubah

Cukup update satu baris di `.env`:

```bash
# Ganti dari
SITE_URL=https://partai34848.com
# Menjadi (contoh)
SITE_URL=https://partaibaru99.com
```

Lalu restart bot:
```bash
sudo systemctl restart hokidraw-bot
```

---

## Catatan Penting

> **Prediksi LLM bersifat eksperimental.** Togel adalah permainan acak — LLM menganalisis pola statistik, bukan menjamin profit.

> **Selalu mulai dengan bet minimum** (`BASE_BET=100`) untuk testing sebelum menaikkan nominal.

> **Daily Loss Limit wajib diset** sesuai kemampuan modal Anda. Bot akan berhenti otomatis jika limit tercapai dan resume keesokan harinya.

---

## Dokumentasi Teknis

- [`HOKIDRAW_API_DOCUMENTATION.md`](HOKIDRAW_API_DOCUMENTATION.md) — Dokumentasi API lengkap hasil reverse engineering
- [`HOKIDRAW_BOT_BLUEPRINT.md`](HOKIDRAW_BOT_BLUEPRINT.md) — Blueprint dan spesifikasi teknis bot
