# Deploy Guide

Panduan deploy untuk arsitektur CryDevil saat ini: satu bot tunggal yang menganalisis 3 posisi sekaligus dan hanya memasang 1 bet terbaik per periode.

## Layout yang Disarankan

```text
/opt/hokidraw-bot/
├── venv/
├── main.py
├── config.py
├── modules/
├── data/hokidraw.db
├── logs/bot.log
├── .env
└── hokidraw-bot.service
```

## Prinsip

- satu codebase
- satu database
- satu log utama
- satu instance service
- analisis mencakup `depan`, `tengah`, dan `belakang`
- eksekusi hanya 1 bet terbaik per periode

## Langkah Ringkas

```bash
git clone https://github.com/anunnaki13/CryDevil.git
cd CryDevil
bash setup.sh
cp .env.example .env
nano .env
source venv/bin/activate
python main.py --check-config
python main.py --dry-run
sudo systemctl enable --now hokidraw-bot
```

## Reset DB Lama

Versi ini memakai schema baru. Jika Anda sebelumnya menjalankan versi fleet atau schema lama, jangan pakai DB lama untuk boot pertama.

Checklist aman:
1. stop service
2. backup DB lama jika ingin menyimpan arsip
3. hapus atau ganti nama `data/hokidraw.db`
4. jalankan `python main.py --check-config`
5. jalankan `python main.py --dry-run`
6. jika output normal, start service lagi

Contoh:

```bash
sudo systemctl stop hokidraw-bot
mv /opt/hokidraw-bot/data/hokidraw.db /opt/hokidraw-bot/data/hokidraw.db.bak.$(date +%F-%H%M%S)
source venv/bin/activate
python main.py --check-config
python main.py --dry-run
sudo systemctl start hokidraw-bot
```

## Variabel Penting

- `SITE_URL`
- `POOL_ID`
- `PARTAI_USERNAME`
- `PARTAI_PASSWORD`
- `OPENROUTER_API_KEY`
- `BASE_BET`
- `MIN_CONFIDENCE_TO_BET`
- `MARTINGALE_LEVELS`
- `DAILY_LOSS_LIMIT`
- `BACKLOG_RECOVERY_LIMIT`
- `HISTORY_FETCH_MAX_PAGES`
- `PREDICTION_EVAL_WINDOW`

## Operasional

- jika sesi situs expire, bot akan re-login otomatis
- jika confidence terbaik di bawah threshold, bot skip
- jika rugi harian menyentuh limit, bot pause sampai tengah malam WIB
- performa historis prediksi disimpan untuk umpan balik ke analisis berikutnya
- recovery result tertinggal memakai history web dan bisa disetel lewat `BACKLOG_RECOVERY_LIMIT`

## Monitoring

```bash
sudo systemctl status hokidraw-bot
journalctl -u hokidraw-bot -f
tail -f /opt/hokidraw-bot/logs/bot.log
```
