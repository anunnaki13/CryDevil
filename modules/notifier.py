"""Telegram notification module."""

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
            self._bot = Bot(token=TELEGRAM_BOT_TOKEN)
            self._chat_id = TELEGRAM_CHAT_ID
            self._enabled = True
        else:
            self._bot = None
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

    async def send_bet_placed(
        self,
        periode: str,
        position: str,
        bk_category: str,
        gj_category: str,
        bk_confidence: float,
        gj_confidence: float,
        bet_per_number: int,
        martingale_level: int,
        analysis: str = "",
        dry_run: bool = False,
    ) -> None:
        mode = "[DRY RUN] " if dry_run else ""
        total = bet_per_number * 50 * 2
        text = (
            f"{mode}🎯 <b>Bet Dipasang — Periode {periode}</b>\n"
            f"Posisi: <b>{position.upper()}</b>\n"
            f"  Besar/Kecil : <b>{bk_category.upper()}</b> "
            f"(confidence {bk_confidence:.0%}) × 50 nomor\n"
            f"  Genap/Ganjil: <b>{gj_category.upper()}</b> "
            f"(confidence {gj_confidence:.0%}) × 50 nomor\n"
            f"Taruhan: Rp{bet_per_number:,}/nomor | Total: Rp{total:,}\n"
            f"Martingale Level: {martingale_level}\n"
        )
        if analysis:
            text += f"<i>{analysis[:200]}</i>"
        await self._send(text)

    # ─── Result ──────────────────────────────────────────────────────────────

    async def send_result(
        self,
        periode: str,
        draw_result_4d: str,
        position: str,
        predicted_bk: str,
        predicted_gj: str,
        actual_bk: str,
        actual_gj: str,
        win_bk: bool,
        win_gj: bool,
        total_wagered: int,
        total_won: int,
        consecutive_losses: int,
        daily_loss: int,
    ) -> None:
        net = total_won - total_wagered
        bk_icon = "✅" if win_bk else "❌"
        gj_icon = "✅" if win_gj else "❌"

        if win_bk or win_gj:
            header = f"🏆 <b>MENANG — Periode {periode}</b>"
        else:
            header = f"❌ <b>KALAH — Periode {periode}</b>"

        net_str = f"+Rp{net:,}" if net >= 0 else f"-Rp{abs(net):,}"

        text = (
            f"{header}\n"
            f"Hasil: <b>{draw_result_4d}</b> | Posisi: {position.upper()}\n"
            f"{bk_icon} BK: prediksi <b>{predicted_bk.upper()}</b> → "
            f"aktual <b>{actual_bk.upper()}</b>\n"
            f"{gj_icon} GJ: prediksi <b>{predicted_gj.upper()}</b> → "
            f"aktual <b>{actual_gj.upper()}</b>\n"
            f"Net: {net_str} | Taruhan: Rp{total_wagered:,} | Menang: Rp{total_won:,}\n"
            f"Kalah berturut: {consecutive_losses} | Rugi hari ini: Rp{daily_loss:,}"
        )
        await self._send(text)

    # ─── Daily summary ───────────────────────────────────────────────────────

    async def send_daily_summary(
        self,
        date: str,
        total_bets: int,
        total_wagered: int,
        total_won: int,
        win_count: int,
        loss_count: int,
        final_balance: Optional[int] = None,
    ) -> None:
        net = total_won - total_wagered
        net_str = f"+Rp{net:,}" if net >= 0 else f"-Rp{abs(net):,}"
        win_rate = (win_count / total_bets * 100) if total_bets else 0
        balance_line = f"Balance: Rp{final_balance:,}\n" if final_balance else ""
        text = (
            f"📊 <b>Rekap Harian — {date}</b>\n"
            f"Periode: {total_bets} (W:{win_count} L:{loss_count} | {win_rate:.1f}%)\n"
            f"Taruhan: Rp{total_wagered:,}\n"
            f"Menang : Rp{total_won:,}\n"
            f"Net    : {net_str}\n"
            f"{balance_line}"
        )
        await self._send(text)

    # ─── Misc ────────────────────────────────────────────────────────────────

    async def send_alert(self, message: str) -> None:
        await self._send(f"⚠️ <b>Alert</b>\n{message}")

    async def send_info(self, message: str) -> None:
        await self._send(f"ℹ️ {message}")

    async def send_startup(self, dry_run: bool = False) -> None:
        mode = " [DRY RUN MODE]" if dry_run else ""
        await self._send(f"🤖 <b>Hokidraw Bot Aktif{mode}</b>\nMenunggu draw pertama...")

    async def send_shutdown(self) -> None:
        await self._send("🛑 <b>Hokidraw Bot Berhenti</b>")

    async def send_limit_reached(self, daily_loss: int, limit: int) -> None:
        await self._send(
            f"🚫 <b>Limit Rugi Harian Tercapai</b>\n"
            f"Rugi: Rp{daily_loss:,} / Limit: Rp{limit:,}\n"
            f"Bot berhenti bet sampai tengah malam WIB."
        )
