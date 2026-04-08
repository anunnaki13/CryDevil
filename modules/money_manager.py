"""
Money management — Soft Martingale dengan tracking TERPISAH untuk BK dan GJ.

Setiap dimensi (besar_kecil dan genap_ganjil) punya:
  - consecutive_losses sendiri
  - martingale_level sendiri

Win → reset level dimensi itu ke 0.
Loss → increment counter; naik level tiap MARTINGALE_STEP_LOSSES kalah berturut.
"""

import logging
from datetime import datetime, timezone, timedelta

from config import (
    MARTINGALE_LEVELS, MARTINGALE_STEP_LOSSES,
    MAX_MARTINGALE_LEVEL, DAILY_LOSS_LIMIT,
)
from modules import database as db

logger = logging.getLogger(__name__)

_WIB = timezone(timedelta(hours=7))


def _today_wib() -> str:
    return datetime.now(_WIB).strftime("%Y-%m-%d")


# Kunci state di DB per dimensi
_KEYS = {
    "besar_kecil":  ("consecutive_losses_bk", "martingale_level_bk"),
    "genap_ganjil": ("consecutive_losses_gj", "martingale_level_gj"),
}


class MoneyManager:

    # ─── Level & bet amount ───────────────────────────────────────────────────

    async def get_level(self, dimension: str) -> int:
        _, level_key = _KEYS[dimension]
        return int(await db.get_state(level_key, "0"))

    async def set_level(self, dimension: str, level: int) -> None:
        _, level_key = _KEYS[dimension]
        await db.set_state(level_key, str(min(level, MAX_MARTINGALE_LEVEL)))

    async def get_bet_amount(self, dimension: str) -> int:
        """Return nominal bet per ANGKA (IDR) untuk dimensi ini."""
        level = await self.get_level(dimension)
        return MARTINGALE_LEVELS[level]

    async def get_consecutive_losses(self, dimension: str) -> int:
        loss_key, _ = _KEYS[dimension]
        return int(await db.get_state(loss_key, "0"))

    async def set_consecutive_losses(self, dimension: str, count: int) -> None:
        loss_key, _ = _KEYS[dimension]
        await db.set_state(loss_key, str(count))

    # ─── Daily loss ───────────────────────────────────────────────────────────

    async def get_daily_loss(self) -> int:
        return int(await db.get_state("daily_loss", "0"))

    async def add_daily_loss(self, amount: int) -> int:
        current = await self.get_daily_loss()
        new_val = current + amount
        await db.set_state("daily_loss", str(new_val))
        return new_val

    async def reset_daily_loss(self) -> None:
        await db.set_state("daily_loss", "0")

    async def is_daily_limit_reached(self) -> bool:
        return (await self.get_daily_loss()) >= DAILY_LOSS_LIMIT

    async def check_and_enforce_daily_limit(self) -> bool:
        """Return True jika masih bisa bet, False jika limit tercapai."""
        if await self.is_daily_limit_reached():
            loss = await self.get_daily_loss()
            logger.warning(
                "Daily loss limit tercapai: Rp%s / Rp%s — pause sampai tengah malam WIB",
                loss, DAILY_LOSS_LIMIT,
            )
            return False
        return True

    # ─── Record hasil ─────────────────────────────────────────────────────────

    async def record_win(self, dimension: str, wagered: int, won: int) -> None:
        """Catat menang — reset level dimensi ini."""
        await self.set_consecutive_losses(dimension, 0)
        await self.set_level(dimension, 0)
        logger.info(
            "%s WIN: wagered=Rp%s won=Rp%s net=+Rp%s → level reset ke 0",
            dimension.upper(), wagered, won, won - wagered,
        )
        today = _today_wib()
        await db.update_daily_stats(today, wagered, won, is_win=True)

    async def record_loss(self, dimension: str, wagered: int) -> None:
        """Catat kalah — update streak dan level martingale jika perlu."""
        losses = await self.get_consecutive_losses(dimension) + 1
        await self.set_consecutive_losses(dimension, losses)

        daily = await self.add_daily_loss(wagered)
        logger.info(
            "%s LOSS: consecutive=%s daily_loss=Rp%s/Rp%s",
            dimension.upper(), losses, daily, DAILY_LOSS_LIMIT,
        )

        # Naik level setiap MARTINGALE_STEP_LOSSES kalah berturut
        if losses % MARTINGALE_STEP_LOSSES == 0:
            current = await self.get_level(dimension)
            if current < MAX_MARTINGALE_LEVEL:
                new_level = current + 1
                await self.set_level(dimension, new_level)
                logger.info(
                    "%s level naik: %s → %s (Rp%s/angka)",
                    dimension.upper(), current, new_level, MARTINGALE_LEVELS[new_level],
                )
            else:
                logger.warning(
                    "%s sudah di level maksimum %s (Rp%s/angka)",
                    dimension.upper(), MAX_MARTINGALE_LEVEL,
                    MARTINGALE_LEVELS[MAX_MARTINGALE_LEVEL],
                )

        today = _today_wib()
        await db.update_daily_stats(today, wagered, 0, is_win=False)

    # ─── Daily reset ─────────────────────────────────────────────────────────

    async def midnight_reset(self) -> None:
        """Reset semua counter harian di tengah malam WIB."""
        await self.reset_daily_loss()
        logger.info("Midnight reset: daily_loss direset")

    # ─── Summary ─────────────────────────────────────────────────────────────

    async def get_status_summary(self) -> dict:
        bk_level  = await self.get_level("besar_kecil")
        gj_level  = await self.get_level("genap_ganjil")
        bk_losses = await self.get_consecutive_losses("besar_kecil")
        gj_losses = await self.get_consecutive_losses("genap_ganjil")
        daily     = await self.get_daily_loss()

        return {
            "bk_level":   bk_level,
            "gj_level":   gj_level,
            "bk_losses":  bk_losses,
            "gj_losses":  gj_losses,
            "bk_bet":     MARTINGALE_LEVELS[bk_level],
            "gj_bet":     MARTINGALE_LEVELS[gj_level],
            "daily_loss": daily,
            "daily_limit": DAILY_LOSS_LIMIT,
            "limit_sisa": max(0, DAILY_LOSS_LIMIT - daily),
            "limit_hit":  daily >= DAILY_LOSS_LIMIT,
        }
