# CryDevil

Bot otomatis untuk memasang taruhan **2D Hokidraw** pada posisi **depan**, **tengah**, atau **belakang**.
Untuk setiap posisi 2D, bot menganalisis dua dimensi taruhan independen: **Besar/Kecil** dan **Genap/Ganjil**.
Menggunakan **OpenRouter LLM** untuk analisis pola dan prediksi, **Telegram** untuk notifikasi real-time.

Untuk layout VPS yang rapi, pola 3 bot, dan coexistence dengan aplikasi bot lain di server yang sama, lihat [DEPLOYMENT.md](DEPLOYMENT.md).

---

## Mekanisme Permainan

Hokidraw menghasilkan 4 digit angka per draw. Dari 4 digit itu terbentuk 3 posisi 2D:

```
Contoh hasil: 1 2 9 5
              └─┘      ← 2D Depan    = "12"
                └─┘    ← 2D Tengah   = "29"
                  └─┘  ← 2D Belakang = "95"
```

Setiap posisi 2D diklasifikasikan ke **2 dimensi independen**:

| Dimensi | Aturan | Contoh "95" |
|---|---|---|
| **Besar/Kecil** | Nilai keseluruhan: 00–49 = KECIL · 50–99 = BESAR | 95 ≥ 50 → **BESAR** |
| **Genap/Ganjil** | Digit terakhir: 0,2,4,6,8 = GENAP · 1,3,5,7,9 = GANJIL | digit akhir 5 → **GANJIL** |

### 4 Pilihan Taruhan (Kode Internal)

| Kode | Nama | Angka yang Dicakup | Win Rate |
|---|---|---|---|
| `BE` | BESAR | 50, 51, 52, ..., 99 — (50 angka) | 50% |
| `KE` | KECIL | 00, 01, 02, ..., 49 — (50 angka) | 50% |
| `GE` | GENAP | 00, 02, 04, ..., 98 — (50 angka) | 50% |
| `GA` | GANJIL | 01, 03, 05, ..., 99 — (50 angka) | 50% |

### Cara Kerja Bet di Situs

Bot menggunakan menu **Quick 2D → Bet Full** (selalu, tidak bisa diubah).

```
Panel situs: IDR 1.000 = 1
Formula     : bet_param = BASE_BET ÷ 1.000

Contoh BASE_BET = 100 (Rp 100/angka):
  bet_param  = 100 ÷ 1.000 = 0.1
  Total/bet  = 0.1 × 1.000 × 50 angka = Rp 5.000
  Payout win = Rp 100/angka × 100x    = Rp 10.000
  Profit     = Rp 10.000 - Rp 5.000   = +Rp 5.000
```

### Ekonomi per Periode (mode `double`, BASE_BET=100)

```
Bet BESAR  → 50 angka × Rp100 = Rp 5.000 modal  |  menang → Rp10.000 (+Rp5.000)
Bet GANJIL → 50 angka × Rp100 = Rp 5.000 modal  |  menang → Rp10.000 (+Rp5.000)
──────────────────────────────────────────────────────────────────────────────
Total modal = Rp 10.000/periode

Outcome (BK dan GJ independen, masing-masing 50% win rate):
  Keduanya menang (25%)  → +Rp 10.000
  Satu menang    (50%)   →     Rp   0  (impas)
  Keduanya kalah (25%)   → -Rp 10.000
```

---

## Fitur Utama

- **Prediksi LLM** — analisis 200 hasil terakhir via OpenRouter (Gemini 2.0 Flash / Claude sebagai fallback)
- **Confidence-driven Single Bet** — default memilih satu dimensi dengan confidence LLM tertinggi
- **3 Posisi Target** — satu instance bot bisa difokuskan ke `depan`, `tengah`, atau `belakang`
- **Martingale Terpisah** — level BK dan GJ dikelola **sendiri-sendiri**, menang salah satu tidak reset yang lain
- **5 Level Martingale** — Rp100 → Rp200 → Rp400 → Rp800 → Rp1.600/angka
- **Daily Loss Limit** — bot pause otomatis jika rugi harian melebihi batas, resume besok
- **Notifikasi Telegram** — setiap bet, hasil menang/kalah, dan summary harian 23:55 WIB
- **Multi-Instance Ready** — bisa jalankan banyak bot di 1 VPS dengan akun, DB, dan log terpisah
- **Shared Telegram** — semua instance bisa kirim laporan ke 1 bot Telegram yang sama, dengan label instance
- **Telegram Commands** — kontrol bot via Telegram: cek status, balance, riwayat, profit, pause/resume
- **Cloudflare Bypass** — httpx dengan browser headers; Playwright headless Chromium sebagai fallback
- **Dry-run Mode** — simulasi penuh tanpa bet sungguhan
- **Persistent State** — semua state (martingale level, last period, daily loss) tersimpan di DB, aman restart
- **Domain Fleksibel** — ganti `SITE_URL` di `.env` jika domain berubah, tanpa ubah kode

