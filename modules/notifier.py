"""Telegram notification module — format pesan sesuai blueprint."""

import logging
from typing import Optional

from telegram import Bot
from telegram.constants import ParseMode
from telegram.error import TelegramError

from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

logger = logging.getLogger(__name__)


class TelegramNotifier:
    def __init__(self) -> None:
        if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
            self._bot     = Bot(token=TELEGRAM_BOT_TOKEN)
            self._chat_id = TELEGRAM_CHAT_ID
            self._enabled = True
        else:
            self._bot     = None
            self._chat_id = None
            self._enabled = False
            logger.warning("Telegram tidak dikonfigurasi — notifikasi dinonaktifkan")

    async def _send(self, text: str) -> None:
        if not self._enabled:
            logger.info("[Telegram] %s", text)
            return
        try:
            await self._bot.send_message(
                chat_id=self._chat_id,
                text=text,
                parse_mode=ParseMode.HTML,
            )
        except TelegramError as e:
            logger.error("Telegram gagal kirim: %s", e)

    # ─── Bet placed ──────────────────────────────────────────────────────────

    async def notify_bet_placed(
        self,
        periode: str,
        bk_choice: str,        # "BE" | "KE"
        gj_choice: str,        # "GE" | "GA"
        bk_confidence: float,
        gj_confidence: float,
        bet_amount: int,       # per angka (IDR)
        bk_level: int,
        gj_level: int,
        dry_run: bool = False,
    ) -> None:
        mode       = "[DRY RUN] " if dry_run else ""
        total      = bet_amount * 50 * 2
        bk_labels  = {"BE": "BESAR", "KE": "KECIL"}
        gj_labels  = {"GE": "GENAP", "GA": "GANJIL"}

        text = (
            f"{mode}🎯 BET Periode {periode}\n"
            f"Posisi: 2D Belakang\n"
            f"Besar/Kecil : <b>{bk_labels[bk_choice]}</b> (confidence: {bk_confidence:.0%}) — Level {bk_level}\n"
            f"Genap/Ganjil: <b>{gj_labels[gj_choice]}</b> (confidence: {gj_confidence:.0%}) — Level {gj_level}\n"
            f"Bet: Rp{bet_amount:,}/angka × 50 = Rp{bet_amount*50:,} per taruhan\n"
            f"Total: Rp{total:,} (2 taruhan)"
        )
        await self._send(text)

    # ─── Result ──────────────────────────────────────────────────────────────

    async def notify_result(
        self,
        periode: str,
        full_result: str,      # "1295"
        result_2d: str,        # "95"
        actual_bk: str,        # "BE" | "KE"
        actual_gj: str,        # "GE" | "GA"
        bet_bk: Optional[str], # pilihan yang dipasang, None jika tidak bet
        bet_gj: Optional[str],
        win_bk: bool,
        win_gj: bool,
        profit_bk: int,        # net per bet (bisa negatif)
        profit_gj: int,
        balance: Optional[int] = None,
    ) -> None:
        bk_labels = {"BE": "BESAR", "KE": "KECIL"}
        gj_labels = {"GE": "GENAP", "GA": "GANJIL"}

        bk_icon = "✅" if win_bk else "❌"
        gj_icon = "✅" if win_gj else "❌"

        total_profit = profit_bk + profit_gj
        profit_str   = f"+Rp{total_profit:,}" if total_profit >= 0 else f"-Rp{abs(total_profit):,}"

        bk_line = (
            f"{bk_icon} Besar/Kecil: {bk_labels.get(actual_bk, actual_bk)}"
            + (f" (bet: {bk_labels.get(bet_bk, bet_bk)})" if bet_bk else "")
        )
        gj_line = (
            f"{gj_icon} Genap/Ganjil: {gj_labels.get(actual_gj, actual_gj)}"
            + (f" (bet: {gj_labels.get(bet_gj, bet_gj)})" if bet_gj else "")
        )

        balance_line = f" | Saldo: Rp{balance:,}" if balance else ""

        text = (
            f"📊 HASIL Periode {periode}: <b>{full_result}</b> (2D={result_2d})\n"
            f"→ {bk_line}\n"
            f"→ {gj_line}\n"
            f"Profit: {profit_str}{balance_line}"
        )
        await self._send(text)

    # ─── Daily summary ───────────────────────────────────────────────────────

    async def notify_daily_summary(
        self,
        date: str,
        total_bets: int,
        total_wins: int,
        total_bet_amount: int,
        total_win_amount: int,
        profit: int,
        ending_balance: Optional[int] = None,
    ) -> None:
        win_rate   = (total_wins / total_bets * 100) if total_bets else 0
        profit_str = f"+Rp{profit:,}" if profit >= 0 else f"-Rp{abs(profit):,}"
        bal_line   = f" | Saldo: Rp{ending_balance:,}" if ending_balance else ""

        text = (
            f"📈 Ringkasan Hari Ini — {date}\n"
            f"Periode: {total_bets} bet | Win: {total_wins}/{total_bets} ({win_rate:.1f}%)\n"
            f"Total Bet: Rp{total_bet_amount:,} | Total Win: Rp{total_win_amount:,}\n"
            f"Profit: {profit_str}{bal_line}"
        )
        await self._send(text)

    # ─── Misc ────────────────────────────────────────────────────────────────

    async def notify_alert(self, message: str) -> None:
        await self._send(f"⚠️ <b>Alert</b>\n{message}")

    async def send_startup(self, dry_run: bool = False) -> None:
        mode = " [DRY RUN MODE]" if dry_run else ""
        await self._send(f"🤖 <b>Hokidraw Bot Aktif{mode}</b>\nMenunggu draw pertama...")

    async def send_shutdown(self) -> None:
        await self._send("🛑 <b>Hokidraw Bot Berhenti</b>")

    async def send_limit_reached(self, daily_loss: int, limit: int) -> None:
        await self._send(
            f"🚫 <b>Limit Rugi Harian Tercapai</b>\n"
            f"Rugi: Rp{daily_loss:,} / Limit: Rp{limit:,}\n"
            f"Bot berhenti sampai tengah malam WIB."
        )
