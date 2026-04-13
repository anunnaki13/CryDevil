# HOKIDRAW BOT BLUEPRINT — SINGLE BOT MULTI-POSITION

## Overview

CryDevil sekarang berjalan sebagai satu bot tunggal. Bot ini menganalisis tiga posisi 2D sekaligus:
- depan
- tengah
- belakang

Untuk masing-masing posisi, bot menilai dua dimensi independen:
- besar/kecil
- genap/ganjil

Artinya ada 6 kandidat prediksi per periode:
- depan_bk
- depan_gj
- tengah_bk
- tengah_gj
- belakang_bk
- belakang_gj

Bot hanya memasang 1 bet per periode, yaitu kandidat dengan confidence tertinggi global.

## Sumber Data

- History hasil utama: `GET /history/detail/data/p76368-1`
- Fallback history: `GET /games/4d/history/quick_2d/p76368`
- Current period: load/game page yang memuat field `periode`
- Balance: `POST /request-balance`
- Submit bet: `POST /games/4d/send`

## Analisis

Setiap periode:
1. Bot ambil history 4D terbaru.
2. Bot pecah setiap result ke 2D depan, tengah, belakang.
3. Untuk tiap posisi, bot hitung sinyal BK dan GJ.
4. LLM memberi prediksi dan confidence untuk 6 kandidat.
5. Heuristic lokal ikut dihitung.
6. Confidence akhir di-ensemble antara LLM dan heuristic.
7. Feedback historis akurasi slot dipakai untuk menurunkan confidence slot yang terbukti lemah atau overconfident.
8. Semua kandidat diranking.
9. Bot hanya mengeksekusi kandidat peringkat 1 jika confidence >= threshold.

## Martingale

Martingale dipisah penuh per slot:
- depan_bk
- depan_gj
- tengah_bk
- tengah_gj
- belakang_bk
- belakang_gj

Loss pada satu slot tidak memengaruhi slot lain.

## Database

Tabel utama:
- `results`: simpan hasil 4D lengkap plus seluruh turunan 2D depan/tengah/belakang
- `bets`: simpan satu bet aktual yang benar-benar dipasang
- `prediction_runs`: simpan semua prediksi 6 slot per periode, termasuk confidence dan evaluasinya
- `daily_stats`: ringkasan profit/loss harian
- `bot_state`: state martingale, daily loss, pause, last_period

## Feedback Loop

Saat result keluar:
1. Bot settle bet yang benar-benar dipasang.
2. Bot juga settle semua `prediction_runs` pada periode itu.
3. Tiap slot ditandai benar/salah.
4. Ringkasan akurasi historis dipakai lagi pada prompt dan penyesuaian confidence periode berikutnya.

## Telegram

Command utama:
- `/status`
- `/balance`
- `/history`
- `/results`
- `/stats`
- `/profit`
- `/level`
- `/signal`
- `/predict`
- `/betnow`
- `/pause`
- `/resume`

## Catatan Operasional

- Arsitektur fleet 3 bot sudah dipensiunkan.
- DB lama tidak dianggap kompatibel otomatis dengan schema baru.
- Recovery result tertinggal dilakukan dari history web dengan membandingkan period yang belum ada di DB.
