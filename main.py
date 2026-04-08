"""
Hokidraw 2D Lottery Bot
=======================
Otomatis prediksi dan pasang bet Besar/Kecil + Genap/Ganjil
pada pasar Hokidraw (partai34848.com).

Cara main:
  - Setiap periode, LLM menganalisis history dan memprediksi kategori BK/GJ
    untuk posisi yang dikonfigurasi (depan/tengah/belakang).
  - Bot memasang 50 angka per kategori (2 bet per posisi: BK + GJ).
  - Menang jika hasil draw masuk kategori yang diprediksi (win rate ~50%).

Usage:
    python main.py                # live mode
    python main.py --dry-run      # test tanpa bet sungguhan
    python main.py --check-config # cek .env saja lalu keluar
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
    POLL_INTERVAL_SECONDS, MAX_POLL_ATTEMPTS,
    BET_START_MINUTE, BET_STOP_MINUTE,
    DAILY_LOSS_LIMIT, LOG_PATH,
    BET_POSITIONS,
    validate_config,
)
from modules import database as db
from modules.auth import AuthManager
from modules.scraper import Scraper
from modules.predictor import Predictor
from modules.bettor import Bettor
from modules.money_manager import MoneyManager
from modules.notifier import TelegramNotifier
from modules.categories import parse_result, result_summary

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


# ─── Bot ─────────────────────────────────────────────────────────────────────

class HokidrawBot:
    def __init__(self, dry_run: bool = False) -> None:
        self.dry_run       = dry_run
        self.auth          = AuthManager()
        self.scraper       = Scraper(self.auth)
        self.predictor     = Predictor()
        self.bettor        = Bettor(self.auth)
        self.money_manager = MoneyManager()
        self.notifier      = TelegramNotifier()
        self._last_periode: Optional[str] = None

    # ─── Siklus utama ─────────────────────────────────────────────────────────

    async def run_betting_cycle(self) -> None:
        now = _now_wib()
        logger.info("=== Siklus bet mulai @ %s ===", now.strftime("%H:%M WIB"))

        # 1. Cek limit harian
        if not await self.money_manager.check_and_enforce_daily_limit():
            await self.notifier.send_limit_reached(
                await self.money_manager.get_daily_loss(), DAILY_LOSS_LIMIT
            )
            return

        # 2. Pastikan login aktif
        if not await self.auth.ensure_logged_in():
            await self.notifier.send_alert("Login gagal — skip siklus ini")
            return

        # 3. Tunggu result baru
        new_result = await self._wait_for_new_result()
        if new_result is None:
            logger.warning("Tidak ada result baru setelah %s polling", MAX_POLL_ATTEMPTS)
            return

        # 4. Settle pending bet dengan result baru
        await self._settle_pending_bets(new_result)

        # 5. Cek limit lagi setelah settle
        if not await self.money_manager.check_and_enforce_daily_limit():
            await self.notifier.send_limit_reached(
                await self.money_manager.get_daily_loss(), DAILY_LOSS_LIMIT
            )
            return

        # 6. Cek window waktu betting
        if not (BET_START_MINUTE <= now.minute <= BET_STOP_MINUTE):
            logger.info("Di luar window bet (menit :%02d) — skip", now.minute)
            return

        # 7. Ambil history dan prediksi
        history = await self.scraper.get_draw_history()
        if not history:
            await self.notifier.send_alert("Gagal ambil history draw")
            return

        prediction = await self.predictor.predict(history)
        if prediction is None:
            await self.notifier.send_alert("LLM prediction gagal")
            return

        # 8. Ambil periode saat ini
        periode = await self.scraper.get_current_periode()
        if not periode:
            await self.notifier.send_alert("Gagal ambil periode saat ini")
            return

        if periode == self._last_periode:
            logger.info("Sudah bet di periode %s — skip", periode)
            return

        # 9. Pasang bet per posisi
        bet_per_number  = await self.money_manager.get_bet_amount()
        martingale_level = await self.money_manager.get_martingale_level()
        analysis         = prediction.get("analysis", "")

        for pred in prediction["predictions"]:
            pos = pred["position"]
            if pos not in BET_POSITIONS:
                continue

            await self._place_one_bet(
                periode, pos, pred, bet_per_number, martingale_level, analysis
            )

        self._last_periode = periode

    # ─── Pasang satu bet (satu posisi) ────────────────────────────────────────

    async def _place_one_bet(
        self,
        periode: str,
        position: str,
        pred: dict,
        bet_per_number: int,
        martingale_level: int,
        analysis: str,
    ) -> None:
        bk = pred["besar_kecil"]
        gj = pred["genap_ganjil"]
        bk_conf = pred["bk_confidence"]
        gj_conf = pred["gj_confidence"]

        logger.info(
            "Prediksi %s: BK=%s(%.0f%%) GJ=%s(%.0f%%)",
            position, bk, bk_conf*100, gj, gj_conf*100,
        )

        response = await self.bettor.place_category_bet(
            position, bk, gj, bet_per_number, dry_run=self.dry_run
        )
        if response is None:
            await self.notifier.send_alert(f"Bet gagal — {position} periode {periode}")
            return

        # Cek sukses (dry_run atau API OK)
        bk_resp = response.get("bk", {}) or {}
        gj_resp = response.get("gj", {}) or {}
        bk_ok = self.dry_run or bk_resp.get("status") in (1, "1", True, "true", "ok", "success")
        gj_ok = self.dry_run or gj_resp.get("status") in (1, "1", True, "true", "ok", "success")

        if bk_ok and gj_ok:
            bet_id = await db.save_bet(
                periode=periode,
                numbers=[f"{position}|{bk}", f"{position}|{gj}"],
                bet_amount=bet_per_number,
                martingale_level=martingale_level,
                raw_response=str(response),
            )
            logger.info("Bet tersimpan: id=%s posisi=%s", bet_id, position)

            await self.notifier.send_bet_placed(
                periode=periode,
                position=position,
                bk_category=bk,
                gj_category=gj,
                bk_confidence=bk_conf,
                gj_confidence=gj_conf,
                bet_per_number=bet_per_number,
                martingale_level=martingale_level,
                analysis=analysis,
                dry_run=self.dry_run,
            )
        else:
            await self.notifier.send_alert(
                f"Bet ditolak — {position} periode {periode}\n"
                f"BK: {bk_resp}\nGJ: {gj_resp}"
            )

    # ─── Polling result baru ──────────────────────────────────────────────────

    async def _wait_for_new_result(self) -> Optional[dict]:
        last_known   = await db.get_last_result()
        last_periode = last_known["periode"] if last_known else None

        for attempt in range(1, MAX_POLL_ATTEMPTS + 1):
            latest = await self.scraper.get_latest_result()
            if latest and latest["periode"] != last_periode:
                inserted = await db.save_result(
                    latest["periode"],
                    latest["result"],
                    latest["draw_time"],
                )
                if inserted:
                    parsed = parse_result(latest["result"])
                    summary = result_summary(parsed) if parsed else latest["result"]
                    logger.info("Result baru: %s", summary)
                return latest

            if attempt < MAX_POLL_ATTEMPTS:
                logger.debug("Belum ada result baru (%d/%d), tunggu %ds",
                             attempt, MAX_POLL_ATTEMPTS, POLL_INTERVAL_SECONDS)
                await asyncio.sleep(POLL_INTERVAL_SECONDS)

        return None

    # ─── Settle pending bets ──────────────────────────────────────────────────

    async def _settle_pending_bets(self, new_result: dict) -> None:
        pending = await db.get_pending_bets()
        if not pending:
            return

        result_4d = new_result["result"]

        for bet in pending:
            # numbers field: ["belakang|besar", "belakang|ganjil"]
            numbers = bet["numbers"].split(",")
            if len(numbers) < 2:
                continue

            position  = numbers[0].split("|")[0]
            pred_bk   = numbers[0].split("|")[1]
            pred_gj   = numbers[1].split("|")[1]
            bet_per_n = bet["bet_amount"]

            win_data = self.bettor.check_category_win(position, pred_bk, pred_gj, result_4d)
            payout   = self.bettor.calculate_category_payout(
                bet_per_n,
                win_data["bk_win"],
                win_data["gj_win"],
            )

            status = "won" if (win_data["bk_win"] or win_data["gj_win"]) else "lost"
            await db.update_bet_result(bet["id"], status, payout["total_won"])

            if payout["total_won"] > 0:
                await self.money_manager.record_win(
                    payout["total_wagered"], payout["total_won"]
                )
            else:
                await self.money_manager.record_loss(payout["total_wagered"])

            consecutive = await self.money_manager.get_consecutive_losses()
            daily_loss  = await self.money_manager.get_daily_loss()

            await self.notifier.send_result(
                periode=bet["periode"],
                draw_result_4d=result_4d,
                position=position,
                predicted_bk=pred_bk,
                predicted_gj=pred_gj,
                actual_bk=win_data["actual_bk"],
                actual_gj=win_data["actual_gj"],
                win_bk=win_data["bk_win"],
                win_gj=win_data["gj_win"],
                total_wagered=payout["total_wagered"],
                total_won=payout["total_won"],
                consecutive_losses=consecutive,
                daily_loss=daily_loss,
            )

    # ─── Daily summary ────────────────────────────────────────────────────────

    async def run_daily_summary(self) -> None:
        today   = _now_wib().strftime("%Y-%m-%d")
        stats   = await db.get_daily_stats(today)
        balance = await self.auth.get_balance()

        if stats:
            await self.notifier.send_daily_summary(
                date=today,
                total_bets=stats["total_bets"],
                total_wagered=stats["total_wagered"],
                total_won=stats["total_won"],
                win_count=stats["win_count"],
                loss_count=stats["loss_count"],
                final_balance=balance,
            )
        else:
            await self.notifier.send_info(f"Tidak ada bet hari ini ({today})")

        await self.money_manager.midnight_reset()

    # ─── Startup / Shutdown ───────────────────────────────────────────────────

    async def startup(self) -> None:
        await db.init_db()
        if not await self.auth.login():
            logger.error("Login awal gagal — cek kredensial di .env")
            sys.exit(1)
        balance = await self.auth.get_balance()
        logger.info("Login berhasil. Balance: Rp%s", f"{balance:,}" if balance else "?")
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
        bot.run_betting_cycle,
        CronTrigger(minute=BET_START_MINUTE, timezone="Asia/Jakarta"),
        id="betting_cycle",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=120,
    )

    scheduler.add_job(
        bot.run_daily_summary,
        CronTrigger(hour=23, minute=55, timezone="Asia/Jakarta"),
        id="daily_summary",
        max_instances=1,
    )

    scheduler.start()
    logger.info(
        "Scheduler aktif. Siklus bet setiap jam di menit :%02d. Dry run: %s",
        BET_START_MINUTE, dry_run,
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
    parser = argparse.ArgumentParser(description="Hokidraw 2D Lottery Bot")
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
