# CryDevil

Bot auto-bet Hokidraw berbasis Python yang menganalisis `2D depan`, `2D tengah`, dan `2D belakang` sekaligus, lalu hanya memasang `1 bet terbaik` per periode berdasarkan confidence tertinggi global.

## Cara Kerja Baru

Setiap periode bot mengevaluasi 6 kandidat:
- `depan_bk`
- `depan_gj`
- `tengah_bk`
- `tengah_gj`
- `belakang_bk`
- `belakang_gj`

Untuk tiap kandidat, bot membangun prediksi dari:
- analisis LLM via OpenRouter
- heuristic statistik lokal
- feedback performa historis prediksi yang sudah pernah dibuat

Setelah itu bot:
1. memberi confidence untuk semua kandidat
2. membuat ranking global
3. memilih 1 kandidat dengan confidence tertinggi
4. memasang hanya 1 bet pada posisi dan kategori tersebut

## Contoh

Jika ranking periode aktif adalah:
- `belakang_gj = GA @ 71%`
- `tengah_bk = KE @ 67%`
- `depan_bk = BE @ 61%`

Maka bot hanya akan memasang:
- posisi `belakang`
- dimensi `genap_ganjil`
- choice `GA`

## Martingale

Martingale dipisah penuh per slot:
- `depan_bk`
- `depan_gj`
- `tengah_bk`
- `tengah_gj`
- `belakang_bk`
- `belakang_gj`

Artinya loss pada satu slot tidak memengaruhi nominal slot lain.

## Evaluasi Prediksi

Bot menyimpan semua prediksi yang dihasilkan, bukan hanya yang dipasang. Saat hasil draw keluar, bot menandai tiap prediksi:
- benar atau salah
- confidence saat diprediksi
- slot mana yang dipilih untuk bet

Ringkasan performa historis ini dipakai lagi pada prompt LLM periode berikutnya agar analisis menjadi lebih adaptif.

## Knowledge Base Manual

Bot sekarang mendukung knowledge base manual berbasis historis jangka menengah.

Skemanya:
1. Anda trigger command Telegram `/kbbuild`
2. bot menarik `400` history draw dari web
3. LLM merangkum 400 data itu menjadi knowledge base operasional
4. ringkasan tersebut disimpan ke database
5. setiap analisis berikutnya, predictor membaca knowledge base aktif itu sebagai konteks tambahan

Penting:
- proses ini `manual`, bukan otomatis
- bot tidak akan selalu mengirim 400 raw history pada setiap prediksi
- yang dipakai terus-menerus adalah hasil ringkasannya, agar prompt tetap efisien dan konsisten

## File Penting

- [main.py](/root/new/barbatos/main.py:1)
- [config.py](/root/new/barbatos/config.py:1)
- [modules/predictor.py](/root/new/barbatos/modules/predictor.py:1)
- [modules/money_manager.py](/root/new/barbatos/modules/money_manager.py:1)
- [modules/database.py](/root/new/barbatos/modules/database.py:1)
- [modules/telegram_commands.py](/root/new/barbatos/modules/telegram_commands.py:1)

## Menjalankan

```bash
cp .env.example .env
nano .env
python main.py --check-config
python main.py --dry-run
python main.py
```

## Konfigurasi Inti

- `BASE_BET`
- `MIN_CONFIDENCE_TO_BET`
- `MARTINGALE_LEVELS`
- `MARTINGALE_STEP_LOSSES`
- `DAILY_LOSS_LIMIT`
- `BACKLOG_RECOVERY_LIMIT`
- `HISTORY_FETCH_MAX_PAGES`
- `PREDICTION_EVAL_WINDOW`
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`

## Reset DB

Jika Anda datang dari versi lama atau fleet 3 bot, gunakan DB baru. Schema saat ini berbeda dan tidak didesain auto-migrate dari DB lama.

Urutan aman:
1. stop service
2. backup DB lama
3. hapus atau rename `data/hokidraw.db`
4. jalankan `python main.py --check-config`
5. jalankan `python main.py --dry-run`
6. start service lagi

## Telegram

Command yang tersedia sekarang:
- `/status`
- `/balance`
- `/history`
- `/results`
- `/stats`
- `/profit`
- `/level`
- `/signal`
- `/predict`
- `/kb`
- `/kbbuild`
- `/betnow`
- `/pause`
- `/resume`

## Catatan

- Bot ini sekarang didesain untuk `single instance`, bukan fleet 3 bot.
- File `hokidraw-bot.service` adalah jalur utama yang direkomendasikan.
- `hokidraw-bot@.service` masih ada sebagai template systemd, tetapi arsitektur aplikasi utamanya sekarang bukan mode leader/worker.
