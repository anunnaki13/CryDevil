"""Telegram command handler for single-bot multi-position mode."""

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Awaitable, Callable, Optional

from telegram import BotCommand, Update
from telegram.ext import Application, CommandHandler, ContextTypes

from config import (
    DEFAULT_OPERATION_MODE,
    DB_PATH,
    INSTANCE_LABEL,
    KNOWLEDGE_BASE_HISTORY_LIMIT,
    LLM_PRIMARY,
    POSITIONS,
    SLOTS,
    TELEGRAM_BOT_TOKEN,
    TELEGRAM_CHAT_ID,
    TELEGRAM_COMMANDS_ENABLED,
    TELEGRAM_PREDICT_COOLDOWN_SECONDS,
    get_operation_profile,
    normalize_operation_mode,
)
from modules import database as db
from modules.auth import AuthManager
from modules.categories import CHOICE_LABELS, DIMENSION_LABELS, POSITION_LABELS, format_slot, parse_result_full
from modules.money_manager import MoneyManager
from modules.predictor import Predictor
from modules.scraper import Scraper

logger = logging.getLogger(__name__)

_WIB = timezone(timedelta(hours=7))


def _now_wib() -> datetime:
    return datetime.now(_WIB)


def _today_wib() -> str:
    return _now_wib().strftime("%Y-%m-%d")


def _idr(value: int | float | None) -> str:
    if value is None:
        return "?"
    return f"Rp{int(value):,}"


def _net(value: int | float | None) -> str:
    if value is None:
        return "?"
    amount = int(value)
    return f"+Rp{amount:,}" if amount > 0 else (f"-Rp{abs(amount):,}" if amount < 0 else "Rp0")


def _choice_label(choice: str) -> str:
    return CHOICE_LABELS.get(choice, choice)


