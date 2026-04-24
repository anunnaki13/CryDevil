"""
Hokidraw single-bot multi-position auto-betting bot.

Bot menganalisis 2D depan, tengah, dan belakang sekaligus.
Untuk setiap posisi, bot menilai BK dan GJ, lalu hanya memasang
satu taruhan terbaik berdasarkan confidence tertinggi global.
"""

import argparse
import asyncio
import json
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from typing import Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from config import (
    AUTO_RELEARN_LOSS_STREAK,
    BACKLOG_RECOVERY_LIMIT,
    DAILY_LOSS_LIMIT,
    DEFAULT_OPERATION_MODE,
    get_strategy_threshold,
    HISTORY_WINDOW,
    INSTANCE_LABEL,
    KNOWLEDGE_BASE_HISTORY_LIMIT,
    LOG_PATH,
    MAX_POLL_ATTEMPTS,
    POLL_INTERVAL_SECONDS,
    POLL_START_MINUTE,
    POSITIONS,
    BET_DEADLINE_MINUTE,
    validate_config,
)
from modules import database as db
from modules.auth import AuthManager
from modules.bettor import Bettor
from modules.categories import get_target_result, parse_result_full
from modules.money_manager import MoneyManager
from modules.notifier import TelegramNotifier
from modules.predictor import Predictor
from modules.scraper import Scraper
from modules.telegram_commands import TelegramCommands

_log_dir = os.path.dirname(LOG_PATH)
if _log_dir:
    os.makedirs(_log_dir, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout), logging.FileHandler(LOG_PATH, encoding="utf-8")],
)
logger = logging.getLogger("hokidraw.main")

_WIB = timezone(timedelta(hours=7))


def _now_wib() -> datetime:
    return datetime.now(_WIB)


def _today_wib() -> str:
    return _now_wib().strftime("%Y-%m-%d")


def _normalize_analysis_scope(value: str | None) -> str:
    normalized = (value or "all").strip().lower()
    return normalized if normalized in ("all", *POSITIONS) else "all"


def _normalize_strategy_mode(value: str | None) -> str:
    normalized = (value or "auto").strip().lower()
    return normalized if normalized in ("auto", "zigzag", "trend", "heuristic", "llm", "hybrid") else "auto"


