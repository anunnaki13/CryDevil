"""Telegram notification module for single-bot multi-position mode."""

import logging
from typing import Optional

from telegram import Bot
from telegram.constants import ParseMode
from telegram.error import TelegramError

from config import INSTANCE_LABEL, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, TELEGRAM_MESSAGE_THREAD_ID
from modules.categories import CHOICE_LABELS, DIMENSION_LABELS, POSITION_LABELS, format_slot

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
            payload = {"chat_id": self._chat_id, "text": text, "parse_mode": ParseMode.HTML}
            if TELEGRAM_MESSAGE_THREAD_ID is not None:
                payload["message_thread_id"] = TELEGRAM_MESSAGE_THREAD_ID
            await self._bot.send_message(**payload)
        except TelegramError as exc:
            logger.error("Telegram gagal kirim: %s", exc)

    def _title(self, text: str) -> str:
        return f"[{INSTANCE_LABEL}] {text}"

    @staticmethod
    def _idr(amount: int | float | None) -> str:
        if amount is None:
            return "?"
        return f"Rp{int(amount):,}"

    @staticmethod
    def _net(amount: int | float) -> str:
        value = int(amount)
        return f"+Rp{value:,}" if value > 0 else (f"-Rp{abs(value):,}" if value < 0 else "Rp0")

    async def notify_bet_placed(
        self,
        periode: str,
        target_position: str,
        dimension: str,
        choice: str,
        confidence: float,
        score: float,
        selected_reason: str,
        strategy_mode: str,
        selected_method: str,
        threshold: float,
        amount: int,
        level: int,
        ranking: list[dict],
        balance: Optional[int] = None,
        dry_run: bool = False,
    ) -> None:
        mode = "[DRY RUN] " if dry_run else ""
        top_lines = []
        for item in ranking[:6]:
            item_score = float(item.get("score", item["confidence"]))
            top_lines.append(
                f"{format_slot(item['slot'])}={CHOICE_LABELS.get(item['choice'], item['choice'])} "
                f"C{float(item['confidence']):.0%}/S{item_score:.0%}"
            )
        reason_short = (selected_reason or "-").replace(" | ", " / ")
        if len(reason_short) > 180:
            reason_short = reason_short[:177] + "..."
        text = (
            f"{mode}🎯 {self._title(f'BET Periode {periode}')}\n"
            f"Pilihan: <b>{POSITION_LABELS.get(target_position, target_position)} "
            f"{DIMENSION_LABELS.get(dimension, dimension)} {CHOICE_LABELS.get(choice, choice)}</b>\n"
            f"Strategy: {str(strategy_mode).upper()} | Method: {str(selected_method).upper()}\n"
            f"Confidence: {confidence:.0%} | Score: {score:.0%} | Threshold: {threshold:.0%} | Level: {level}\n"
            f"Alasan: {reason_short}\n"
            f"Modal: {self._idr(amount * 50)}\n"
            f"Ranking: {' | '.join(top_lines)}"
        )
        if balance is not None:
            text += f"\nSaldo saat ini: {self._idr(balance)}"
        await self._send(text)

    async def notify_result(
        self,
        periode: str,
        full_result: str,
        target_position: str,
        result_2d: str,
        actual_choice: str,
        bet_choice: Optional[str],
        won: bool,
        profit: int,
        balance: Optional[int] = None,
    ) -> None:
        text = (
            f"📊 {self._title(f'HASIL Periode {periode}')}\n"
            f"4D: <b>{full_result}</b>\n"
            f"Bet: {POSITION_LABELS.get(target_position, target_position)} | "
            f"hasil 2D={result_2d} | actual={CHOICE_LABELS.get(actual_choice, actual_choice)}\n"
        )
        if bet_choice:
            text += (
                f"Pilihan bot: {CHOICE_LABELS.get(bet_choice, bet_choice)} | "
                f"{'MENANG' if won else 'KALAH'} | net {self._net(profit)}"
            )
        else:
            text += "Bot: SKIP"
        if balance is not None:
            text += f"\nSaldo: {self._idr(balance)}"
        await self._send(text)

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
        win_rate = (total_wins / total_bets * 100) if total_bets else 0
        text = (
            f"📈 {self._title('Ringkasan Hari Ini')} — {date}\n"
            f"Bet settle: {total_bets} | Menang: {total_wins}/{total_bets} ({win_rate:.1f}%)\n"
            f"Modal: {self._idr(total_bet_amount)} | Payout: {self._idr(total_win_amount)}\n"
            f"Net: <b>{self._net(profit)}</b>"
        )
        if ending_balance is not None:
            text += f" | Saldo {self._idr(ending_balance)}"
        await self._send(text)

    async def notify_alert(self, message: str) -> None:
        await self._send(f"⚠️ <b>{self._title('Alert')}</b>\n{message}")

    async def send_startup(self, dry_run: bool = False) -> None:
        mode = " [DRY RUN MODE]" if dry_run else ""
        await self._send(
            f"🤖 <b>{self._title(f'Hokidraw Bot Aktif{mode}')}</b>\n"
            "Mode: single-bot multi-position\n"
            "Analisis: depan + tengah + belakang\n"
            "Aksi: 1 kandidat terbaik per periode"
        )

    async def send_shutdown(self) -> None:
        await self._send(f"🛑 <b>{self._title('Hokidraw Bot Berhenti')}</b>")

    async def send_limit_reached(self, daily_loss: int, limit: int) -> None:
        await self._send(
            f"🚫 <b>{self._title('Limit Rugi Harian Tercapai')}</b>\n"
            f"Rugi: {self._idr(daily_loss)} / Limit: {self._idr(limit)}\n"
            "Bot berhenti sampai tengah malam WIB."
        )
