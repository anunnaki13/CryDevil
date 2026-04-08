"""
Hokidraw 2D Auto-Betting Bot
=============================
Otomatis prediksi dan pasang taruhan BESAR/KECIL + GENAP/GANJIL
pada 2D Belakang pasaran Hokidraw (partai34848.com).

Cara kerja per jam:
  1. Tunggu hasil draw periode sebelumnya
  2. Klasifikasi hasil → catat menang/kalah → update martingale BK & GJ
  3. Ambil history 200 periode → kirim ke LLM via OpenRouter
  4. LLM prediksi BE/KE dan GE/GA + confidence
  5. Pasang 2 bet (BK + GJ), masing-masing 50 angka
  6. Telegram notifikasi

Usage:
    python main.py                # live
    python main.py --dry-run      # test tanpa bet sungguhan
    python main.py --check-config # cek .env saja
"""

import asyncio
import argparse
import logging
import sys
import os
from datetime import datetime, timezone, timedelta
from typing import Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from config import (
    POLL_START_MINUTE, POLL_INTERVAL_SECONDS, MAX_POLL_ATTEMPTS,
    BET_DEADLINE_MINUTE, DAILY_LOSS_LIMIT,
    LOG_PATH, BET_MODE,
    validate_config,
)
from modules import database as db
from modules.auth import AuthManager
from modules.scraper import Scraper
from modules.predictor import Predictor
from modules.bettor import Bettor
from modules.money_manager import MoneyManager
from modules.notifier import TelegramNotifier
from modules.categories import (
    classify_result, extract_belakang, parse_result_full, result_summary,
)

# ─── Logging ─────────────────────────────────────────────────────────────────

os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOG_PATH, encoding="utf-8"),
    ],
)
logger = logging.getLogger("hokidraw.main")

_WIB = timezone(timedelta(hours=7))


def _now_wib() -> datetime:
    return datetime.now(_WIB)


def _today_wib() -> str:
    return _now_wib().strftime("%Y-%m-%d")


# ─── Bot ─────────────────────────────────────────────────────────────────────

