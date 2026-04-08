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
  /predict       — trigger prediksi LLM (tanpa bet)
  /pause         — pause bot (skip siklus)
  /resume        — resume bot
"""

import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

from telegram import Update, BotCommand
from telegram.ext import (
    Application, CommandHandler, ContextTypes,
)

from config import (
    TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID,
    MARTINGALE_LEVELS, DAILY_LOSS_LIMIT, BASE_BET, BET_MODE,
    BASE_URL, POOL_ID, LLM_PRIMARY,
)
from modules import database as db
from modules.auth import AuthManager
from modules.money_manager import MoneyManager

logger = logging.getLogger(__name__)

_WIB = timezone(timedelta(hours=7))


def _now_wib() -> datetime:
    return datetime.now(_WIB)


def _today_wib() -> str:
    return _now_wib().strftime("%Y-%m-%d")


class TelegramCommands:
    """Handles incoming Telegram commands alongside the existing notifier."""

    def __init__(self, auth: AuthManager, money_manager: MoneyManager) -> None:
        self._auth = auth
        self._mm = money_manager
        self._app: Optional[Application] = None
        self._paused = False

    @property
    def is_paused(self) -> bool:
        return self._paused

    def _is_authorized(self, update: Update) -> bool:
        """Only respond to the configured chat ID."""
        return str(update.effective_chat.id) == str(TELEGRAM_CHAT_ID)

    # ─── /start & /help ──────────────────────────────────────────────────────

    async def _cmd_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_authorized(update):
            return
        text = (
            "<b>Hokidraw Bot — Command List</b>\n\n"
            "/status   — Status bot & konfigurasi\n"
            "/balance  — Cek saldo akun\n"
            "/history  — 10 bet terakhir\n"
            "/results  — 10 hasil draw terakhir\n"
            "/stats    — Statistik hari ini\n"
            "/profit   — Profit hari ini & keseluruhan\n"
            "/level    — Level martingale BK & GJ\n"
            "/pause    — Pause bot (skip siklus)\n"
            "/resume   — Resume bot\n"
            "/help     — Tampilkan menu ini"
        )
        await update.message.reply_text(text, parse_mode="HTML")

    # ─── /status ─────────────────────────────────────────────────────────────

    async def _cmd_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_authorized(update):
            return

        summary = await self._mm.get_status_summary()
        balance = await self._auth.get_balance()
        last_period = await db.get_state("last_period", "-")
        now = _now_wib().strftime("%H:%M:%S WIB")

        pause_str = "PAUSED" if self._paused else "AKTIF"
        bal_str = f"Rp{balance:,}" if balance else "?"

        text = (
            f"<b>Status Bot — {now}</b>\n\n"
            f"Mode      : {pause_str}\n"
            f"Balance   : {bal_str}\n"
            f"Bet Mode  : {BET_MODE}\n"
            f"Base Bet  : Rp{BASE_BET:,}/angka\n"
            f"LLM       : {LLM_PRIMARY}\n"
            f"Last Bet  : Periode {last_period}\n\n"
            f"<b>Martingale</b>\n"
            f"BK Level  : {summary['bk_level']} (Rp{summary['bk_bet']:,}/angka) | Streak kalah: {summary['bk_losses']}\n"
            f"GJ Level  : {summary['gj_level']} (Rp{summary['gj_bet']:,}/angka) | Streak kalah: {summary['gj_losses']}\n\n"
            f"<b>Daily Limit</b>\n"
            f"Rugi hari ini : Rp{summary['daily_loss']:,} / Rp{DAILY_LOSS_LIMIT:,}\n"
            f"Sisa limit    : Rp{summary['limit_sisa']:,}\n"
            f"Limit tercapai: {'YA' if summary['limit_hit'] else 'Belum'}"
        )
        await update.message.reply_text(text, parse_mode="HTML")

    # ─── /balance ────────────────────────────────────────────────────────────

    async def _cmd_balance(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_authorized(update):
            return

        balance = await self._auth.get_balance()
        if balance is not None:
            text = f"Saldo saat ini: <b>Rp{balance:,}</b>"
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
            icon = {"won": "+", "lost": "-", "placed": "~"}.get(status, "?")
            dim_short = "BK" if b["bet_dimension"] == "besar_kecil" else "GJ"
            choice = choice_labels.get(b["bet_choice"], b["bet_choice"])
            amount = int(b["bet_amount_per_angka"])
            net = ""
            if status == "won":
                net = f" +Rp{int(b['win_amount']):,}"
            elif status == "lost":
                net = f" -Rp{amount * 50:,}"

            lines.append(
                f"[{icon}] P{b['period']} | {dim_short} {choice} | "
                f"Rp{amount:,}/angka | Lv{b['martingale_level']}{net}"
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

        bk_labels = {"BE": "BE", "KE": "KE"}
        gj_labels = {"GE": "GE", "GA": "GA"}

        lines = ["<b>10 Hasil Draw Terakhir</b>\n"]
        for r in results:
            lines.append(
                f"P{r['period']} | {r['full_number']} | "
                f"2D={r['number_2d_belakang']} "
                f"({r['belakang_bk']}/{r['belakang_gj']})"
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
        profit_str = f"+Rp{profit:,}" if profit >= 0 else f"-Rp{abs(profit):,}"

        text = (
            f"<b>Statistik Hari Ini — {today}</b>\n\n"
            f"Total Bet   : {total}\n"
            f"Menang      : {wins}\n"
            f"Kalah       : {losses}\n"
            f"Win Rate    : {wr:.1f}%\n"
            f"Total Taruh : Rp{int(stats['total_bet_amount']):,}\n"
            f"Total Menang: Rp{int(stats['total_win_amount']):,}\n"
            f"Profit      : {profit_str}"
        )
        await update.message.reply_text(text, parse_mode="HTML")

    # ─── /profit ─────────────────────────────────────────────────────────────

    async def _cmd_profit(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_authorized(update):
            return

        today = _today_wib()
        today_stats = await db.get_daily_stats(today)

        # Profit hari ini
        today_profit = int(today_stats["profit"]) if today_stats else 0
        today_str = f"+Rp{today_profit:,}" if today_profit >= 0 else f"-Rp{abs(today_profit):,}"

        # Profit keseluruhan (semua hari)
        all_stats = await self._get_all_daily_stats()
        total_profit = sum(int(s["profit"]) for s in all_stats)
        total_str = f"+Rp{total_profit:,}" if total_profit >= 0 else f"-Rp{abs(total_profit):,}"
        total_days = len(all_stats)

        # Balance
        balance = await self._auth.get_balance()
        bal_str = f"Rp{balance:,}" if balance else "?"

        text = (
            f"<b>Profit Report</b>\n\n"
            f"Hari ini ({today}): <b>{today_str}</b>\n"
            f"Total ({total_days} hari)   : <b>{total_str}</b>\n"
            f"Balance saat ini : {bal_str}"
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
            f"  Level     : {bk_lv} / {len(MARTINGALE_LEVELS)-1}\n"
            f"  Bet/angka : Rp{summary['bk_bet']:,}\n"
            f"  Total/bet : Rp{summary['bk_bet']*50:,}\n"
            f"  Kalah berturut: {summary['bk_losses']}\n\n"
            f"<b>Genap/Ganjil</b>\n"
            f"  Level     : {gj_lv} / {len(MARTINGALE_LEVELS)-1}\n"
            f"  Bet/angka : Rp{summary['gj_bet']:,}\n"
            f"  Total/bet : Rp{summary['gj_bet']*50:,}\n"
            f"  Kalah berturut: {summary['gj_losses']}"
        )
        await update.message.reply_text(text, parse_mode="HTML")

    # ─── /pause & /resume ────────────────────────────────────────────────────

    async def _cmd_pause(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_authorized(update):
            return
        self._paused = True
        await db.set_state("bot_paused", "1")
        await update.message.reply_text("Bot di-PAUSE. Siklus berikutnya akan di-skip.\nKetik /resume untuk lanjutkan.")

    async def _cmd_resume(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_authorized(update):
            return
        self._paused = False
        await db.set_state("bot_paused", "0")
        await update.message.reply_text("Bot RESUMED. Siklus berikutnya akan berjalan normal.")

    # ─── DB helpers ──────────────────────────────────────────────────────────

    async def _get_recent_bets(self, limit: int = 10) -> list[dict]:
        async with __import__("aiosqlite").connect("data/hokidraw.db") as conn:
            conn.row_factory = __import__("aiosqlite").Row
            async with conn.execute(
                "SELECT * FROM bets ORDER BY id DESC LIMIT ?", (limit,)
            ) as cur:
                return [dict(r) for r in await cur.fetchall()]

    async def _get_all_daily_stats(self) -> list[dict]:
        async with __import__("aiosqlite").connect("data/hokidraw.db") as conn:
            conn.row_factory = __import__("aiosqlite").Row
            async with conn.execute(
                "SELECT * FROM daily_stats ORDER BY date DESC"
            ) as cur:
                return [dict(r) for r in await cur.fetchall()]

    # ─── Setup & start ───────────────────────────────────────────────────────

    async def start(self) -> None:
        """Start the Telegram command listener (polling)."""
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
        self._app.add_handler(CommandHandler("pause", self._cmd_pause))
        self._app.add_handler(CommandHandler("resume", self._cmd_resume))

        # Set bot command menu in Telegram
        await self._app.bot.set_my_commands([
            BotCommand("status", "Status bot & konfigurasi"),
            BotCommand("balance", "Cek saldo akun"),
            BotCommand("history", "10 bet terakhir"),
            BotCommand("results", "10 hasil draw terakhir"),
            BotCommand("stats", "Statistik hari ini"),
            BotCommand("profit", "Profit hari ini & total"),
            BotCommand("level", "Level martingale BK & GJ"),
            BotCommand("pause", "Pause bot"),
            BotCommand("resume", "Resume bot"),
            BotCommand("help", "Daftar perintah"),
        ])

        # Restore pause state
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
