# Deploy Guide

Panduan ini menjelaskan layout VPS yang rapi untuk menjalankan `CryDevil` dengan 3 bot utama, plus aplikasi bot lain di server yang sama tanpa bentrok.

## Prinsip Isolasi

Yang boleh dibagi:
- source code `CryDevil`
- virtualenv `CryDevil`
- Telegram bot token yang sama
- OpenRouter API key yang sama

Yang harus unik per instance:
- akun website togel
- `INSTANCE_NAME`
- `INSTANCE_LABEL`
- `BET_TARGET`
- `STATE_DIR`
- `DB_PATH`
- `LOG_PATH`

Mode taruhan yang direkomendasikan:
- `BET_MODE=single`
- `MIN_CONFIDENCE_TO_BET=0.60`

Artinya:
- setiap bot mengevaluasi 2 opsi pada posisi targetnya: `Besar/Kecil` dan `Genap/Ganjil`
- bot hanya memilih 1 opsi dengan confidence tertinggi
- jika confidence tertinggi di bawah 60%, bot `SKIP`

Yang hanya boleh aktif sekali per Telegram token:
- `TELEGRAM_COMMANDS_ENABLED=true`

## Layout VPS Yang Disarankan

```text
/opt/
├── crydevil/
│   ├── venv/
│   ├── main.py
│   ├── config.py
│   ├── modules/
│   ├── shared/
│   │   ├── fleet_plan.json
│   │   └── fleet_state.json
│   ├── instances/
│   │   ├── bot-1/
│   │   │   ├── .env
│   │   │   ├── data/hokidraw.db
│   │   │   └── logs/bot.log
│   │   ├── bot-2/
│   │   │   ├── .env
│   │   │   ├── data/hokidraw.db
│   │   │   └── logs/bot.log
│   │   └── bot-3/
│   │       ├── .env
│   │       ├── data/hokidraw.db
│   │       └── logs/bot.log
│   └── instances/bot-template.env
├── another-bot/
└── monitoring/
```

Intinya:
- satu codebase `CryDevil`
- tiga instance folder untuk tiga akun
- satu folder `shared/` untuk koordinasi leader-worker
- aplikasi lain di VPS ditempatkan di folder terpisah

## Topologi 3 Bot

```text
bot-1  -> akun A -> target depan    -> role leader
bot-2  -> akun B -> target tengah   -> role worker
bot-3  -> akun C -> target belakang -> role worker
```

Alasan:
- 1 kali analisa LLM per periode
- 3 akun berbeda tetap berjalan terpisah
- 1 bot Telegram cukup untuk laporan semua instance

## Aturan Telegram

Semua instance boleh memakai:
- `TELEGRAM_BOT_TOKEN` yang sama
- `TELEGRAM_CHAT_ID` yang sama

Tetapi:
- hanya `bot-1` yang disarankan memakai `TELEGRAM_COMMANDS_ENABLED=true`
- `bot-2` dan `bot-3` harus `false`

Tujuannya:
- polling command Telegram tidak bentrok
- `/bot_on` dan `/bot_off` dikendalikan dari satu controller

## Template Env Yang Rapi

### `bot-1`

```env
INSTANCE_NAME=bot-1
INSTANCE_LABEL=Depan-A
BET_TARGET=depan

PARTAI_USERNAME=akun_a
PARTAI_PASSWORD=xxxx

STATE_DIR=/opt/crydevil/instances/bot-1
DB_PATH=/opt/crydevil/instances/bot-1/data/hokidraw.db
LOG_PATH=/opt/crydevil/instances/bot-1/logs/bot.log

FLEET_SHARED_ANALYSIS=true
FLEET_ROLE=leader
FLEET_SHARED_DIR=/opt/crydevil/shared
FLEET_BOT_NAMES=bot-1,bot-2,bot-3

TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
TELEGRAM_COMMANDS_ENABLED=true
BET_MODE=single
MIN_CONFIDENCE_TO_BET=0.60
```

### `bot-2`

```env
INSTANCE_NAME=bot-2
INSTANCE_LABEL=Tengah-B
BET_TARGET=tengah

PARTAI_USERNAME=akun_b
PARTAI_PASSWORD=xxxx

STATE_DIR=/opt/crydevil/instances/bot-2
DB_PATH=/opt/crydevil/instances/bot-2/data/hokidraw.db
LOG_PATH=/opt/crydevil/instances/bot-2/logs/bot.log

FLEET_SHARED_ANALYSIS=true
FLEET_ROLE=worker
FLEET_SHARED_DIR=/opt/crydevil/shared
FLEET_BOT_NAMES=bot-1,bot-2,bot-3

TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
TELEGRAM_COMMANDS_ENABLED=false
BET_MODE=single
MIN_CONFIDENCE_TO_BET=0.60
```

### `bot-3`

```env
INSTANCE_NAME=bot-3
INSTANCE_LABEL=Belakang-C
BET_TARGET=belakang

PARTAI_USERNAME=akun_c
PARTAI_PASSWORD=xxxx

STATE_DIR=/opt/crydevil/instances/bot-3
DB_PATH=/opt/crydevil/instances/bot-3/data/hokidraw.db
LOG_PATH=/opt/crydevil/instances/bot-3/logs/bot.log

FLEET_SHARED_ANALYSIS=true
FLEET_ROLE=worker
FLEET_SHARED_DIR=/opt/crydevil/shared
FLEET_BOT_NAMES=bot-1,bot-2,bot-3

TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
TELEGRAM_COMMANDS_ENABLED=false
BET_MODE=single
MIN_CONFIDENCE_TO_BET=0.60
```

## Menjalankan Dengan Systemd

```bash
sudo systemctl enable --now hokidraw-bot@bot-1
sudo systemctl enable --now hokidraw-bot@bot-2
sudo systemctl enable --now hokidraw-bot@bot-3
```

Cek status:

```bash
sudo systemctl status hokidraw-bot@bot-1
sudo systemctl status hokidraw-bot@bot-2
sudo systemctl status hokidraw-bot@bot-3
```

Lihat log:

```bash
journalctl -u hokidraw-bot@bot-1 -f
journalctl -u hokidraw-bot@bot-2 -f
journalctl -u hokidraw-bot@bot-3 -f
```

## Menambah Bot Lain Di VPS Yang Sama

Aman, selama bot lain:
- punya folder aplikasi sendiri
- tidak memakai DB/log `CryDevil`
- tidak ikut polling Telegram dengan token yang sama kecuali memang dirancang demikian

Contoh layout yang aman:

```text
/opt/crydevil
/opt/another-bot
/opt/price-monitor
```

Jangan campur:
- database antar aplikasi
- file log antar aplikasi
- file `.env` antar aplikasi

## Risiko Operasional Yang Perlu Diperhatikan

- Playwright bisa berat jika banyak instance sering fallback ke browser.
- Banyak login paralel ke situs target bisa memicu rate limit atau challenge.
- Worker yang dimatikan dari Telegram akan `SKIP`, tetapi akun lain tetap jalan normal.
- State fleet disimpan di file JSON bersama, jadi direktori `shared/` harus writable oleh user service.

## Checklist Sebelum Live

- tiap bot punya akun website berbeda
- tiap bot punya `BET_TARGET` yang benar
- tiap bot punya path data/log sendiri
- hanya leader yang aktif command Telegram
- `python main.py --check-config` lolos pada tiap instance
- dry run lulus sebelum `systemctl enable --now`
