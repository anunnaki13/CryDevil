"""Telegram command handler for single-bot multi-position mode."""

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Awaitable, Callable, Optional

from telegram import BotCommand, BotCommandScopeChat, Update
from telegram.ext import Application, CommandHandler, ContextTypes

from config import (
    AUTO_RELEARN_LOSS_STREAK,
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
    get_strategy_threshold,
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


def _normalize_scope(value: str | None) -> str:
    normalized = (value or "all").strip().lower()
    return normalized if normalized in ("all", *POSITIONS) else "all"


def _normalize_strategy_mode(value: str | None) -> str:
    normalized = (value or "auto").strip().lower()
    return normalized if normalized in ("auto", "zigzag", "trend", "heuristic", "llm", "hybrid") else "auto"


def _strategy_label(value: str | None) -> str:
    labels = {
        "auto": "AUTO",
        "zigzag": "ZIGZAG",
        "trend": "TREND",
        "heuristic": "HEURISTIC",
        "llm": "LLM",
        "hybrid": "HYBRID",
    }
    return labels.get(_normalize_strategy_mode(value), "AUTO")


def _extract_scope_from_source(source: str | None) -> str:
    raw = str(source or "").strip().lower()
    for scope in (*POSITIONS, "all"):
        if raw.endswith(f"_scope_{scope}"):
            return scope
    return "all"


def _format_kb_operational_lines(knowledge: dict | None, scope: str) -> list[str]:
    if not isinstance(knowledge, dict):
        return []
    positions = knowledge.get("positions")
    if not isinstance(positions, dict):
        return []

    active_positions = [scope] if scope in POSITIONS else list(POSITIONS)
    lines: list[str] = []
    for target in active_positions:
        item = positions.get(target)
        if not isinstance(item, dict):
            continue
        bk = item.get("besar_kecil", {}) if isinstance(item.get("besar_kecil"), dict) else {}
        gj = item.get("genap_ganjil", {}) if isinstance(item.get("genap_ganjil"), dict) else {}
        lines.append(
            f"{POSITION_LABELS.get(target, target)}: "
            f"BK {bk.get('bias', 'NETRAL')}/{bk.get('strength', 'lemah')} | "
            f"GJ {gj.get('bias', 'NETRAL')}/{gj.get('strength', 'lemah')}"
        )
        note_parts = [str(bk.get("note", "")).strip(), str(gj.get("note", "")).strip()]
        note = " | ".join(part for part in note_parts if part)
        if note:
            lines.append(note[:220])
    return lines


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

    async def _get_analysis_scope(self) -> str:
        return _normalize_scope(await db.get_state("analysis_scope", "all"))

    async def _get_strategy_mode(self) -> str:
        return _normalize_strategy_mode(await db.get_state("strategy_mode", "auto"))

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
            f"/kbbuild  — Build knowledge base dari {KNOWLEDGE_BASE_HISTORY_LIMIT} history\n"
            "/scope    — Lihat/ganti scope analisa posisi\n"
            "/strategy — Lihat/ganti metode prediksi\n"
            "/relearnstatus — Status auto relearn loss streak\n"
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
        scope = await self._get_analysis_scope()
        strategy = await self._get_strategy_mode()
        strategy_threshold = get_strategy_threshold(strategy, float(summary["threshold"]))
        pause_str = "PAUSED" if self._paused else "AKTIF"
        mode_label = summary.get("mode_label", "SEDANG")
        global_loss_streak = int(await db.get_state("global_consecutive_losses", "0"))
        last_relearn_period = await db.get_state("last_auto_relearn_period", "-")
        last_relearn_streak = await db.get_state("last_auto_relearn_at_streak", "-")
        top_slots = sorted(summary["slots"].items(), key=lambda item: (-item[1]["level"], item[0]))[:6]
        slot_lines = [
            f"{format_slot(slot)}: Lv{info['level']} | {_idr(info['bet'])}/angka | loss {info['losses']}"
            for slot, info in top_slots
        ]
        text = (
            f"<b>Status Bot {INSTANCE_LABEL}</b>\n\n"
            f"Status    : {pause_str}\n"
            f"Mode      : {mode_label}\n"
            f"Scope     : {scope.upper()}\n"
            f"Strategy  : {_strategy_label(strategy)}\n"
            f"Saldo     : {_idr(balance)}\n"
            f"Seleksi   : 1 bet terbaik dari kandidat scope aktif\n"
            f"LLM       : {LLM_PRIMARY}\n"
            f"Threshold : {float(strategy_threshold):.0%}\n"
            f"Periode terakhir: {last_period}\n\n"
            f"<b>Auto Relearn</b>\n"
            f"Trigger    : {AUTO_RELEARN_LOSS_STREAK} loss beruntun\n"
            f"Streak kini: {global_loss_streak}\n"
            f"Trigger terakhir: P{last_relearn_period} @ streak {last_relearn_streak}\n\n"
            f"<b>Martingale</b>\n" + "\n".join(slot_lines) + "\n\n"
            f"<b>Limit Harian</b>\n"
            f"Rugi hari ini : {_idr(summary['daily_loss'])} / {_idr(summary['daily_limit'])}\n"
            f"Sisa limit    : {_idr(summary['limit_sisa'])}\n"
            f"Limit hit     : {'YA' if summary['limit_hit'] else 'BELUM'}"
        )
        await update.message.reply_text(text, parse_mode="HTML")

    async def _cmd_relearnstatus(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_authorized(update):
            return
        kb = await db.get_active_knowledge_base()
        global_loss_streak = int(await db.get_state("global_consecutive_losses", "0"))
        last_period = await db.get_state("last_auto_relearn_period", "-")
        last_streak = await db.get_state("last_auto_relearn_at_streak", "-")
        sisa = max(0, AUTO_RELEARN_LOSS_STREAK - global_loss_streak) if AUTO_RELEARN_LOSS_STREAK > 0 else 0

        lines = [
            "<b>Auto Relearn Status</b>\n",
            f"Trigger streak : {AUTO_RELEARN_LOSS_STREAK}",
            f"Streak saat ini: {global_loss_streak}",
            f"Sisa ke trigger: {sisa}",
            f"Last trigger   : P{last_period} @ streak {last_streak}",
            f"KB window      : {KNOWLEDGE_BASE_HISTORY_LIMIT} history",
        ]
        if kb:
            lines.extend([
                "",
                "<b>KB Aktif</b>",
                f"Dataset : {kb['source_count']}",
                f"Periode : {kb['period_from']} -> {kb['period_to']}",
                f"Sumber  : {kb['source']}",
                f"Dibuat  : {kb['created_at']}",
            ])
        await update.message.reply_text("\n".join(lines), parse_mode="HTML")

    async def _cmd_scope(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_authorized(update):
            return

        current = await self._get_analysis_scope()
        if not context.args:
            lines = [
                "<b>Scope Analisa</b>\n",
                f"Aktif: <b>{current.upper()}</b>",
                "",
                "Pilihan:",
                "/scope all",
                "/scope depan",
                "/scope tengah",
                "/scope belakang",
            ]
            await update.message.reply_text("\n".join(lines), parse_mode="HTML")
            return

        requested = _normalize_scope(context.args[0])
        if context.args[0].strip().lower() not in ("all", *POSITIONS):
            await update.message.reply_text("Scope tidak valid. Gunakan: /scope all | depan | tengah | belakang")
            return

        await db.set_state("analysis_scope", requested)
        await update.message.reply_text(
            f"Scope analisa diubah ke <b>{requested.upper()}</b>. "
            "Prediksi dan bet berikutnya hanya memakai scope ini.",
            parse_mode="HTML",
        )

    async def _cmd_strategy(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_authorized(update):
            return

        current = await self._get_strategy_mode()
        base_threshold = float((await self._mm.get_status_summary())["threshold"])
        if not context.args:
            lines = [
                "<b>Metode Prediksi</b>\n",
                f"Aktif: <b>{_strategy_label(current)}</b>",
                "",
                "Pilihan:",
                "/strategy auto",
                "/strategy zigzag",
                "/strategy trend",
                "/strategy heuristic",
                "/strategy llm",
                "/strategy hybrid",
                "",
                "Threshold:",
                f"AUTO {get_strategy_threshold('auto', base_threshold):.0%} | "
                f"ZIGZAG {get_strategy_threshold('zigzag', base_threshold):.0%} | "
                f"TREND {get_strategy_threshold('trend', base_threshold):.0%}",
                f"HEURISTIC {get_strategy_threshold('heuristic', base_threshold):.0%} | "
                f"LLM {get_strategy_threshold('llm', base_threshold):.0%} | "
                f"HYBRID {get_strategy_threshold('hybrid', base_threshold):.0%}",
                "",
                "AUTO = bandingkan semua metode lalu pilih kandidat terbaik",
                "HYBRID = LLM + heuristic + feedback + adaptive",
            ]
            await update.message.reply_text("\n".join(lines), parse_mode="HTML")
            return

        raw = context.args[0].strip().lower()
        if raw not in ("auto", "zigzag", "trend", "heuristic", "llm", "hybrid"):
            await update.message.reply_text(
                "Strategy tidak valid. Gunakan: /strategy auto | zigzag | trend | heuristic | llm | hybrid"
            )
            return

        requested = _normalize_strategy_mode(raw)
        await db.set_state("strategy_mode", requested)
        await update.message.reply_text(
            f"Metode prediksi diubah ke <b>{_strategy_label(requested)}</b>. "
            f"Threshold aktif: <b>{get_strategy_threshold(requested, base_threshold):.0%}</b>. "
            "Perubahan berlaku untuk analisa dan bet berikutnya.",
            parse_mode="HTML",
        )

    async def _cmd_balance(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_authorized(update):
            return
        if not await self._auth.ensure_logged_in():
            await update.message.reply_text("Login gagal atau sesi expired.")
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
                f"Belum ada knowledge base aktif. Jalankan /kbbuild untuk menarik {KNOWLEDGE_BASE_HISTORY_LIMIT} history dan membangun knowledge base."
            )
            return
        scope = _extract_scope_from_source(kb["source"])
        knowledge = None
        try:
            knowledge = json.loads(kb.get("knowledge_json", "") or "{}")
        except json.JSONDecodeError:
            knowledge = None
        operational_lines = _format_kb_operational_lines(knowledge, scope)
        lines = [
            "<b>Knowledge Base Aktif</b>\n",
            f"Dataset : {kb['source_count']} hasil",
            f"Periode : {kb['period_from']} -> {kb['period_to']}",
            f"Model   : {kb['model']}",
            f"Sumber  : {kb['source']}",
            f"Scope   : {scope.upper()}",
            f"Dibuat  : {kb['created_at']}",
            "",
        ]
        if operational_lines:
            lines.extend(["<b>Ringkasan Operasional</b>", *operational_lines, ""])
        lines.append(kb["summary_text"])
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
        scope = await self._get_analysis_scope()
        await update.message.reply_text(
            f"Memulai build knowledge base dari {KNOWLEDGE_BASE_HISTORY_LIMIT} history "
            f"dengan scope <b>{scope.upper()}</b>. Proses ini manual dan bisa makan waktu.",
            parse_mode="HTML",
        )
        try:
            if not await self._auth.ensure_logged_in():
                await update.message.reply_text("Login gagal atau sesi expired.")
                return

            history = await self._scraper.get_draw_history(limit=KNOWLEDGE_BASE_HISTORY_LIMIT)
            if len(history) < 50:
                await update.message.reply_text("History yang berhasil diambil terlalu sedikit untuk build knowledge base.")
                return

            kb = await self._predictor.rebuild_knowledge_base(
                history,
                source=f"telegram_manual_scope_{scope}",
                scope=scope,
            )
            if kb is None:
                await update.message.reply_text("Build knowledge base gagal. Cek log untuk detail error LLM/parse.")
                return

            lines = [
                "<b>Knowledge Base Berhasil Dibangun</b>\n",
                f"Dataset : {kb['source_count']} hasil",
                f"Periode : {kb['period_from']} -> {kb['period_to']}",
                f"Model   : {kb['model']}",
                f"Scope   : {scope.upper()}",
                "",
            ]
            lines.extend(_format_kb_operational_lines(kb.get("knowledge"), scope))
            if lines[-1] != "":
                lines.append("")
            lines.append(kb["summary_text"])
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
            f"Scope    : {str(data.get('scope', 'all')).upper()}",
            f"Strategy : {_strategy_label(data.get('strategy_mode', 'auto'))}",
            f"Method   : {_strategy_label(data.get('selected_method', data.get('strategy_mode', 'auto')))}",
            f"Decision : {data.get('decision', '-')}",
            f"Selected : {format_slot(data.get('selected_slot', '-')) if data.get('selected_slot') else '-'} | "
            f"{_choice_label(data.get('selected_choice', '-'))} | "
            f"C{float(data.get('selected_confidence', 0.0)):.0%}/S{float(data.get('selected_score', data.get('selected_confidence', 0.0))):.0%}",
            f"Reason   : {str(data.get('selected_reason', '-') or '-')}",
            "",
        ]
        candidates = data.get("method_candidates") or []
        if candidates:
            lines.append("<b>Method Compare</b>")
            for item in candidates[:6]:
                lines.append(
                    f"{_strategy_label(item.get('method'))} | {format_slot(item.get('slot', '-'))} | "
                    f"{_choice_label(item.get('choice', '-'))} | "
                    f"C{float(item.get('confidence', 0.0)):.0%}/S{float(item.get('score', item.get('confidence', 0.0))):.0%}"
                )
            lines.append("")
        lines.append("<b>Ranking</b>")
        for item in ranking[:6]:
            lines.append(
                f"{format_slot(item['slot'])} | {_choice_label(item['choice'])} | "
                f"C{float(item['confidence']):.0%}/S{float(item.get('score', item['confidence'])):.0%} | {item.get('reason', '-')}"
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

        scope = await self._get_analysis_scope()
        strategy = await self._get_strategy_mode()
        prediction = await self._predictor.analyze(history, scope=scope, strategy_mode=strategy)
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
            f"Scope  : {scope.upper()}",
            f"Strategy: {_strategy_label(strategy)}",
            f"Method : {_strategy_label(prediction.get('selected_method', strategy))}",
            f"Selected: {format_slot(best['slot'])} | {_choice_label(best['choice'])} | "
            f"C{best['confidence']:.0%}/S{float(best.get('score', best['confidence'])):.0%}",
            "",
        ]
        candidates = prediction.get("method_candidates") or []
        if candidates:
            lines.append("<b>Method Compare</b>")
            for item in candidates[:6]:
                lines.append(
                    f"{_strategy_label(item.get('method'))} | {format_slot(item.get('slot', '-'))} | "
                    f"{_choice_label(item.get('choice', '-'))} | "
                    f"C{float(item.get('confidence', 0.0)):.0%}/S{float(item.get('score', item.get('confidence', 0.0))):.0%}"
                )
            lines.append("")
        lines.append("<b>Ranking</b>")
        for item in prediction["ranking"][:6]:
            lines.append(
                f"{format_slot(item['slot'])} | {_choice_label(item['choice'])} | "
                f"C{float(item['confidence']):.0%}/S{float(item.get('score', item['confidence'])):.0%}"
            )
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
        self._app.add_handler(CommandHandler("scope", self._cmd_scope))
        self._app.add_handler(CommandHandler("strategy", self._cmd_strategy))
        self._app.add_handler(CommandHandler("relearnstatus", self._cmd_relearnstatus))
        self._app.add_handler(CommandHandler("mode", self._cmd_mode))
        self._app.add_handler(CommandHandler("betnow", self._cmd_betnow))
        self._app.add_handler(CommandHandler("pause", self._cmd_pause))
        self._app.add_handler(CommandHandler("resume", self._cmd_resume))

        command_list = [
            BotCommand("status", "Ringkasan status bot"),
            BotCommand("scope", "Lihat atau ganti scope"),
            BotCommand("strategy", "Lihat atau ganti metode"),
            BotCommand("balance", "Cek saldo bot"),
            BotCommand("history", "10 bet terakhir + net"),
            BotCommand("results", "10 hasil draw terakhir"),
            BotCommand("stats", "Statistik settle hari ini"),
            BotCommand("profit", "Ringkasan profit bot"),
            BotCommand("level", "Martingale 6 slot"),
            BotCommand("signal", "Snapshot prediksi terakhir"),
            BotCommand("predict", "Analisis manual tanpa bet"),
            BotCommand("kb", "Lihat knowledge base aktif"),
            BotCommand("kbbuild", f"Build knowledge base {KNOWLEDGE_BASE_HISTORY_LIMIT} history"),
            BotCommand("relearnstatus", "Status auto relearn"),
            BotCommand("mode", "Lihat atau ganti mode"),
            BotCommand("betnow", "Bet sekarang untuk periode aktif"),
            BotCommand("pause", "Pause bot"),
            BotCommand("resume", "Resume bot"),
            BotCommand("help", "Daftar perintah"),
        ]
        await self._app.bot.set_my_commands(command_list)
        await self._app.bot.set_my_commands(
            command_list,
            scope=BotCommandScopeChat(chat_id=int(TELEGRAM_CHAT_ID)),
        )

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