class HokidrawBot:
    def __init__(self, dry_run: bool = False) -> None:
        self.dry_run = dry_run
        self.auth = AuthManager()
        self.scraper = Scraper(self.auth)
        self.predictor = Predictor()
        self.bettor = Bettor(self.auth)
        self.mm = MoneyManager()
        self.notifier = TelegramNotifier()
        self.tg_commands = TelegramCommands(
            self.auth,
            self.mm,
            scraper=self.scraper,
            predictor=self.predictor,
            signal_snapshot_writer=self._store_signal_snapshot,
            bet_now_requester=self.request_bet_now,
        )
        self._last_period: Optional[str] = None
        self._cycle_lock = asyncio.Lock()
        self._auto_relearn_lock = asyncio.Lock()

    async def _sync_site_status_alert(self) -> None:
        status = str(await db.get_state("site_status", "unknown") or "unknown")
        notified = str(await db.get_state("site_status_notified", "") or "")
        detail = str(await db.get_state("site_status_detail", "") or "")
        if status == notified:
            return

        if status == "maintenance":
            message = (
                "Website terdeteksi <b>MAINTENANCE</b>. "
                "Bot akan menahan aksi yang butuh login/scrape dan mencoba lagi pada siklus berikutnya."
            )
            if detail:
                message += f"\nDetail: <code>{detail}</code>"
            await self.notifier.notify_alert(message)
        elif status == "normal" and notified in {"maintenance", "session_invalid", "degraded"}:
            message = "Website kembali <b>ONLINE</b>. Session dan endpoint utama kembali normal."
            if detail:
                message += f"\nDetail terakhir: <code>{detail}</code>"
            await self.notifier.notify_alert(message)
        elif status in {"session_invalid", "degraded"}:
            message = (
                "Website/login sedang <b>tidak stabil</b>. "
                "Bot akan retry otomatis pada siklus berikutnya."
            )
            if detail:
                message += f"\nDetail: <code>{detail}</code>"
            await self.notifier.notify_alert(message)

        await db.set_state("site_status_notified", status)

    async def _get_analysis_scope(self) -> str:
        return _normalize_analysis_scope(await db.get_state("analysis_scope", "all"))

    async def _get_strategy_mode(self) -> str:
        return _normalize_strategy_mode(await db.get_state("strategy_mode", "auto"))

    async def _store_signal_snapshot(
        self,
        period: str,
        prediction: dict,
        *,
        selected: dict | None,
        decision: str,
        source: str,
        note: str = "",
        threshold: float | None = None,
    ) -> None:
        if threshold is None:
            threshold = (await self.mm.get_operation_profile())["threshold"]
        payload = {
            "period": period,
            "source": source,
            "scope": prediction.get("scope", await self._get_analysis_scope()),
            "strategy_mode": prediction.get("strategy_mode", await self._get_strategy_mode()),
            "selected_method": prediction.get("selected_method"),
            "method_candidates": prediction.get("method_candidates", []),
            "active_targets": prediction.get("active_targets", list(POSITIONS)),
            "decision": decision,
            "selected_slot": selected.get("slot") if selected else None,
            "selected_target": selected.get("target") if selected else None,
            "selected_dimension": selected.get("dimension") if selected else None,
            "selected_choice": selected.get("choice") if selected else None,
            "selected_confidence": selected.get("confidence") if selected else None,
            "selected_score": selected.get("score") if selected else None,
            "selected_reason": selected.get("reason") if selected else None,
            "threshold": threshold,
            "positions": prediction.get("positions", {}),
            "ranking": prediction.get("ranking", []),
            "note": note,
        }
        await db.set_state("last_signal_snapshot", json.dumps(payload, ensure_ascii=True))

    async def _execute_bet_flow(
        self,
        *,
        now: datetime,
        allow_after_deadline: bool = False,
        forced_period: str | None = None,
        trigger: str = "scheduled",
    ) -> tuple[bool, str]:
        if not await self.mm.check_and_enforce_daily_limit():
            already_notified = await db.get_state("daily_limit_notified", "0")
            if already_notified != "1":
                await self.notifier.send_limit_reached(await self.mm.get_daily_loss(), DAILY_LOSS_LIMIT)
                await db.set_state("daily_limit_notified", "1")
            return False, "daily_limit"

        if not allow_after_deadline and now.minute > BET_DEADLINE_MINUTE:
            logger.info("Lewat deadline menit :%02d — skip bet", BET_DEADLINE_MINUTE)
            return False, "past_deadline"

        history = await self.scraper.get_draw_history()
        if not history:
            await self.notifier.notify_alert("Gagal ambil history draw")
            return False, "history_failed"

        period = forced_period or await self.scraper.get_current_periode()
        if not period:
            await self.notifier.notify_alert("Gagal ambil periode saat ini")
            return False, "period_failed"
        if period == self._last_period:
            logger.info("Skip bet: periode %s sudah pernah dibet sebelumnya", period)
            return False, f"already_bet:{period}"

        profile = await self.mm.get_operation_profile()
        scope = await self._get_analysis_scope()
        strategy_mode = await self._get_strategy_mode()
        prediction = await self.predictor.analyze(history, scope=scope, strategy_mode=strategy_mode)
        if prediction is None or not prediction.get("ranking"):
            await self.notifier.notify_alert(f"Prediksi gagal untuk scope {scope} dengan strategy {strategy_mode}")
            return False, "prediction_failed"

        best = prediction["ranking"][0]
        selected_strategy = str(prediction.get("selected_method") or strategy_mode)
        threshold = float(get_strategy_threshold(selected_strategy, float(profile["threshold"])))
        best_score = float(best.get("score", best["confidence"]))
        await self._store_signal_snapshot(
            period,
            prediction,
            selected=best,
            decision="ANALYZED",
            source="auto",
            note=trigger,
            threshold=threshold,
        )

        for item in prediction["ranking"]:
            await db.save_prediction_run(
                period,
                item["slot"],
                item["target"],
                item["dimension"],
                item["choice"],
                item["confidence"],
                "auto",
                selected_for_bet=item["slot"] == best["slot"],
                reason=item.get("reason", ""),
            )

        if best_score < threshold:
            await self._store_signal_snapshot(
                period,
                prediction,
                selected=best,
                decision="SKIP",
                source="auto",
                note="below_threshold_score",
                threshold=threshold,
            )
            return False, f"below_threshold_score:{period}"

        amount = await self.mm.get_bet_amount(best["slot"])
        level = await self.mm.get_level(best["slot"])
        response = await self.bettor.place_bet(best["choice"], amount, best["target"], dry_run=self.dry_run)
        if not self.bettor.is_bet_successful(response):
            return False, f"bet_failed:{self.bettor.get_failure_reason(response)}"

        await db.save_bet(
            period=period,
            target_position=best["target"],
            dimension=best["dimension"],
            bet_slot=best["slot"],
            choice=best["choice"],
            bet_amount_per_angka=amount,
            total_amount=amount * 50,
            martingale_level=level,
            confidence=best["confidence"],
            api_response=str(response),
        )

        self._last_period = period
        await db.set_state("last_period", period)
        balance_after = await self.auth.get_balance()
        await self._store_signal_snapshot(
            period,
            prediction,
            selected=best,
            decision="BET",
            source="auto",
            note=trigger,
            threshold=threshold,
        )
        await self.notifier.notify_bet_placed(
            periode=period,
            target_position=best["target"],
            dimension=best["dimension"],
            choice=best["choice"],
            confidence=best["confidence"],
            score=float(best.get("score", best["confidence"])),
            selected_reason=str(best.get("reason", "")),
            strategy_mode=str(prediction.get("strategy_mode", strategy_mode)),
            selected_method=str(prediction.get("selected_method", strategy_mode)),
            threshold=threshold,
            amount=amount,
            level=level,
            ranking=prediction["ranking"],
            balance=balance_after,
            dry_run=self.dry_run,
        )
        return True, f"bet_placed:{period}"

    async def hourly_cycle(self) -> None:
        if self._cycle_lock.locked():
            logger.info("Siklus dilewati karena masih ada eksekusi lain yang berjalan")
            return

        async with self._cycle_lock:
            if not await self.auth.ensure_logged_in():
                await self._sync_site_status_alert()
                site_status = str(await db.get_state("site_status", "unknown") or "unknown")
                if site_status != "maintenance":
                    await self.notifier.notify_alert("Login gagal — skip siklus ini")
                return
            await self._sync_site_status_alert()

            results = []
            for attempt in range(1, MAX_POLL_ATTEMPTS + 1):
                results = await self._detect_new_results()
                if results:
                    break
                if attempt < MAX_POLL_ATTEMPTS:
                    await asyncio.sleep(POLL_INTERVAL_SECONDS)

            if results:
                for result in results:
                    await self._process_result(result)
            else:
                logger.warning("Tidak ada result baru setelah %s polling", MAX_POLL_ATTEMPTS)

            if self.tg_commands.is_paused:
                logger.info("Bot sedang PAUSE — settlement tetap jalan, skip bet baru")
                return

            success, note = await self._execute_bet_flow(now=_now_wib(), trigger="scheduled")
            logger.info("Hourly cycle selesai: success=%s note=%s", success, note)

    async def request_bet_now(self) -> str:
        if self._cycle_lock.locked():
            return "Eksekusi lain masih berjalan. Tunggu beberapa detik lalu coba lagi."
        if self.tg_commands.is_paused:
            return "Bot sedang <b>PAUSED</b>. Gunakan /resume dulu."
        if not await self.auth.ensure_logged_in():
            await self._sync_site_status_alert()
            site_status = str(await db.get_state("site_status", "unknown") or "unknown")
            if site_status == "maintenance":
                return "Website sedang <b>MAINTENANCE</b>. Bot akan cek lagi otomatis pada siklus berikutnya."
            return "Login gagal atau sesi expired."
        await self._sync_site_status_alert()

        period = await self.scraper.get_current_periode()
        if not period:
            status = self.scraper.get_last_period_status()
            if status == "bet_close":
                return "Periode aktif tidak tersedia karena market sedang <b>BET CLOSE</b>."
            return "Periode aktif tidak tersedia."
        if period == self._last_period:
            return f"Periode <b>{period}</b> sudah pernah dibet."

        async with self._cycle_lock:
            success, note = await self._execute_bet_flow(
                now=_now_wib(),
                allow_after_deadline=True,
                forced_period=period,
                trigger="betnow",
            )
        return (
            f"BET NOW sukses untuk periode <b>{period}</b>."
            if success else
            f"BET NOW tidak memasang bet untuk periode <b>{period}</b> ({note})."
        )

    async def _detect_new_results(self, limit: int = BACKLOG_RECOVERY_LIMIT) -> list[dict]:
        history = await self.scraper.get_draw_history(limit=limit)
        if not history:
            return []

        pending: list[dict] = []
        for item in reversed(history):
            period = item.get("period") or item.get("periode") or ""
            if not period:
                continue
            if await db.result_exists(period):
                continue
            item.setdefault("period", period)
            item.setdefault("periode", period)
            pending.append(item)
        return pending

    async def _process_result(self, raw_result: dict) -> None:
        period = raw_result.get("period") or raw_result.get("periode") or ""
        result_4d = str(raw_result.get("result", "")).strip()
        draw_time = str(raw_result.get("draw_time", "")).strip()

        parsed = parse_result_full(result_4d)
        if not parsed:
            logger.error("Tidak bisa parse result: %s", result_4d)
            return

        await db.save_result(period, draw_time, parsed)
        await db.settle_prediction_runs(period, parsed)

        pending = await db.get_placed_bets(period)
        for bet in pending:
            target = bet["target_position"]
            result_2d = parsed[target]
            actual_choice = parsed[f"{target}_{'bk' if bet['bet_dimension'] == 'besar_kecil' else 'gj'}"]
            amount = int(bet["bet_amount_per_angka"])
            won = self.bettor.check_win(bet["bet_choice"], result_2d)
            payout = self.bettor.calculate_payout(amount, won)
            await db.settle_bet(
                bet_id=bet["id"],
                status="won" if won else "lost",
                win_amount=payout["won"],
                result_2d=result_2d,
                result_match=actual_choice,
            )
            if won:
                await self.mm.record_win(bet["bet_slot"], payout["wagered"], payout["won"])
            else:
                await self.mm.record_loss(bet["bet_slot"], payout["wagered"])

            balance = await self.auth.get_balance()
            await self.notifier.notify_result(
                periode=period,
                full_result=parsed["full"],
                target_position=target,
                result_2d=result_2d,
                actual_choice=actual_choice,
                bet_choice=bet["bet_choice"],
                won=won,
                profit=payout["net"],
                balance=balance,
            )
            await self._handle_auto_relearn(period=period, won=won)

    async def _handle_auto_relearn(self, *, period: str, won: bool) -> None:
        if AUTO_RELEARN_LOSS_STREAK <= 0:
            return

        if won:
            await db.set_state("global_consecutive_losses", "0")
            return

        streak = int(await db.get_state("global_consecutive_losses", "0")) + 1
        await db.set_state("global_consecutive_losses", str(streak))
        if streak % AUTO_RELEARN_LOSS_STREAK != 0:
            return

        last_trigger_period = await db.get_state("last_auto_relearn_period", "")
        if last_trigger_period == period:
            return
        if self._auto_relearn_lock.locked():
            logger.info("Auto relearn dilewati: rebuild sebelumnya masih berjalan")
            return

        async with self._auto_relearn_lock:
            logger.warning(
                "Loss streak global mencapai %s pada periode %s — memulai auto relearn knowledge base",
                streak,
                period,
            )
            await self.notifier.notify_alert(
                f"Loss streak mencapai {streak} kali berturut-turut. "
                f"Memulai auto relearn knowledge base dari {KNOWLEDGE_BASE_HISTORY_LIMIT} history terbaru."
            )

            if not await self.auth.ensure_logged_in():
                logger.error("Auto relearn dibatalkan: login/session tidak valid")
                return

            history = await self.scraper.get_draw_history(limit=KNOWLEDGE_BASE_HISTORY_LIMIT)
            if len(history) < max(20, KNOWLEDGE_BASE_HISTORY_LIMIT):
                logger.error("Auto relearn dibatalkan: history terlalu sedikit (%s)", len(history))
                return

            kb = await self.predictor.rebuild_knowledge_base(history, source=f"auto_loss_streak_{streak}")
            if kb is None:
                logger.error("Auto relearn gagal saat rebuild knowledge base")
                await self.notifier.notify_alert(
                    f"Auto relearn gagal setelah {streak} loss beruntun. Cek log predictor/LLM."
                )
                return

            await db.set_state("last_auto_relearn_period", period)
            await db.set_state("last_auto_relearn_at_streak", str(streak))
            await self.notifier.notify_alert(
                f"Auto relearn selesai setelah {streak} loss beruntun.\n"
                f"KB baru: {kb['source_count']} history, periode {kb['period_from']} -> {kb['period_to']}."
            )

    async def daily_summary(self) -> None:
        today = _today_wib()
        stats = await db.get_daily_stats(today)
        balance = await self.auth.get_balance()
        if balance is not None:
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
        await db.set_state("global_consecutive_losses", "0")
        await db.set_state("daily_limit_notified", "0")

    async def startup(self) -> None:
        await db.init_db()
        if await db.get_state("operation_mode") is None:
            await db.set_state("operation_mode", DEFAULT_OPERATION_MODE)
        if await db.get_state("analysis_scope") is None:
            await db.set_state("analysis_scope", "all")
        if await db.get_state("strategy_mode") is None:
            await db.set_state("strategy_mode", "auto")
        if not await self.auth.login():
            await self._sync_site_status_alert()
            logger.error("Login awal gagal — cek kredensial di .env")
            sys.exit(1)
        await self._sync_site_status_alert()
        balance = await self.auth.get_balance()
        logger.info("Login OK. Balance: Rp%s", f"{balance:,}" if balance else "?")
        saved_period = await db.get_state("last_period")
        if saved_period:
            self._last_period = saved_period
        await self.notifier.send_startup(dry_run=self.dry_run)
        await self.tg_commands.start()

    async def shutdown(self) -> None:
        await self.tg_commands.stop()
        await self.notifier.send_shutdown()
        await self.auth.close()


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
    logger.info("Scheduler aktif. Siklus setiap jam di menit :%02d. Dry run: %s", POLL_START_MINUTE, dry_run)

    try:
        while True:
            await asyncio.sleep(60)
    except (KeyboardInterrupt, SystemExit):
        logger.info("Shutdown...")
    finally:
        scheduler.shutdown(wait=False)
        await bot.shutdown()


def main() -> None:
    parser = argparse.ArgumentParser(description="Hokidraw single-bot multi-position bot")
    parser.add_argument("--dry-run", action="store_true", help="Test tanpa bet sungguhan")
    parser.add_argument("--check-config", action="store_true", help="Cek konfigurasi .env saja lalu keluar")
    args = parser.parse_args()

    validate_config(exit_on_error=not args.check_config)
    if args.check_config:
        sys.exit(0)
    if args.dry_run:
        logger.info("*** DRY RUN MODE — tidak ada bet sungguhan ***")
    asyncio.run(run(dry_run=args.dry_run))


if __name__ == "__main__":
    main()
