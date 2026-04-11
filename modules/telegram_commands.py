"""
Telegram command handler — perintah interaktif via chat Telegram.

Commands:
  /start, /help  — daftar perintah
  /status        — status bot (mode, level martingale, daily loss)
  /balance       — cek saldo akun
  /history       — 10 bet terakhir
  /results       — 10 hasil draw terakhir
  /stats         — statistik hari ini
  /profit        — profit hari ini & total
  /level         — level martingale BK & GJ saat ini
  /signal        — snapshot prediksi terakhir
  /pause         — pause bot (skip siklus)
  /resume        — resume bot
"""

import json
import logging
from datetime import datetime, timezone, timedelta
from typing import Awaitable, Callable, Optional

from telegram import Update, BotCommand
from telegram.ext import (
    Application, CommandHandler, ContextTypes,
)

from config import (
    TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, TELEGRAM_COMMANDS_ENABLED, DB_PATH,
    MARTINGALE_LEVELS, DAILY_LOSS_LIMIT, BASE_BET, BET_MODE,
    LLM_PRIMARY, INSTANCE_LABEL, BET_TARGET, FLEET_BOT_NAMES,
    TELEGRAM_PREDICT_COOLDOWN_SECONDS,
)
from modules import database as db
from modules import fleet
from modules.auth import AuthManager
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


def _snapshot_age(snapshot: dict) -> str:
    raw = snapshot.get("snapshot_at")
    if not raw:
        return "?"
    try:
        stamp = datetime.fromisoformat(raw)
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        seconds = max(0, int((datetime.now(timezone.utc) - stamp).total_seconds()))
    except ValueError:
        return "?"

    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    return f"{seconds // 3600}h"


