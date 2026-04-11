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
import json
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
    LOG_PATH, BET_MODE, BET_TARGET, INSTANCE_LABEL,
    MIN_CONFIDENCE_TO_BET,
    FLEET_SHARED_ANALYSIS, FLEET_ROLE, INSTANCE_NAME,
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
    get_target_result, parse_result_full, result_summary,
)
from modules.telegram_commands import TelegramCommands
from modules import fleet

# ─── Logging ─────────────────────────────────────────────────────────────────

_log_dir = os.path.dirname(LOG_PATH)
if _log_dir:
    os.makedirs(_log_dir, exist_ok=True)

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
        self.tg_commands   = TelegramCommands(self.auth, self.mm)
        self._last_period: Optional[str] = None

    @staticmethod
    def _pick_single_candidate(bk_data: dict, gj_data: dict) -> tuple[str, dict]:
        if bk_data["confidence"] >= gj_data["confidence"]:
            return "besar_kecil", bk_data
        return "genap_ganjil", gj_data

    async def _store_signal_snapshot(
        self,
        periode: str,
        bk_data: dict,
        gj_data: dict,
        source: str,
        decision: str,
        selected_dimension: str | None = None,
        selected_choice: str | None = None,
        selected_confidence: float | None = None,
        note: str | None = None,
    ) -> None:
        payload = {
            "period": periode,
            "target": BET_TARGET,
            "source": source,
            "decision": decision,
            "selected_dimension": selected_dimension,
            "selected_choice": selected_choice,
            "selected_confidence": selected_confidence,
            "threshold": MIN_CONFIDENCE_TO_BET,
            "besar_kecil": {
                "choice": bk_data.get("choice"),
                "confidence": bk_data.get("confidence"),
                "reason": bk_data.get("reason", ""),
            },
            "genap_ganjil": {
                "choice": gj_data.get("choice"),
                "confidence": gj_data.get("confidence"),
                "reason": gj_data.get("reason", ""),
            },
            "note": note or "",
        }
        await db.set_state("last_signal_snapshot", json.dumps(payload, ensure_ascii=True))

    async def _publish_snapshot(self, balance: Optional[int] = None) -> None:
        summary = await self.mm.get_status_summary()
        fleet.update_snapshot({
            "instance_label": INSTANCE_LABEL,
            "target": BET_TARGET,
            "enabled": fleet.is_bot_enabled(INSTANCE_NAME),
            "balance": balance if balance is not None else await self.auth.get_balance(),
            "daily_loss": summary["daily_loss"],
            "bk_level": summary["bk_level"],
            "gj_level": summary["gj_level"],
            "bk_bet": summary["bk_bet"],
            "gj_bet": summary["gj_bet"],
            "mode": BET_MODE,
        })

    # ─── Siklus utama ─────────────────────────────────────────────────────────

    async def hourly_cycle(self) -> None:
        now = _now_wib()
        logger.info("=== Siklus mulai @ %s ===", now.strftime("%H:%M WIB"))

        if FLEET_SHARED_ANALYSIS and not fleet.is_bot_enabled(INSTANCE_NAME):
            logger.info("Bot %s dimatikan dari fleet state — skip siklus ini", INSTANCE_NAME)
            return

        # 0. Cek pause
        if self.tg_commands.is_paused:
            logger.info("Bot di-PAUSE via Telegram — skip siklus ini")
            await self.notifier.notify_alert("Siklus di-skip karena bot sedang PAUSE")
            return

        # 1. Pastikan login aktif
        if not await self.auth.ensure_logged_in():
            await self.notifier.notify_alert("Login gagal — skip siklus ini")
            return

        await self._publish_snapshot()

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

        # 6. Ambil history dan prediksi LLM/plan fleet
        history = await self.scraper.get_draw_history()
        if not history:
            await self.notifier.notify_alert("Gagal ambil history draw")
            return

        # 7. Ambil periode saat ini
        periode = await self.scraper.get_current_periode()
        if not periode:
            await self.notifier.notify_alert("Gagal ambil periode saat ini")
            return

        if periode == self._last_period:
            logger.info("Sudah bet di periode %s — skip", periode)
            return

        if FLEET_SHARED_ANALYSIS:
            fleet_plan = None
            if FLEET_ROLE == "leader":
                snapshots = fleet.get_snapshots()
                fleet_plan = await self.predictor.analyze_fleet(history, snapshots)
                if fleet_plan is None:
                    await self.notifier.notify_alert("LLM fleet analysis gagal")
                    return
                fleet.write_plan({
                    "period": periode,
                    **fleet_plan,
                })
            else:
                for _ in range(10):
                    candidate = fleet.read_plan()
                    if candidate.get("period") == periode:
                        fleet_plan = candidate
                        break
                    await asyncio.sleep(2)
                if fleet_plan is None:
                    await self.notifier.notify_alert("Plan fleet belum tersedia untuk periode ini")
                    return

            my_plan = (fleet_plan.get("bots") or {}).get(INSTANCE_NAME)
            if not my_plan:
                logger.warning("Tidak ada plan untuk bot %s", INSTANCE_NAME)
                return
            if my_plan.get("target") != BET_TARGET:
                logger.warning(
                    "Plan target mismatch untuk %s: expected=%s actual=%s",
                    INSTANCE_NAME, BET_TARGET, my_plan.get("target")
                )
                return
            if my_plan.get("action") != "BET":
                logger.info("Plan fleet memutuskan SKIP untuk %s: %s", INSTANCE_NAME, my_plan.get("note", ""))
                await self._store_signal_snapshot(
                    periode,
                    my_plan["besar_kecil"],
                    my_plan["genap_ganjil"],
                    source="fleet",
                    decision="SKIP",
                    note=my_plan.get("note", ""),
                )
                return

            bk_data = my_plan["besar_kecil"]
            gj_data = my_plan["genap_ganjil"]
            logger.info(
                "[%s] Fleet plan %s → BK: %s (%.0f%%) | GJ: %s (%.0f%%) | risk=%s",
                INSTANCE_LABEL,
                BET_TARGET,
                bk_data["choice"], bk_data["confidence"] * 100,
                gj_data["choice"], gj_data["confidence"] * 100,
                my_plan.get("mode_risiko", "normal"),
            )
        else:
            prediction = await self.predictor.analyze(history)
            if prediction is None:
                await self.notifier.notify_alert("LLM prediction gagal")
                return

            bk_data = prediction["besar_kecil"]
            gj_data = prediction["genap_ganjil"]

            logger.info(
                "[%s] Prediksi %s → BK: %s (%.0f%%) | GJ: %s (%.0f%%)",
                INSTANCE_LABEL,
                BET_TARGET,
                bk_data["choice"], bk_data["confidence"] * 100,
                gj_data["choice"], gj_data["confidence"] * 100,
            )
            await self._store_signal_snapshot(
                periode,
                bk_data,
                gj_data,
                source="single",
                decision="ANALYZED",
            )

        # 8. Ambil bet amount dari money manager (per dimensi)
        bk_amount = await self.mm.get_bet_amount("besar_kecil")
        gj_amount = await self.mm.get_bet_amount("genap_ganjil")
        bk_level  = await self.mm.get_level("besar_kecil")
        gj_level  = await self.mm.get_level("genap_ganjil")
        effective_bet_mode = BET_MODE
        if FLEET_SHARED_ANALYSIS and FLEET_ROLE in ("leader", "worker"):
            if my_plan.get("mode_risiko") == "conservative":
                effective_bet_mode = "single"

        # 9. Pasang bet
        if effective_bet_mode == "double":
            results = await self.bettor.place_double_bet(
                bk_data["choice"], gj_data["choice"],
                bk_amount, gj_amount,
                dry_run=self.dry_run,
            )
            bk_resp, gj_resp = results[0], results[1]
        else:
            chosen_dimension, chosen_data = self._pick_single_candidate(bk_data, gj_data)
            chosen_conf = chosen_data["confidence"]

            if chosen_conf < MIN_CONFIDENCE_TO_BET:
                logger.info(
                    "[%s] Skip bet: confidence tertinggi %.0f%% masih di bawah threshold %.0f%%",
                    INSTANCE_LABEL,
                    chosen_conf * 100,
                    MIN_CONFIDENCE_TO_BET * 100,
                )
                await self.notifier.notify_alert(
                    "Skip bet: confidence tertinggi "
                    f"{chosen_conf:.0%} masih di bawah threshold {MIN_CONFIDENCE_TO_BET:.0%}"
                )
                await self._store_signal_snapshot(
                    periode,
                    bk_data,
                    gj_data,
                    source="fleet" if FLEET_SHARED_ANALYSIS else "single",
                    decision="SKIP",
                    selected_dimension=chosen_dimension,
                    selected_choice=chosen_data["choice"],
                    selected_confidence=chosen_conf,
                    note="below_threshold",
                )
                return

            await self._store_signal_snapshot(
                periode,
                bk_data,
                gj_data,
                source="fleet" if FLEET_SHARED_ANALYSIS else "single",
                decision="BET",
                selected_dimension=chosen_dimension,
                selected_choice=chosen_data["choice"],
                selected_confidence=chosen_conf,
            )

            if chosen_dimension == "besar_kecil":
                bk_resp = await self.bettor.place_bet(bk_data["choice"], bk_amount, self.dry_run)
                gj_resp = None
            else:
                gj_resp = await self.bettor.place_bet(gj_data["choice"], gj_amount, self.dry_run)
                bk_resp = None

        # 10. Simpan ke DB
        await self._save_bets(periode, bk_data, gj_data, bk_amount, gj_amount,
                              bk_level, gj_level, bk_resp, gj_resp)

        self._last_period = periode
        await db.set_state("last_period", periode)

        # 11. Notifikasi
        await self.notifier.notify_bet_placed(
            periode=periode,
            bk_choice=bk_data["choice"] if bk_resp else None,
            gj_choice=gj_data["choice"] if gj_resp else None,
            bk_confidence=bk_data["confidence"] if bk_resp else None,
            gj_confidence=gj_data["confidence"] if gj_resp else None,
            bk_amount=bk_amount,
            gj_amount=gj_amount,
            bk_level=bk_level if bk_resp else None,
            gj_level=gj_level if gj_resp else None,
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

        target_data = get_target_result(parsed, BET_TARGET)
        number_2d   = target_data["number_2d"]
        actual_bk   = target_data["besar_kecil"]
        actual_gj   = target_data["genap_ganjil"]

        logger.info("Result %s: %s → %s", period, result_summary(result_4d),
                    f"{BET_TARGET}=2D {number_2d} {actual_bk}+{actual_gj}")

        # Simpan ke DB
        await db.save_result(
            period=period,
            draw_time=draw_time,
            full_number=parsed["full"],
            target_position=BET_TARGET,
            target_number_2d=number_2d,
            target_bk=actual_bk,
            target_gj=actual_gj,
        )

        # Settle hanya bet untuk periode result ini.
        # Menggunakan semua pending bets berisiko salah settle jika bot sempat tertinggal beberapa draw.
        pending = await db.get_placed_bets(period)
        for bet in pending:
            bet_choice = bet["bet_choice"]
            dimension  = bet["bet_dimension"]
            amount     = int(bet["bet_amount_per_angka"])
            won        = self.bettor.check_win(bet_choice, number_2d)
            payout     = self.bettor.calculate_payout(amount, won)

            status = "won" if won else "lost"
            await db.settle_bet(
                bet_id=bet["id"],
                status=status,
                win_amount=payout["won"],
                result_2d=number_2d,
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
            await self._notify_result(period, result_4d, number_2d,
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
                target_position=BET_TARGET,
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
                target_position=BET_TARGET,
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
        await self._publish_snapshot(balance=balance)

        # Restore last_period dari DB agar tidak bet duplikat setelah restart
        saved_period = await db.get_state("last_period")
        if saved_period:
            self._last_period = saved_period
            logger.info("Restored last_period dari DB: %s", saved_period)

        await self.notifier.send_startup(dry_run=self.dry_run)

        # Start Telegram command listener
        await self.tg_commands.start()

    async def shutdown(self) -> None:
        await self.tg_commands.stop()
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