---

## Struktur Proyek

```
CryDevil/
├── main.py                  # Entry point + APScheduler hourly cycle
├── config.py                # Semua setting dibaca dari .env
├── .env.example             # Template konfigurasi lengkap (salin ke .env)
├── requirements.txt         # Dependensi Python
├── setup.sh                 # Installer otomatis Ubuntu VPS
├── hokidraw-bot.service     # Systemd unit file
├── modules/
│   ├── auth.py              # Login, CSRF token, validasi sesi, Playwright CF-bypass
│   ├── scraper.py           # Ambil history draw, periode aktif, timer pasaran
│   ├── categories.py        # Klasifikasi BE/KE/GE/GA + generator 50 angka per kategori
│   ├── predictor.py         # Kirim history ke LLM → prediksi BK + GJ
│   ├── bettor.py            # POST /games/4d/send dengan 50 angka, cek menang
│   ├── money_manager.py     # Soft martingale terpisah BK/GJ + daily loss limit
│   ├── notifier.py          # Notifikasi Telegram (outgoing)
│   ├── telegram_commands.py # Telegram command handler (incoming): /status, /balance, dll
│   └── database.py          # SQLite: results, bets, daily_stats, bot_state
├── data/                    # SQLite DB — dibuat otomatis saat runtime
└── logs/                    # Log file — dibuat otomatis saat runtime
```

---

## Install di VPS Ubuntu

### Prasyarat