class TelegramCommands:
    """Handles incoming Telegram commands alongside the existing notifier."""

    def __init__(
        self,
        auth: AuthManager,
        money_manager: MoneyManager,
        scraper: Optional[Scraper] = None,
        predictor: Optional[Predictor] = None,
        signal_snapshot_writer: Optional[Callable[..., Awaitable[None]]] = None,
    ) -> None:
        self._auth = auth
        self._mm = money_manager
        self._scraper = scraper
        self._predictor = predictor
        self._signal_snapshot_writer = signal_snapshot_writer
        self._app: Optional[Application] = None
        self._paused = False
        self._last_predict_at: Optional[datetime] = None

    @property
    def is_paused(self) -> bool:
        return self._paused

    def _is_authorized(self, update: Update) -> bool:
        """Only respond to the configured chat ID."""
        return str(update.effective_chat.id) == str(TELEGRAM_CHAT_ID)

    def _normalize_bot_name(self, raw: str) -> Optional[str]:
        bot_name = raw.strip()
        return bot_name if bot_name in FLEET_BOT_NAMES else None

    def _parse_scope_arg(self, context: ContextTypes.DEFAULT_TYPE) -> str | None:
        if not context.args:
            return None
        raw = context.args[0].strip().lower()
        if raw == "all":
            return "all"
        return self._normalize_bot_name(raw)

    # ─── /start & /help ──────────────────────────────────────────────────────

    async def _cmd_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_authorized(update):
            return
        text = (
            f"<b>{INSTANCE_LABEL} — Command List</b>\n\n"
            "/status   — Ringkasan status operasional\n"
            "/status X — Status bot tertentu / all\n"
            "/balance  — Cek saldo akun\n"
            "/history  — 10 bet terakhir + net\n"
            "/results  — 10 hasil draw terakhir\n"
            "/stats    — Statistik settle hari ini\n"
            "/profit   — Ringkasan profit bot\n"
            "/level    — Level martingale BK & GJ\n"
            "/signal X — Snapshot prediksi bot tertentu\n"
            "/predict  — Analisis manual tanpa pasang bet\n"
            "/bots     — Status fleet bot\n"
            "/bot_on X — Aktifkan bot tertentu\n"
            "/bot_off X — Matikan bot tertentu\n"
            "/pause X  — Pause bot tertentu / all\n"
            "/resume X — Resume bot tertentu / all\n"
            "/help     — Tampilkan menu ini"
        )
        await update.message.reply_text(text, parse_mode="HTML")

    # ─── /status ─────────────────────────────────────────────────────────────

    async def _cmd_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_authorized(update):
            return

        scope = self._parse_scope_arg(context)
        if scope == "all":
            await self._cmd_bots(update, context)
            return
        if scope and scope != fleet.INSTANCE_NAME:
            snapshot = fleet.get_snapshots().get(scope, {})
            if not snapshot:
                await update.message.reply_text(f"Belum ada snapshot untuk {scope}.")
                return
            text = (
                f"<b>Status {snapshot.get('instance_label', scope)}</b>\n\n"
                f"Bot       : {scope}\n"
                f"Enabled   : {'YA' if snapshot.get('enabled', True) else 'TIDAK'}\n"
                f"Paused    : {'YA' if snapshot.get('paused', False) else 'TIDAK'}\n"
                f"Target    : 2D {snapshot.get('target', '?')}\n"
                f"Mode      : {snapshot.get('mode', '?')}\n"
                f"Saldo     : {_idr(snapshot.get('balance'))}\n"
                f"Daily loss: {_idr(snapshot.get('daily_loss'))}\n"
                f"BK        : Lv {snapshot.get('bk_level', '?')} | {_idr(snapshot.get('bk_bet'))}/angka\n"
                f"GJ        : Lv {snapshot.get('gj_level', '?')} | {_idr(snapshot.get('gj_bet'))}/angka\n"
                f"Heartbeat : {_snapshot_age(snapshot)} lalu"
            )
            await update.message.reply_text(text, parse_mode="HTML")
            return

        summary = await self._mm.get_status_summary()
        balance = await self._auth.get_balance()
        last_period = await db.get_state("last_period", "-")
        now = _now_wib().strftime("%H:%M:%S WIB")

        pause_str = "PAUSED" if self._paused else "AKTIF"
        bal_str = f"Rp{balance:,}" if balance is not None else "?"

        text = (
            f"<b>Status Bot {INSTANCE_LABEL} — {now}</b>\n\n"
            f"Status    : {pause_str}\n"
            f"Saldo     : {bal_str}\n"
            f"Target    : 2D {BET_TARGET}\n"
            f"Mode Bet  : {BET_MODE}\n"
            f"Base Bet  : {_idr(BASE_BET)}/angka\n"
            f"LLM       : {LLM_PRIMARY}\n"
            f"Periode terakhir: {last_period}\n\n"
            f"<b>Martingale</b>\n"
            f"BK        : Lv {summary['bk_level']} | bet {_idr(summary['bk_bet'])}/angka | streak kalah {summary['bk_losses']}\n"
            f"GJ        : Lv {summary['gj_level']} | bet {_idr(summary['gj_bet'])}/angka | streak kalah {summary['gj_losses']}\n\n"
            f"<b>Limit Harian</b>\n"
            f"Rugi hari ini : {_idr(summary['daily_loss'])} / {_idr(DAILY_LOSS_LIMIT)}\n"
            f"Sisa limit    : {_idr(summary['limit_sisa'])}\n"
            f"Limit hit     : {'YA' if summary['limit_hit'] else 'BELUM'}"
        )
        await update.message.reply_text(text, parse_mode="HTML")

    # ─── /balance ────────────────────────────────────────────────────────────

    async def _cmd_balance(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_authorized(update):
            return

        balance = await self._auth.get_balance()
        if balance is not None:
            text = f"Saldo saat ini: <b>{_idr(balance)}</b>"
        else:
            text = "Gagal mengambil saldo. Mungkin sesi expired."
        await update.message.reply_text(text, parse_mode="HTML")

    # ─── /history ────────────────────────────────────────────────────────────

    async def _cmd_history(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_authorized(update):
            return

        bets = await self._get_recent_bets(10)
        if not bets:
            await update.message.reply_text("Belum ada riwayat bet.")
            return

        lines = ["<b>10 Bet Terakhir</b>\n"]
        choice_labels = {"BE": "BESAR", "KE": "KECIL", "GE": "GENAP", "GA": "GANJIL"}

        for b in bets:
            status = b["status"]
            icon = {"won": "WIN", "lost": "LOSS", "placed": "OPEN"}.get(status, "?")
            dim_short = "BK" if b["bet_dimension"] == "besar_kecil" else "GJ"
            choice = choice_labels.get(b["bet_choice"], b["bet_choice"])
            amount = int(b["bet_amount_per_angka"])
            stake = amount * 50
            net = ""
            if status == "won":
                net = _net(int(b["win_amount"]) - stake)
            elif status == "lost":
                net = _net(-stake)
            else:
                net = f"modal {_idr(stake)}"

            lines.append(
                f"{icon} | P{b['period']} | {dim_short} {choice} | "
                f"Lv{b['martingale_level']} | conf {float(b['confidence']):.0%} | {net}"
            )

        await update.message.reply_text("\n".join(lines), parse_mode="HTML")

    # ─── /results ────────────────────────────────────────────────────────────

    async def _cmd_results(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_authorized(update):
            return

        results = await db.get_recent_results(10)
        if not results:
            await update.message.reply_text("Belum ada data hasil draw di database.")
            return

        lines = ["<b>10 Hasil Draw Terakhir</b>\n"]
        for r in results:
            lines.append(
                f"P{r['period']} | {r['full_number']} | "
                f"2D {r['target_position']}={r['target_number_2d']} | "
                f"BK {r['target_bk']} | GJ {r['target_gj']}"
            )

        await update.message.reply_text("\n".join(lines), parse_mode="HTML")

    # ─── /stats ──────────────────────────────────────────────────────────────

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
        losses = total - wins
        wr = (wins / total * 100) if total else 0
        profit = int(stats["profit"])

        text = (
            f"<b>Statistik Hari Ini — {today}</b>\n\n"
            f"Bet settle  : {total}\n"
            f"Menang      : {wins}\n"
            f"Kalah       : {losses}\n"
            f"Win rate    : {wr:.1f}%\n"
            f"Modal       : {_idr(stats['total_bet_amount'])}\n"
            f"Payout      : {_idr(stats['total_win_amount'])}\n"
            f"Net         : <b>{_net(profit)}</b>"
        )
        await update.message.reply_text(text, parse_mode="HTML")

    # ─── /profit ─────────────────────────────────────────────────────────────

    async def _cmd_profit(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_authorized(update):
            return

        today = _today_wib()
        today_stats = await db.get_daily_stats(today)

        today_profit = int(today_stats["profit"]) if today_stats else 0
        aggregate = await db.get_aggregate_daily_stats()
        total_days = int(aggregate["total_days"])
        total_periods = await db.count_distinct_bet_periods()
        balance = await self._auth.get_balance()
        bal_str = _idr(balance) if balance is not None else "?"

        text = (
            f"<b>Profit Report</b>\n\n"
            f"Hari ini ({today})\n"
            f"Modal : {_idr(today_stats['total_bet_amount']) if today_stats else _idr(0)}\n"
            f"Payout: {_idr(today_stats['total_win_amount']) if today_stats else _idr(0)}\n"
            f"Net   : <b>{_net(today_profit)}</b>\n\n"
            f"Akumulasi\n"
            f"Hari tercatat : {total_days}\n"
            f"Periode bet   : {total_periods}\n"
            f"Modal total   : {_idr(aggregate['total_bet_amount'])}\n"
            f"Payout total  : {_idr(aggregate['total_win_amount'])}\n"
            f"Net total     : <b>{_net(aggregate['profit'])}</b>\n"
            f"Saldo saat ini: {bal_str}"
        )
        await update.message.reply_text(text, parse_mode="HTML")

    # ─── /level ──────────────────────────────────────────────────────────────

    async def _cmd_level(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_authorized(update):
            return

        summary = await self._mm.get_status_summary()
        levels_str = " → ".join(f"Rp{x:,}" for x in MARTINGALE_LEVELS)

        bk_lv = summary["bk_level"]
        gj_lv = summary["gj_level"]

        text = (
            f"<b>Martingale Level</b>\n\n"
            f"Daftar level: {levels_str}\n\n"
            f"<b>Besar/Kecil</b>\n"
            f"Level     : {bk_lv} / {len(MARTINGALE_LEVELS)-1}\n"
            f"Bet/angka : {_idr(summary['bk_bet'])}\n"
            f"Modal/bet : {_idr(summary['bk_bet']*50)}\n"
            f"Kalah berturut: {summary['bk_losses']}\n\n"
            f"<b>Genap/Ganjil</b>\n"
            f"Level     : {gj_lv} / {len(MARTINGALE_LEVELS)-1}\n"
            f"Bet/angka : {_idr(summary['gj_bet'])}\n"
            f"Modal/bet : {_idr(summary['gj_bet']*50)}\n"
            f"Kalah berturut: {summary['gj_losses']}"
        )
        await update.message.reply_text(text, parse_mode="HTML")

    async def _cmd_signal(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_authorized(update):
            return

        scope = self._parse_scope_arg(context)
        if scope and scope != fleet.INSTANCE_NAME:
            data = (fleet.get_snapshots().get(scope, {}) or {}).get("last_signal_snapshot")
            if not data:
                await update.message.reply_text(f"Belum ada snapshot prediksi untuk {scope}.")
                return
        else:
            raw = await db.get_state("last_signal_snapshot")
            if not raw:
                await update.message.reply_text("Belum ada snapshot prediksi.")
                return
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                await update.message.reply_text("Snapshot prediksi rusak atau tidak bisa dibaca.")
                return

        bk = data.get("besar_kecil", {})
        gj = data.get("genap_ganjil", {})
        selected_dim = data.get("selected_dimension") or "-"
        selected_choice = data.get("selected_choice") or "-"
        selected_conf = data.get("selected_confidence")
        selected_conf_str = f"{float(selected_conf):.0%}" if selected_conf is not None else "-"

        text = (
            f"<b>Signal Snapshot</b>\n\n"
            f"Periode   : {data.get('period', '-')}\n"
            f"Target    : 2D {data.get('target', BET_TARGET)}\n"
            f"Source    : {data.get('source', '-')}\n"
            f"Decision  : {data.get('decision', '-')}\n"
            f"Threshold : {float(data.get('threshold', 0.6)):.0%}\n"
            f"Selected  : {selected_dim} | {selected_choice} | {selected_conf_str}\n\n"
            f"BK        : {bk.get('choice', '-')} | {float(bk.get('confidence', 0.5)):.0%}\n"
            f"Reason BK : {bk.get('reason', '-')}\n\n"
            f"GJ        : {gj.get('choice', '-')} | {float(gj.get('confidence', 0.5)):.0%}\n"
            f"Reason GJ : {gj.get('reason', '-')}\n\n"
            f"Note      : {data.get('note', '-')}"
        )
        await update.message.reply_text(text, parse_mode="HTML")

    async def _cmd_predict(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_authorized(update):
            return

        if self._scraper is None or self._predictor is None or self._signal_snapshot_writer is None:
            await update.message.reply_text("Predictor manual belum terhubung di instance ini.")
            return

        now = datetime.now(timezone.utc)
        if self._last_predict_at is not None:
            elapsed = (now - self._last_predict_at).total_seconds()
            if elapsed < TELEGRAM_PREDICT_COOLDOWN_SECONDS:
                wait_seconds = int(TELEGRAM_PREDICT_COOLDOWN_SECONDS - elapsed)
                await update.message.reply_text(
                    f"/predict masih cooldown. Coba lagi dalam {wait_seconds} detik."
                )
                return
        self._last_predict_at = now

        await update.message.reply_text("Menjalankan analisis manual. Tidak ada bet yang akan dipasang.")

        if not await self._auth.ensure_logged_in():
            self._last_predict_at = None
            await update.message.reply_text("Login gagal atau sesi expired. Coba lagi setelah sesi pulih.")
            return

        history = await self._scraper.get_draw_history()
        if not history:
            self._last_predict_at = None
            await update.message.reply_text("Gagal ambil history draw.")
            return

        periode = await self._scraper.get_current_periode()
        if not periode:
            self._last_predict_at = None
            await update.message.reply_text("Gagal ambil periode saat ini.")
            return

        prediction = await self._predictor.analyze(history)
        if prediction is None:
            self._last_predict_at = None
            await update.message.reply_text("Prediksi gagal.")
            return

        bk = prediction["besar_kecil"]
        gj = prediction["genap_ganjil"]
        selected_dim = "besar_kecil" if bk["confidence"] >= gj["confidence"] else "genap_ganjil"
        selected = bk if selected_dim == "besar_kecil" else gj

        await self._signal_snapshot_writer(
            periode,
            bk,
            gj,
            source="manual",
            decision="ANALYZED",
            selected_dimension=selected_dim,
            selected_choice=selected["choice"],
            selected_confidence=selected["confidence"],
            note="manual_predict",
        )

        text = (
            f"<b>Manual Predict</b>\n\n"
            f"Periode   : {periode}\n"
            f"Target    : 2D {BET_TARGET}\n"
            f"BK        : {bk['choice']} | {float(bk['confidence']):.0%}\n"
            f"Reason BK : {bk.get('reason', '-')}\n\n"
            f"GJ        : {gj['choice']} | {float(gj['confidence']):.0%}\n"
            f"Reason GJ : {gj.get('reason', '-')}\n\n"
            f"Selected  : {selected_dim} | {selected['choice']} | {float(selected['confidence']):.0%}\n"
            f"Snapshot  : tersimpan, cek /signal"
        )
        await update.message.reply_text(text, parse_mode="HTML")

    # ─── /pause & /resume ────────────────────────────────────────────────────

    async def _cmd_pause(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_authorized(update):
            return

        scope = self._parse_scope_arg(context)
        if scope == "all":
            for bot_name in FLEET_BOT_NAMES:
                fleet.set_bot_paused(bot_name, True)
            self._paused = True
            await db.set_state("bot_paused", "1")
            await update.message.reply_text("Semua bot di-PAUSE.")
            return

        if scope and scope != fleet.INSTANCE_NAME:
            fleet.set_bot_paused(scope, True)
            await update.message.reply_text(f"{scope} di-PAUSE.")
            return

        self._paused = True
        fleet.set_bot_paused(fleet.INSTANCE_NAME, True)
        await db.set_state("bot_paused", "1")
        await update.message.reply_text("Bot di-PAUSE. Siklus berikutnya akan di-skip.\nKetik /resume untuk lanjutkan.")

    async def _cmd_resume(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_authorized(update):
            return

        scope = self._parse_scope_arg(context)
        if scope == "all":
            for bot_name in FLEET_BOT_NAMES:
                fleet.set_bot_paused(bot_name, False)
            self._paused = False
            await db.set_state("bot_paused", "0")
            await update.message.reply_text("Semua bot di-RESUME.")
            return

        if scope and scope != fleet.INSTANCE_NAME:
            fleet.set_bot_paused(scope, False)
            await update.message.reply_text(f"{scope} di-RESUME.")
            return

        self._paused = False
        fleet.set_bot_paused(fleet.INSTANCE_NAME, False)
        await db.set_state("bot_paused", "0")
        await update.message.reply_text("Bot RESUMED. Siklus berikutnya akan berjalan normal.")

    async def _cmd_bots(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_authorized(update):
            return
        bots = fleet.get_snapshots()
        lines = ["<b>Status Fleet Bot</b>\n"]
        for bot_name in FLEET_BOT_NAMES:
            snapshot = bots.get(bot_name, {})
            lines.append(
                f"{bot_name} | {'ON' if snapshot.get('enabled', True) else 'OFF'} | "
                f"{'PAUSED' if snapshot.get('paused', False) else 'RUN'} | "
                f"target={snapshot.get('target', '?')} | balance={_idr(snapshot.get('balance'))} | "
                f"daily_loss={_idr(snapshot.get('daily_loss', 0))} | age={_snapshot_age(snapshot)}"
            )
        await update.message.reply_text("\n".join(lines), parse_mode="HTML")

    async def _cmd_bot_on(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_authorized(update):
            return
        if not context.args:
            await update.message.reply_text("Gunakan: /bot_on bot-2")
            return
        bot_name = self._normalize_bot_name(context.args[0])
        if not bot_name:
            await update.message.reply_text(
                f"Nama bot tidak valid. Pilihan: {', '.join(FLEET_BOT_NAMES)}"
            )
            return
        fleet.set_bot_enabled(bot_name, True)
        await update.message.reply_text(f"{bot_name} diaktifkan.")

    async def _cmd_bot_off(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_authorized(update):
            return
        if not context.args:
            await update.message.reply_text("Gunakan: /bot_off bot-2")
            return
        bot_name = self._normalize_bot_name(context.args[0])
        if not bot_name:
            await update.message.reply_text(
                f"Nama bot tidak valid. Pilihan: {', '.join(FLEET_BOT_NAMES)}"
            )
            return
        fleet.set_bot_enabled(bot_name, False)
        await update.message.reply_text(f"{bot_name} dimatikan.")

    # ─── DB helpers ──────────────────────────────────────────────────────────

    async def _get_recent_bets(self, limit: int = 10) -> list[dict]:
        async with __import__("aiosqlite").connect(DB_PATH) as conn:
            conn.row_factory = __import__("aiosqlite").Row
            async with conn.execute(
                "SELECT * FROM bets ORDER BY id DESC LIMIT ?", (limit,)
            ) as cur:
                return [dict(r) for r in await cur.fetchall()]

    # ─── Setup & start ───────────────────────────────────────────────────────

    async def start(self) -> None:
        """Start the Telegram command listener (polling)."""
        if not TELEGRAM_COMMANDS_ENABLED:
            logger.info("Telegram commands disabled for instance %s", INSTANCE_LABEL)
            return

        if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
            logger.warning("Telegram commands disabled — token/chat_id not set")
            return

        self._app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

        # Register commands
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
        self._app.add_handler(CommandHandler("bots", self._cmd_bots))
        self._app.add_handler(CommandHandler("bot_on", self._cmd_bot_on))
        self._app.add_handler(CommandHandler("bot_off", self._cmd_bot_off))
        self._app.add_handler(CommandHandler("pause", self._cmd_pause))
        self._app.add_handler(CommandHandler("resume", self._cmd_resume))

        # Set bot command menu in Telegram
        await self._app.bot.set_my_commands([
            BotCommand("status", "Ringkasan status bot"),
            BotCommand("balance", "Cek saldo akun"),
            BotCommand("history", "10 bet terakhir + net"),
            BotCommand("results", "10 hasil draw terakhir"),
            BotCommand("stats", "Statistik settle hari ini"),
            BotCommand("profit", "Ringkasan profit bot"),
            BotCommand("level", "Level martingale BK & GJ"),
            BotCommand("signal", "Snapshot prediksi terakhir"),
            BotCommand("predict", "Analisis manual tanpa bet"),
            BotCommand("bots", "Status fleet bot"),
            BotCommand("bot_on", "Aktifkan bot tertentu"),
            BotCommand("bot_off", "Matikan bot tertentu"),
            BotCommand("pause", "Pause bot"),
            BotCommand("resume", "Resume bot"),
            BotCommand("help", "Daftar perintah"),
        ])

        # Restore pause state
        paused = await db.get_state("bot_paused", "0")
        self._paused = paused == "1" or fleet.is_bot_paused(fleet.INSTANCE_NAME)

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