class HokidrawBot:
    def __init__(self, dry_run: bool = False) -> None:
        self.dry_run       = dry_run
        self.auth          = AuthManager()
        self.scraper       = Scraper(self.auth)
        self.predictor     = Predictor()
        self.bettor        = Bettor(self.auth)
        self.mm            = MoneyManager()
        self.notifier      = TelegramNotifier()
        self._last_period: Optional[str] = None

    # ─── Siklus utama ─────────────────────────────────────────────────────────

    async def hourly_cycle(self) -> None:
        now = _now_wib()
        logger.info("=== Siklus mulai @ %s ===", now.strftime("%H:%M WIB"))

        # 1. Pastikan login aktif
        if not await self.auth.ensure_logged_in():
            await self.notifier.notify_alert("Login gagal — skip siklus ini")
            return

        # 2. Poll hasil draw baru
        result = None
        for attempt in range(1, MAX_POLL_ATTEMPTS + 1):
            result = await self._detect_new_result()
            if result:
                break
            if attempt < MAX_POLL_ATTEMPTS:
                logger.debug("Belum ada result baru (%d/%d), tunggu %ds",
                             attempt, MAX_POLL_ATTEMPTS, POLL_INTERVAL_SECONDS)
                await asyncio.sleep(POLL_INTERVAL_SECONDS)

        # 3. Proses hasil draw + settle pending bets
        if result:
            await self._process_result(result)
        else:
            logger.warning("Tidak ada result baru setelah %s polling", MAX_POLL_ATTEMPTS)

        # 4. Cek daily limit
        if not await self.mm.check_and_enforce_daily_limit():
            await self.notifier.send_limit_reached(
                await self.mm.get_daily_loss(), DAILY_LOSS_LIMIT
            )
            return

        # 5. Cek window waktu betting
        if now.minute > BET_DEADLINE_MINUTE:
            logger.info("Lewat deadline menit :%02d — skip bet", BET_DEADLINE_MINUTE)
            return

        # 6. Ambil history dan prediksi LLM
        history = await self.scraper.get_draw_history()
        if not history:
            await self.notifier.notify_alert("Gagal ambil history draw")
            return

        prediction = await self.predictor.analyze(history)
        if prediction is None:
            await self.notifier.notify_alert("LLM prediction gagal")
            return

        bk_data = prediction["besar_kecil"]
        gj_data = prediction["genap_ganjil"]

        logger.info(
            "Prediksi → BK: %s (%.0f%%) | GJ: %s (%.0f%%)",
            bk_data["choice"], bk_data["confidence"] * 100,
            gj_data["choice"], gj_data["confidence"] * 100,
        )

        # 7. Ambil periode saat ini
        periode = await self.scraper.get_current_periode()
        if not periode:
            await self.notifier.notify_alert("Gagal ambil periode saat ini")
            return

        if periode == self._last_period:
            logger.info("Sudah bet di periode %s — skip", periode)
            return

        # 8. Ambil bet amount dari money manager (per dimensi)
        bk_amount = await self.mm.get_bet_amount("besar_kecil")
        gj_amount = await self.mm.get_bet_amount("genap_ganjil")
        bk_level  = await self.mm.get_level("besar_kecil")
        gj_level  = await self.mm.get_level("genap_ganjil")

        # 9. Pasang bet
        if BET_MODE == "double":
            results = await self.bettor.place_double_bet(
                bk_data["choice"], gj_data["choice"],
                bk_amount, gj_amount,
                dry_run=self.dry_run,
            )
            bk_resp, gj_resp = results[0], results[1]
        else:
            # single: pilih yang confidence tertinggi
            if bk_data["confidence"] >= gj_data["confidence"]:
                bk_resp = await self.bettor.place_bet(bk_data["choice"], bk_amount, self.dry_run)
                gj_resp = None
            else:
                gj_resp = await self.bettor.place_bet(gj_data["choice"], gj_amount, self.dry_run)
                bk_resp = None

        # 10. Simpan ke DB
        await self._save_bets(periode, bk_data, gj_data, bk_amount, gj_amount,
                              bk_level, gj_level, bk_resp, gj_resp)

        self._last_period = periode

        # 11. Notifikasi
        actual_bk_amount = bk_amount if bk_resp else 0
        await self.notifier.notify_bet_placed(
            periode=periode,
            bk_choice=bk_data["choice"],
            gj_choice=gj_data["choice"],
            bk_confidence=bk_data["confidence"],
            gj_confidence=gj_data["confidence"],
            bet_amount=bk_amount,  # tampilkan BK amount (biasanya sama)
            bk_level=bk_level,
            gj_level=gj_level,
            dry_run=self.dry_run,
        )

    # ─── Detect new result ────────────────────────────────────────────────────

    async def _detect_new_result(self) -> Optional[dict]:
        """Cek apakah ada hasil draw baru yang belum ada di DB."""
        last = await db.get_last_result()
        last_period = last["period"] if last else None

        latest = await self.scraper.get_latest_result()
        if not latest:
            return None

        # Normalise field names (scraper bisa return "periode" atau "period")
        period = latest.get("period") or latest.get("periode") or ""
        if not period or period == last_period:
            return None

        return latest

    # ─── Process new result ───────────────────────────────────────────────────

    async def _process_result(self, raw_result: dict) -> None:
        """Parse, simpan, settle bets, dan notifikasi hasil draw."""
        period    = raw_result.get("period") or raw_result.get("periode") or ""
        result_4d = str(raw_result.get("result", "")).strip()
        draw_time = str(raw_result.get("draw_time", "")).strip()

        parsed = parse_result_full(result_4d)
        if not parsed:
            logger.error("Tidak bisa parse result: %s", result_4d)
            return

        belakang    = parsed["belakang"]
        actual_bk   = parsed["belakang_bk"]
        actual_gj   = parsed["belakang_gj"]

        logger.info("Result %s: %s → %s", period, result_summary(result_4d),
                    f"2D={belakang} {actual_bk}+{actual_gj}")

        # Simpan ke DB
        await db.save_result(
            period=period,
            draw_time=draw_time,
            full_number=parsed["full"],
            depan=parsed["depan"],
            tengah=parsed["tengah"],
            belakang=belakang,
            belakang_bk=actual_bk,
            belakang_gj=actual_gj,
        )

        # Settle semua pending bet
        pending = await db.get_all_placed_bets()
        for bet in pending:
            bet_choice = bet["bet_choice"]
            dimension  = bet["bet_dimension"]
            amount     = int(bet["bet_amount_per_angka"])
            won        = self.bettor.check_win(bet_choice, belakang)
            payout     = self.bettor.calculate_payout(amount, won)

            status = "won" if won else "lost"
            await db.settle_bet(
                bet_id=bet["id"],
                status=status,
                win_amount=payout["won"],
                result_2d=belakang,
                result_match=actual_bk if dimension == "besar_kecil" else actual_gj,
            )

            if won:
                await self.mm.record_win(dimension, payout["wagered"], payout["won"])
            else:
                await self.mm.record_loss(dimension, payout["wagered"])

            logger.info(
                "Settle %s %s: %s | net=%s",
                dimension, bet_choice, status, payout["net"],
            )

        # Notifikasi hasil (aggregasi BK + GJ untuk periode yang sama)
        if pending:
            await self._notify_result(period, result_4d, belakang,
                                      actual_bk, actual_gj, pending)

    async def _notify_result(
        self,
        periode: str,
        full_result: str,
        result_2d: str,
        actual_bk: str,
        actual_gj: str,
        settled_bets: list[dict],
    ) -> None:
        bet_bk = bet_gj = None
        win_bk = win_gj = False
        profit_bk = profit_gj = 0

        for bet in settled_bets:
            dim    = bet["bet_dimension"]
            amount = int(bet["bet_amount_per_angka"])
            won    = bet["status"] == "won"
            payout = self.bettor.calculate_payout(amount, won)

            if dim == "besar_kecil":
                bet_bk   = bet["bet_choice"]
                win_bk   = won
                profit_bk = payout["net"]
            elif dim == "genap_ganjil":
                bet_gj   = bet["bet_choice"]
                win_gj   = won
                profit_gj = payout["net"]

        balance = await self.auth.get_balance()
        await self.notifier.notify_result(
            periode=periode,
            full_result=full_result,
            result_2d=result_2d,
            actual_bk=actual_bk,
            actual_gj=actual_gj,
            bet_bk=bet_bk,
            bet_gj=bet_gj,
            win_bk=win_bk,
            win_gj=win_gj,
            profit_bk=profit_bk,
            profit_gj=profit_gj,
            balance=balance,
        )

    # ─── Save bets ────────────────────────────────────────────────────────────

    async def _save_bets(
        self,
        periode: str,
        bk_data: dict, gj_data: dict,
        bk_amount: int, gj_amount: int,
        bk_level: int, gj_level: int,
        bk_resp: Optional[dict], gj_resp: Optional[dict],
    ) -> None:
        if bk_resp is not None:
            await db.save_bet(
                period=periode,
                dimension="besar_kecil",
                choice=bk_data["choice"],
                bet_amount_per_angka=bk_amount,
                total_amount=bk_amount * 50,
                martingale_level=bk_level,
                confidence=bk_data["confidence"],
                api_response=str(bk_resp),
            )

        if gj_resp is not None:
            await db.save_bet(
                period=periode,
                dimension="genap_ganjil",
                choice=gj_data["choice"],
                bet_amount_per_angka=gj_amount,
                total_amount=gj_amount * 50,
                martingale_level=gj_level,
                confidence=gj_data["confidence"],
                api_response=str(gj_resp),
            )

    # ─── Daily summary ────────────────────────────────────────────────────────

    async def daily_summary(self) -> None:
        today   = _today_wib()
        stats   = await db.get_daily_stats(today)
        balance = await self.auth.get_balance()

        if balance:
            await db.set_daily_ending_balance(today, balance)

        if stats:
            await self.notifier.notify_daily_summary(
                date=today,
                total_bets=stats["total_bets"],
                total_wins=stats["total_wins"],
                total_bet_amount=int(stats["total_bet_amount"]),
                total_win_amount=int(stats["total_win_amount"]),
                profit=int(stats["profit"]),
                ending_balance=balance,
            )
        else:
            await self.notifier.notify_alert(f"Tidak ada bet hari ini ({today})")

        await self.mm.midnight_reset()

    # ─── Startup / Shutdown ───────────────────────────────────────────────────

    async def startup(self) -> None:
        await db.init_db()
        if not await self.auth.login():
            logger.error("Login awal gagal — cek kredensial di .env")
            sys.exit(1)
        balance = await self.auth.get_balance()
        logger.info("Login OK. Balance: Rp%s", f"{balance:,}" if balance else "?")
        await self.notifier.send_startup(dry_run=self.dry_run)

    async def shutdown(self) -> None:
        await self.notifier.send_shutdown()
        await self.auth.close()