class TelegramCommands:
    def __init__(
        self,
        auth: AuthManager,
        money_manager: MoneyManager,
        scraper: Optional[Scraper] = None,
        predictor: Optional[Predictor] = None,
        signal_snapshot_writer: Optional[Callable[..., Awaitable[None]]] = None,
        bet_now_requester: Optional[Callable[[], Awaitable[str]]] = None,
    ) -> None:
        self._auth = auth
        self._mm = money_manager
        self._scraper = scraper
        self._predictor = predictor
        self._signal_snapshot_writer = signal_snapshot_writer
        self._bet_now_requester = bet_now_requester
        self._app: Optional[Application] = None
        self._paused = False
        self._last_predict_at: Optional[datetime] = None
        self._kb_rebuild_lock = False

    @property
    def is_paused(self) -> bool:
        return self._paused

    def _is_authorized(self, update: Update) -> bool:
        return str(update.effective_chat.id) == str(TELEGRAM_CHAT_ID)

    async def _cmd_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_authorized(update):
            return
        text = (
            f"<b>{INSTANCE_LABEL} — Command List</b>\n\n"
            "/status   — Ringkasan status operasional\n"
            "/balance  — Cek saldo bot\n"
            "/history  — 10 bet terakhir + net\n"
            "/results  — 10 hasil draw terakhir\n"
            "/stats    — Statistik settle hari ini\n"
            "/profit   — Ringkasan profit bot\n"
            "/level    — Martingale 6 slot\n"
            "/signal   — Snapshot prediksi terakhir\n"
            "/predict  — Analisis manual tanpa pasang bet\n"
            "/kb       — Status knowledge base aktif\n"
            "/kbbuild  — Build knowledge base dari 400 history\n"
            "/mode     — Lihat/ganti mode aman-sedang-agresif\n"
            "/betnow   — Pasang bet sekarang untuk periode aktif\n"
            "/pause    — Pause bot\n"
            "/resume   — Resume bot\n"
            "/help     — Tampilkan menu ini"
        )
        await update.message.reply_text(text, parse_mode="HTML")

    async def _cmd_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_authorized(update):
            return
        summary = await self._mm.get_status_summary()
        balance = await self._auth.get_balance()
        last_period = await db.get_state("last_period", "-")
        pause_str = "PAUSED" if self._paused else "AKTIF"
        mode_label = summary.get("mode_label", "SEDANG")
        top_slots = sorted(summary["slots"].items(), key=lambda item: (-item[1]["level"], item[0]))[:6]
        slot_lines = [
            f"{format_slot(slot)}: Lv{info['level']} | {_idr(info['bet'])}/angka | loss {info['losses']}"
            for slot, info in top_slots
        ]
        text = (
            f"<b>Status Bot {INSTANCE_LABEL}</b>\n\n"
            f"Status    : {pause_str}\n"
            f"Mode      : {mode_label}\n"
            f"Saldo     : {_idr(balance)}\n"
            f"Strategi  : 1 bet terbaik dari 6 kandidat\n"
            f"LLM       : {LLM_PRIMARY}\n"
            f"Threshold : {float(summary['threshold']):.0%}\n"
            f"Periode terakhir: {last_period}\n\n"
            f"<b>Martingale</b>\n" + "\n".join(slot_lines) + "\n\n"
            f"<b>Limit Harian</b>\n"
            f"Rugi hari ini : {_idr(summary['daily_loss'])} / {_idr(summary['daily_limit'])}\n"
            f"Sisa limit    : {_idr(summary['limit_sisa'])}\n"
            f"Limit hit     : {'YA' if summary['limit_hit'] else 'BELUM'}"
        )
        await update.message.reply_text(text, parse_mode="HTML")

    async def _cmd_balance(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_authorized(update):
            return
        balance = await self._auth.get_balance()
        text = f"Saldo {INSTANCE_LABEL} saat ini: <b>{_idr(balance)}</b>" if balance is not None else "Gagal mengambil saldo."
        await update.message.reply_text(text, parse_mode="HTML")

    async def _cmd_history(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_authorized(update):
            return
        bets = await self._get_recent_bets(10)
        if not bets:
            await update.message.reply_text("Belum ada riwayat bet.")
            return
        lines = ["<b>10 Bet Terakhir</b>\n"]
        for bet in bets:
            status = {"won": "WIN", "lost": "LOSS", "placed": "OPEN"}.get(bet["status"], "?")
            stake = int(bet["bet_amount_per_angka"]) * 50
            net = _net(int(bet["win_amount"]) - stake) if bet["status"] == "won" else (_net(-stake) if bet["status"] == "lost" else f"modal {_idr(stake)}")
            lines.append(
                f"{status} | P{bet['period']} | {format_slot(bet['bet_slot'])} {_choice_label(bet['bet_choice'])} | "
                f"Lv{bet['martingale_level']} | conf {float(bet['confidence']):.0%} | {net}"
            )
        await update.message.reply_text("\n".join(lines), parse_mode="HTML")

    async def _cmd_results(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_authorized(update):
            return
        if self._scraper is None:
            await update.message.reply_text("Scraper tidak tersedia.")
            return
        history = await self._scraper.get_draw_history(limit=10)
        if not history:
            await update.message.reply_text("Gagal mengambil history draw.")
            return
        lines = ["<b>10 Hasil Draw Terakhir</b>\n"]
        for item in history:
            period = item.get("periode") or item.get("period") or "-"
            full_number = str(item.get("result", "")).strip()
            parsed = parse_result_full(full_number)
            if not parsed:
                lines.append(f"P{period} | {full_number or '?'}")
                continue
            lines.append(
                f"P{period} | {parsed['full']} | "
                f"D {parsed['depan']} {_choice_label(parsed['depan_bk'])}/{_choice_label(parsed['depan_gj'])} | "
                f"T {parsed['tengah']} {_choice_label(parsed['tengah_bk'])}/{_choice_label(parsed['tengah_gj'])} | "
                f"B {parsed['belakang']} {_choice_label(parsed['belakang_bk'])}/{_choice_label(parsed['belakang_gj'])}"
            )
        await update.message.reply_text("\n".join(lines), parse_mode="HTML")

    async def _cmd_stats(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_authorized(update):
            return
        today = _today_wib()
        stats = await db.get_daily_stats(today)
        if not stats:
            await update.message.reply_text(f"Belum ada statistik untuk hari ini ({today}).")
            return
        total = stats["total_bets"]
        wins = stats["total_wins"]
        wr = (wins / total * 100) if total else 0
        text = (
            f"<b>Statistik Hari Ini — {today}</b>\n\n"
            f"Bet settle  : {total}\n"
            f"Menang      : {wins}\n"
            f"Kalah       : {total - wins}\n"
            f"Win rate    : {wr:.1f}%\n"
            f"Modal       : {_idr(stats['total_bet_amount'])}\n"
            f"Payout      : {_idr(stats['total_win_amount'])}\n"
            f"Net         : <b>{_net(stats['profit'])}</b>"
        )
        await update.message.reply_text(text, parse_mode="HTML")

    async def _cmd_profit(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_authorized(update):
            return
        today = _today_wib()
        today_stats = await db.get_daily_stats(today)
        today_profit = int(today_stats["profit"]) if today_stats else 0
        aggregate = await db.get_aggregate_daily_stats()
        total_periods = await db.count_distinct_bet_periods()
        balance = await self._auth.get_balance()
        text = (
            "<b>Profit Report</b>\n\n"
            f"Hari ini ({today})\n"
            f"Modal : {_idr(today_stats['total_bet_amount']) if today_stats else _idr(0)}\n"
            f"Payout: {_idr(today_stats['total_win_amount']) if today_stats else _idr(0)}\n"
            f"Net   : <b>{_net(today_profit)}</b>\n\n"
            "Akumulasi\n"
            f"Hari tercatat : {int(aggregate['total_days'])}\n"
            f"Periode bet   : {total_periods}\n"
            f"Modal total   : {_idr(aggregate['total_bet_amount'])}\n"
            f"Payout total  : {_idr(aggregate['total_win_amount'])}\n"
            f"Net total     : <b>{_net(aggregate['profit'])}</b>\n"
            f"Saldo saat ini: {_idr(balance)}"
        )
        await update.message.reply_text(text, parse_mode="HTML")

    async def _cmd_level(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_authorized(update):
            return
        summary = await self._mm.get_status_summary()
        levels_str = " → ".join(f"Rp{x:,}" for x in summary["martingale_levels"])
        lines = [f"<b>Martingale 6 Slot — {summary['mode_label']}</b>\n", f"Daftar level: {levels_str}\n"]
        for slot in SLOTS:
            info = summary["slots"][slot]
            lines.append(
                f"{format_slot(slot)}: Lv {info['level']} | {_idr(info['bet'])}/angka | "
                f"modal {_idr(info['bet'] * 50)} | kalah berturut {info['losses']}"
            )
        await update.message.reply_text("\n".join(lines), parse_mode="HTML")

    async def _cmd_mode(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_authorized(update):
            return

        if not context.args:
            current = normalize_operation_mode(await db.get_state("operation_mode", DEFAULT_OPERATION_MODE))
            current_profile = get_operation_profile(current)
            lines = [
                "<b>Mode Operasional</b>\n",
                f"Aktif: <b>{current_profile['label']}</b>",
                f"Threshold: {current_profile['threshold']:.0%}",
                f"Levels: {' → '.join(f'Rp{x:,}' for x in current_profile['martingale_levels'])}",
                "",
                "Ganti mode:",
                "/mode aman",
                "/mode sedang",
                "/mode agresif",
            ]
            await update.message.reply_text("\n".join(lines), parse_mode="HTML")
            return

        requested = normalize_operation_mode(context.args[0])
        if context.args[0].strip().lower() not in ("aman", "sedang", "agresif"):
            await update.message.reply_text("Mode tidak valid. Gunakan: /mode aman | sedang | agresif")
            return

        await db.set_state("operation_mode", requested)
        profile = get_operation_profile(requested)
        lines = [
            "<b>Mode Berhasil Diubah</b>\n",
            f"Mode aktif: <b>{profile['label']}</b>",
            f"Threshold: {profile['threshold']:.0%}",
            f"Levels: {' → '.join(f'Rp{x:,}' for x in profile['martingale_levels'])}",
            "",
            "Perubahan berlaku untuk bet berikutnya.",
        ]
        await update.message.reply_text("\n".join(lines), parse_mode="HTML")

    async def _cmd_kb(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_authorized(update):
            return
        kb = await db.get_active_knowledge_base()
        if not kb:
            await update.message.reply_text(
                "Belum ada knowledge base aktif. Jalankan /kbbuild untuk menarik 400 history dan membangun knowledge base."
            )
            return
        lines = [
            "<b>Knowledge Base Aktif</b>\n",
            f"Dataset : {kb['source_count']} hasil",
            f"Periode : {kb['period_from']} -> {kb['period_to']}",
            f"Model   : {kb['model']}",
            f"Sumber  : {kb['source']}",
            f"Dibuat  : {kb['created_at']}",
            "",
            kb["summary_text"],
        ]
        await update.message.reply_text("\n".join(lines), parse_mode="HTML")

    async def _cmd_kbbuild(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_authorized(update):
            return
        if self._scraper is None or self._predictor is None:
            await update.message.reply_text("Knowledge base builder belum terhubung.")
            return
        if self._kb_rebuild_lock:
            await update.message.reply_text("Build knowledge base masih berjalan. Tunggu selesai dulu.")
            return

        self._kb_rebuild_lock = True
        await update.message.reply_text(
            f"Memulai build knowledge base dari {KNOWLEDGE_BASE_HISTORY_LIMIT} history. Proses ini manual dan bisa makan waktu."
        )
        try:
            if not await self._auth.ensure_logged_in():
                await update.message.reply_text("Login gagal atau sesi expired.")
                return

            history = await self._scraper.get_draw_history(limit=KNOWLEDGE_BASE_HISTORY_LIMIT)
            if len(history) < 50:
                await update.message.reply_text("History yang berhasil diambil terlalu sedikit untuk build knowledge base.")
                return

            kb = await self._predictor.rebuild_knowledge_base(history, source="telegram_manual")
            if kb is None:
                await update.message.reply_text("Build knowledge base gagal. Cek log untuk detail error LLM/parse.")
                return

            lines = [
                "<b>Knowledge Base Berhasil Dibangun</b>\n",
                f"Dataset : {kb['source_count']} hasil",
                f"Periode : {kb['period_from']} -> {kb['period_to']}",
                f"Model   : {kb['model']}",
                "",
                kb["summary_text"],
            ]
            await update.message.reply_text("\n".join(lines), parse_mode="HTML")
        finally:
            self._kb_rebuild_lock = False

    async def _cmd_signal(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_authorized(update):
            return
        raw = await db.get_state("last_signal_snapshot")
        if not raw:
            await update.message.reply_text("Belum ada snapshot prediksi.")
            return
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            await update.message.reply_text("Snapshot prediksi rusak.")
            return
        ranking = data.get("ranking", [])
        lines = [
            "<b>Signal Snapshot</b>\n",
            f"Periode  : {data.get('period', '-')}",
            f"Decision : {data.get('decision', '-')}",
            f"Selected : {format_slot(data.get('selected_slot', '-')) if data.get('selected_slot') else '-'} | "
            f"{_choice_label(data.get('selected_choice', '-'))} | {float(data.get('selected_confidence', 0.0)):.0%}",
            "",
            "<b>Ranking</b>",
        ]
        for item in ranking[:6]:
            lines.append(
                f"{format_slot(item['slot'])} | {_choice_label(item['choice'])} | "
                f"{float(item['confidence']):.0%} | {item.get('reason', '-')}"
            )
        await update.message.reply_text("\n".join(lines), parse_mode="HTML")

    async def _cmd_predict(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_authorized(update):
            return
        if self._scraper is None or self._predictor is None or self._signal_snapshot_writer is None:
            await update.message.reply_text("Predictor manual belum terhubung.")
            return
        now = datetime.now(timezone.utc)
        if self._last_predict_at is not None:
            elapsed = (now - self._last_predict_at).total_seconds()
            if elapsed < TELEGRAM_PREDICT_COOLDOWN_SECONDS:
                await update.message.reply_text(
                    f"/predict masih cooldown. Coba lagi dalam {int(TELEGRAM_PREDICT_COOLDOWN_SECONDS - elapsed)} detik."
                )
                return
        self._last_predict_at = now
        await update.message.reply_text("Menjalankan analisis manual. Tidak ada bet yang akan dipasang.")

        if not await self._auth.ensure_logged_in():
            self._last_predict_at = None
            await update.message.reply_text("Login gagal atau sesi expired.")
            return
        history = await self._scraper.get_draw_history()
        if not history:
            self._last_predict_at = None
            await update.message.reply_text("Gagal ambil history draw.")
            return
        period = await self._scraper.get_current_periode()
        if not period:
            self._last_predict_at = None
            await update.message.reply_text("Gagal ambil periode saat ini.")
            return

        prediction = await self._predictor.analyze(history)
        if prediction is None:
            self._last_predict_at = None
            await update.message.reply_text("Prediksi gagal.")
            return

        best = prediction["ranking"][0]
        for item in prediction["ranking"]:
            await db.save_prediction_run(
                period,
                item["slot"],
                item["target"],
                item["dimension"],
                item["choice"],
                item["confidence"],
                "manual",
                selected_for_bet=item["slot"] == best["slot"],
                reason=item.get("reason", ""),
            )
        await self._signal_snapshot_writer(period, prediction, selected=best, decision="ANALYZED", source="manual")
        lines = [
            "<b>Manual Predict</b>\n",
            f"Periode: {period}",
            f"Selected: {format_slot(best['slot'])} | {_choice_label(best['choice'])} | {best['confidence']:.0%}",
            "",
            "<b>Ranking</b>",
        ]
        for item in prediction["ranking"][:6]:
            lines.append(f"{format_slot(item['slot'])} | {_choice_label(item['choice'])} | {item['confidence']:.0%}")
        await update.message.reply_text("\n".join(lines), parse_mode="HTML")

    async def _cmd_betnow(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_authorized(update):
            return
        if self._bet_now_requester is None:
            await update.message.reply_text("Fitur betnow belum terhubung.")
            return
        await update.message.reply_text("Memproses BET NOW untuk periode aktif.")
        result = await self._bet_now_requester()
        await update.message.reply_text(result, parse_mode="HTML")

    async def _cmd_pause(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_authorized(update):
            return
        self._paused = True
        await db.set_state("bot_paused", "1")
        await update.message.reply_text("Bot di-PAUSE. Siklus berikutnya akan di-skip.")

    async def _cmd_resume(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_authorized(update):
            return
        self._paused = False
        await db.set_state("bot_paused", "0")
        await update.message.reply_text("Bot RESUMED. Siklus berikutnya akan berjalan normal.")

    async def _get_recent_bets(self, limit: int = 10) -> list[dict]:
        async with __import__("aiosqlite").connect(DB_PATH) as conn:
            conn.row_factory = __import__("aiosqlite").Row
            async with conn.execute("SELECT * FROM bets ORDER BY id DESC LIMIT ?", (limit,)) as cur:
                return [dict(row) for row in await cur.fetchall()]

    async def start(self) -> None:
        if not TELEGRAM_COMMANDS_ENABLED:
            logger.info("Telegram commands disabled for instance %s", INSTANCE_LABEL)
            return
        if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
            logger.warning("Telegram commands disabled — token/chat_id not set")
            return

        self._app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
        self._app.add_handler(CommandHandler("start", self._cmd_help))
        self._app.add_handler(CommandHandler("help", self._cmd_help))
        self._app.add_handler(CommandHandler("status", self._cmd_status))
        self._app.add_handler(CommandHandler("balance", self._cmd_balance))
        self._app.add_handler(CommandHandler("history", self._cmd_history))
        self._app.add_handler(CommandHandler("results", self._cmd_results))
        self._app.add_handler(CommandHandler("stats", self._cmd_stats))
        self._app.add_handler(CommandHandler("profit", self._cmd_profit))
        self._app.add_handler(CommandHandler("level", self._cmd_level))
        self._app.add_handler(CommandHandler("signal", self._cmd_signal))
        self._app.add_handler(CommandHandler("predict", self._cmd_predict))
        self._app.add_handler(CommandHandler("kb", self._cmd_kb))
        self._app.add_handler(CommandHandler("kbbuild", self._cmd_kbbuild))
        self._app.add_handler(CommandHandler("mode", self._cmd_mode))
        self._app.add_handler(CommandHandler("betnow", self._cmd_betnow))
        self._app.add_handler(CommandHandler("pause", self._cmd_pause))
        self._app.add_handler(CommandHandler("resume", self._cmd_resume))

        await self._app.bot.set_my_commands([
            BotCommand("status", "Ringkasan status bot"),
            BotCommand("balance", "Cek saldo bot"),
            BotCommand("history", "10 bet terakhir + net"),
            BotCommand("results", "10 hasil draw terakhir"),
            BotCommand("stats", "Statistik settle hari ini"),
            BotCommand("profit", "Ringkasan profit bot"),
            BotCommand("level", "Martingale 6 slot"),
            BotCommand("signal", "Snapshot prediksi terakhir"),
            BotCommand("predict", "Analisis manual tanpa bet"),
            BotCommand("kb", "Lihat knowledge base aktif"),
            BotCommand("kbbuild", "Build knowledge base 400 history"),
            BotCommand("mode", "Lihat atau ganti mode"),
            BotCommand("betnow", "Bet sekarang untuk periode aktif"),
            BotCommand("pause", "Pause bot"),
            BotCommand("resume", "Resume bot"),
            BotCommand("help", "Daftar perintah"),
        ])

        paused = await db.get_state("bot_paused", "0")
        self._paused = paused == "1"
        await self._app.initialize()
        await self._app.start()
        await self._app.updater.start_polling(drop_pending_updates=True)
        logger.info("Telegram command listener aktif")

    async def stop(self) -> None:
        if self._app:
            await self._app.updater.stop()
            await self._app.stop()
            await self._app.shutdown()
            logger.info("Telegram command listener dihentikan")