- Ubuntu 22.04+ dengan Python 3.11
- Akun aktif di situs togel (domain diisi di `SITE_URL`)
- API key OpenRouter → [openrouter.ai/keys](https://openrouter.ai/keys)
- (Opsional) Telegram Bot Token dari [@BotFather](https://t.me/BotFather)

### Install Otomatis

```bash
# 1. Clone repo
git clone https://github.com/anunnaki13/CryDevil.git
cd CryDevil

# 2. Jalankan setup
bash setup.sh
```

`setup.sh` akan otomatis:
- Install Python 3.11 + semua library
- Buat Python virtual environment
- Install Playwright Chromium (untuk bypass Cloudflare)
- Buat file `.env` dari template
- Daftarkan systemd service

---

## Konfigurasi `.env`

**Hanya edit file ini — tidak perlu ubah kode Python apapun.**

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
| `BET_TARGET` | `belakang` | Posisi target 2D: `depan`, `tengah`, atau `belakang` |
| `BASE_BET` | `100` | Nominal per **angka** (IDR). `bet_param = BASE_BET ÷ 1.000` yang dikirim ke API |
| `BET_MODE` | `single` | `single` = pilih 1 dimensi dengan confidence tertinggi · `double` = 2 bet/periode |
| `MIN_CONFIDENCE_TO_BET` | `0.60` | Jika confidence tertinggi BK/GJ di bawah threshold ini, bot skip |

> **Tipe bet selalu Bet Full (B) — payout ×100.** Tidak bisa diubah.

### Contoh Nilai `BASE_BET`

| `BASE_BET` | `bet_param` ke API | Total per bet (50 angka) | Total per periode (×2) |
|---|---|---|---|
| `100` | `0.1` | Rp 5.000 | Rp 10.000 |
| `200` | `0.2` | Rp 10.000 | Rp 20.000 |
| `500` | `0.5` | Rp 25.000 | Rp 50.000 |
| `1000` | `1` | Rp 50.000 | Rp 100.000 |

### Martingale

> BK dan GJ punya level **terpisah** — menang salah satu tidak mereset yang lain.

| Setting | Default | Keterangan |
|---|---|---|
| `MARTINGALE_LEVELS` | `100,200,400,800,1600` | Nominal per angka tiap level (IDR), pisah koma |
| `MARTINGALE_STEP_LOSSES` | `3` | Naik 1 level setiap N kekalahan berturut-turut per dimensi |
| `DAILY_LOSS_LIMIT` | `200000` | Batas rugi harian (Rp). Bot pause hingga tengah malam WIB |

**Tabel martingale default:**

| Level | Per angka | `bet_param` | Per bet (50 angka) | Syarat | Probabilitas |
|---|---|---|---|---|---|
| 0 | Rp 100 | `0.1` | Rp 5.000 | awal | — |
| 1 | Rp 200 | `0.2` | Rp 10.000 | 3 loss berturut | 12.5% |
| 2 | Rp 400 | `0.4` | Rp 20.000 | 6 loss berturut | 1.56% |
| 3 | Rp 800 | `0.8` | Rp 40.000 | 9 loss berturut | 0.20% |
| 4 | Rp 1.600 | `1.6` | Rp 80.000 | 12 loss berturut | 0.024% |

### Telegram (Opsional)

| Setting | Keterangan |
|---|---|
| `TELEGRAM_BOT_TOKEN` | Token dari @BotFather di Telegram |
| `TELEGRAM_CHAT_ID` | ID chat tujuan notifikasi |
| `TELEGRAM_MESSAGE_THREAD_ID` | Opsional, jika ingin kirim ke topic/thread tertentu |
| `TELEGRAM_COMMANDS_ENABLED` | `true/false`. Aktifkan command polling hanya di 1 instance |

> **Cara dapat CHAT_ID:** Kirim pesan ke bot Anda → buka
> `https://api.telegram.org/bot<TOKEN>/getUpdates` → cari `"chat":{"id": ...}`

### Telegram Commands

Bot mendukung perintah interaktif via Telegram. Ketik `/` di chat untuk melihat menu.

| Command | Fungsi |
|---|---|
| `/status` | Status bot: mode, balance, level martingale, daily loss |
| `/balance` | Cek saldo akun saat ini |
| `/history` | 10 bet terakhir (pilihan, amount, menang/kalah) |
| `/results` | 10 hasil draw terakhir dari database |
| `/stats` | Statistik hari ini: total bet, win rate, profit |
| `/profit` | Profit hari ini & keseluruhan + balance |
| `/level` | Detail level martingale BK & GJ |
| `/pause` | Pause bot — skip siklus sampai di-resume |
| `/resume` | Resume bot setelah pause |
| `/help` | Daftar semua perintah |

### LLM

| Setting | Default | Keterangan |
|---|---|---|
| `LLM_MODEL` | `google/gemini-2.0-flash-001` | Model utama (daftar model di openrouter.ai) |
| `LLM_FALLBACK_MODEL` | `anthropic/claude-sonnet-4-20250514` | Model cadangan jika utama gagal |
| `LLM_TEMPERATURE` | `0.3` | 0.0 = deterministik · 1.0 = paling acak |

---

## Menjalankan Bot

```bash
cd /opt/hokidraw-bot
source venv/bin/activate

# Cek konfigurasi saja (tanpa jalankan bot)
python main.py --check-config

# Test dry-run — simulasi penuh tanpa bet sungguhan
python main.py --dry-run

# Jalankan live
python main.py
```

## Menjalankan 3 Bot di 1 VPS Dengan 1 Analisa LLM
Panduan operasional yang lebih rapi ada di [DEPLOYMENT.md](DEPLOYMENT.md). Ringkasnya, satu codebase dipakai bersama dan setiap bot dibedakan oleh `.env`, akun website, DB, log, dan state directory masing-masing.

Dalam mode yang direkomendasikan:
- bot menilai dua opsi per posisi: `Besar/Kecil` dan `Genap/Ganjil`
- bot hanya memilih satu opsi dengan confidence tertinggi
- jika confidence tertinggi masih di bawah `MIN_CONFIDENCE_TO_BET`, bot `SKIP`

Rancangan final 3 bot:

| Instance | Akun Website | Posisi |
|---|---|---|
| `bot-1` | akun A | `depan` |
| `bot-2` | akun B | `tengah` |
| `bot-3` | akun C | `belakang` |

Peran fleet:

| Instance | Role | Tugas |
|---|---|---|
| `bot-1` | `leader` | Memanggil LLM sekali per periode, menulis rencana fleet |
| `bot-2` | `worker` | Membaca rencana leader lalu eksekusi pada akun B |
| `bot-3` | `worker` | Membaca rencana leader lalu eksekusi pada akun C |

Contoh isi penting per instance:

```bash
INSTANCE_NAME=bot-1
INSTANCE_LABEL=Bot 1
BET_TARGET=depan
PARTAI_USERNAME=akun_a
PARTAI_PASSWORD=xxxx
STATE_DIR=/opt/hokidraw-bot/instances/bot-1
DB_PATH=/opt/hokidraw-bot/instances/bot-1/data/hokidraw.db
LOG_PATH=/opt/hokidraw-bot/instances/bot-1/logs/bot.log
FLEET_SHARED_ANALYSIS=true
FLEET_ROLE=leader
FLEET_SHARED_DIR=/opt/hokidraw-bot/shared
FLEET_BOT_NAMES=bot-1,bot-2,bot-3
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
TELEGRAM_COMMANDS_ENABLED=true
MIN_CONFIDENCE_TO_BET=0.60
```

Jika semua bot memakai 1 bot Telegram yang sama:
- gunakan `TELEGRAM_BOT_TOKEN` dan `TELEGRAM_CHAT_ID` yang sama di semua instance
- biarkan `INSTANCE_LABEL` berbeda agar laporan mudah dibedakan
- aktifkan `TELEGRAM_COMMANDS_ENABLED=true` hanya pada leader
- sisanya tetap `false` agar tidak bentrok polling Telegram
- leader akan memanggil LLM sekali, lalu worker membaca plan bersama dari `FLEET_SHARED_DIR`
- Anda bisa mematikan bot tertentu dari Telegram dengan `/bot_off bot-2` lalu menyalakannya lagi dengan `/bot_on bot-2`

### Sebagai Systemd Service (Background, Auto-restart)

```bash
# Aktifkan dan jalankan
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
cd /opt/hokidraw-bot && git pull origin main
sudo systemctl restart hokidraw-bot
```

### Multi-Instance Dengan Systemd Template

```bash
sudo mkdir -p /opt/hokidraw-bot/instances/bot-1
cp /opt/hokidraw-bot/instances/bot-template.env /opt/hokidraw-bot/instances/bot-1/.env
nano /opt/hokidraw-bot/instances/bot-1/.env

sudo systemctl enable --now hokidraw-bot@bot-1
sudo systemctl status hokidraw-bot@bot-1
journalctl -u hokidraw-bot@bot-1 -f
```

Contoh 3 bot:

```bash
sudo mkdir -p /opt/hokidraw-bot/instances/bot-1 /opt/hokidraw-bot/instances/bot-2 /opt/hokidraw-bot/instances/bot-3 /opt/hokidraw-bot/shared
cp /opt/hokidraw-bot/instances/bot-template.env /opt/hokidraw-bot/instances/bot-1/.env
cp /opt/hokidraw-bot/instances/bot-template.env /opt/hokidraw-bot/instances/bot-2/.env
cp /opt/hokidraw-bot/instances/bot-template.env /opt/hokidraw-bot/instances/bot-3/.env

# edit:
# bot-1 => target=depan, role=leader, commands=true
# bot-2 => target=tengah, role=worker, commands=false
# bot-3 => target=belakang, role=worker, commands=false

sudo systemctl enable --now hokidraw-bot@bot-1 hokidraw-bot@bot-2 hokidraw-bot@bot-3
```

---

## Alur Bot per Jam

```
Menit :05 setiap jam (WIB)
│
├─ [1] Login check — re-login otomatis jika sesi expired
│
├─ [2] Poll hasil draw baru (maks 10x, interval 2 menit)
│       └─ Parse 4D → ekstrak posisi target instance → klasifikasi BE/KE + GE/GA → simpan DB
│
├─ [3] Settle pending bets dari periode sebelumnya
│       ├─ Cek bet BK: pilihan cocok dengan belakang_bk? → won / lost
│       ├─ Cek bet GJ: pilihan cocok dengan belakang_gj? → won / lost
│       ├─ Update martingale BK dan GJ secara independen
│       └─ Kirim notifikasi hasil ke Telegram
│
├─ [4] Cek daily loss limit → pause jika tercapai
│
├─ [5] Ambil 200 history draw
│       └─ Solo: analisa target instance sendiri | Fleet: leader analisa 4D sekali untuk semua bot
│
├─ [6] Kirim ke LLM via OpenRouter
│       └─ Analisis: frekuensi, streak, trend 10 & 20 periode terakhir + confidence + keputusan BET/SKIP jika fleet aktif
│
├─ [7] LLM return prediksi:
│       {besar_kecil: {choice:"BE", confidence:0.65, reason:"..."},
│        genap_ganjil: {choice:"GA", confidence:0.72, reason:"..."}}
│
├─ [8] Ambil bet amount per dimensi (sesuai martingale level masing-masing)
│
├─ [9] Pilih satu opsi dengan confidence tertinggi:
│       ├─ jika max(BK, GJ) < threshold → SKIP
│       └─ jika lolos threshold → POST /games/4d/send untuk satu kategori terpilih
│
└─ [10] Notifikasi Telegram: periode, pilihan, confidence, total taruhan

Pukul 23:55 WIB
  └─ Kirim daily summary (total bet, win rate, profit/loss, saldo akhir)
     + Reset daily_loss counter
```

---

## Database SQLite

Semua data disimpan di DB SQLite per instance:

| Tabel | Isi |
|---|---|
| `results` | Setiap draw: 4D lengkap, `target_position`, `target_number_2d`, `target_bk`, `target_gj` |
| `bets` | **1 row per dimensi** — kolom: `bet_dimension`, `bet_choice`, `bet_amount_per_angka`, `confidence`, `status`, `win_amount`, `result_match` |
| `daily_stats` | Per tanggal: total bet, total win, profit/loss, saldo akhir |
| `bot_state` | State persisten: `consecutive_losses_bk`, `consecutive_losses_gj`, `martingale_level_bk`, `martingale_level_gj`, `daily_loss`, `last_period`, `bot_paused` |

---

## Contoh Notifikasi Telegram

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

```
📊 HASIL Periode 11039: 3246 (2D=46)
→ ❌ Besar/Kecil: KECIL (bet: BESAR)
→ ✅ Genap/Ganjil: GENAP (bet: GENAP)
Profit: Rp0 | Saldo: Rp60.000
```

**Daily summary (23:55 WIB):**
```
📈 Ringkasan Hari Ini — 2026-04-08
Periode: 48 bet | Win: 26/48 (54.2%)
Total Bet: Rp480.000 | Total Win: Rp520.000
Profit: +Rp40.000 | Saldo: Rp590.000
```

---

## Jika Domain Website Berubah

Cukup update **satu baris** di `.env` lalu restart:

```bash
# Edit .env
SITE_URL=https://domain-baru.com

# Restart bot
sudo systemctl restart hokidraw-bot
```

---

## Catatan Penting

> **Bot selalu menggunakan Bet Full (payout ×100)** via menu Quick 2D pada posisi target instance.

> **Win rate ~50% per dimensi** — jauh lebih baik dari tebak angka spesifik (1%). Martingale tetap ada risiko pada losing streak panjang.

> **Mulai dengan `BASE_BET=100`** (Rp 10.000/periode) minimal 24 jam untuk memverifikasi bot berjalan benar sebelum naikkan nominal.

> **Set `DAILY_LOSS_LIMIT`** sesuai modal harian Anda. Bot berhenti otomatis jika tercapai dan resume keesokan harinya.

> **Prediksi LLM bersifat statistik** — menganalisis distribusi dan streak, bukan jaminan profit.

---

## Dokumentasi Teknis

- [`HOKIDRAW_API_DOCUMENTATION.md`](HOKIDRAW_API_DOCUMENTATION.md) — API endpoints hasil reverse engineering HAR file
- [`HOKIDRAW_BOT_BLUEPRINT.md`](HOKIDRAW_BOT_BLUEPRINT.md) — Blueprint dan spesifikasi lengkap bot