# ─── Scheduler ───────────────────────────────────────────────────────────────

async def run(dry_run: bool) -> None:
    bot = HokidrawBot(dry_run=dry_run)
    await bot.startup()

    scheduler = AsyncIOScheduler(timezone="Asia/Jakarta")

    scheduler.add_job(
        bot.hourly_cycle,
        CronTrigger(minute=POLL_START_MINUTE, timezone="Asia/Jakarta"),
        id="hourly_cycle",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=120,
    )

    scheduler.add_job(
        bot.daily_summary,
        CronTrigger(hour=23, minute=55, timezone="Asia/Jakarta"),
        id="daily_summary",
        max_instances=1,
    )

    scheduler.start()
    logger.info(
        "Scheduler aktif. Siklus setiap jam di menit :%02d. Dry run: %s",
        POLL_START_MINUTE, dry_run,
    )

    try:
        while True:
            await asyncio.sleep(60)
    except (KeyboardInterrupt, SystemExit):
        logger.info("Shutdown...")
    finally:
        scheduler.shutdown(wait=False)
        await bot.shutdown()


# ─── Entry point ─────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Hokidraw 2D Auto-Betting Bot")
    parser.add_argument("--dry-run",      action="store_true",
                        help="Test tanpa bet sungguhan")
    parser.add_argument("--check-config", action="store_true",
                        help="Cek konfigurasi .env saja lalu keluar")
    args = parser.parse_args()

    validate_config(exit_on_error=not args.check_config)

    if args.check_config:
        sys.exit(0)

    if args.dry_run:
        logger.info("*** DRY RUN MODE — tidak ada bet sungguhan ***")

    asyncio.run(run(dry_run=args.dry_run))


if __name__ == "__main__":
    main()
