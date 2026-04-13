"""Money management for 6 independent martingale slots."""

import logging
from datetime import datetime, timedelta, timezone

from config import (
    DAILY_LOSS_LIMIT,
    DEFAULT_OPERATION_MODE,
    MARTINGALE_STEP_LOSSES,
    SLOTS,
    get_operation_profile,
)
from modules import database as db

logger = logging.getLogger(__name__)

_WIB = timezone(timedelta(hours=7))


def _today_wib() -> str:
    return datetime.now(_WIB).strftime("%Y-%m-%d")


def _slot_keys(slot: str) -> tuple[str, str]:
    if slot not in SLOTS:
        raise ValueError(f"Slot tidak valid: {slot}")
    return (f"consecutive_losses_{slot}", f"martingale_level_{slot}")


class MoneyManager:
    async def get_operation_mode(self) -> str:
        return await db.get_state("operation_mode", DEFAULT_OPERATION_MODE) or DEFAULT_OPERATION_MODE

    async def get_operation_profile(self) -> dict:
        return get_operation_profile(await self.get_operation_mode())

    async def get_level(self, slot: str) -> int:
        _, level_key = _slot_keys(slot)
        return int(await db.get_state(level_key, "0"))

    async def set_level(self, slot: str, level: int) -> None:
        _, level_key = _slot_keys(slot)
        profile = await self.get_operation_profile()
        max_level = len(profile["martingale_levels"]) - 1
        await db.set_state(level_key, str(min(level, max_level)))

    async def get_bet_amount(self, slot: str) -> int:
        level = await self.get_level(slot)
        profile = await self.get_operation_profile()
        levels = profile["martingale_levels"]
        safe_level = min(level, len(levels) - 1)
        return levels[safe_level]

    async def get_consecutive_losses(self, slot: str) -> int:
        loss_key, _ = _slot_keys(slot)
        return int(await db.get_state(loss_key, "0"))

    async def set_consecutive_losses(self, slot: str, count: int) -> None:
        loss_key, _ = _slot_keys(slot)
        await db.set_state(loss_key, str(count))

    async def get_daily_loss(self) -> int:
        return int(await db.get_state("daily_loss", "0"))

    async def add_daily_loss(self, amount: int) -> int:
        current = await self.get_daily_loss()
        new_value = current + amount
        await db.set_state("daily_loss", str(new_value))
        return new_value

    async def reset_daily_loss(self) -> None:
        await db.set_state("daily_loss", "0")

    async def is_daily_limit_reached(self) -> bool:
        return (await self.get_daily_loss()) >= DAILY_LOSS_LIMIT

    async def check_and_enforce_daily_limit(self) -> bool:
        if await self.is_daily_limit_reached():
            loss = await self.get_daily_loss()
            logger.warning(
                "Daily loss limit tercapai: Rp%s / Rp%s — pause sampai tengah malam WIB",
                loss, DAILY_LOSS_LIMIT,
            )
            return False
        return True

    async def record_win(self, slot: str, wagered: int, won: int) -> None:
        await self.set_consecutive_losses(slot, 0)
        await self.set_level(slot, 0)
        logger.info("%s WIN: wagered=Rp%s won=Rp%s net=+Rp%s", slot.upper(), wagered, won, won - wagered)
        await db.update_daily_stats(_today_wib(), wagered, won, is_win=True)

    async def record_loss(self, slot: str, wagered: int) -> None:
        losses = await self.get_consecutive_losses(slot) + 1
        await self.set_consecutive_losses(slot, losses)
        daily = await self.add_daily_loss(wagered)
        logger.info("%s LOSS: consecutive=%s daily_loss=Rp%s/Rp%s", slot.upper(), losses, daily, DAILY_LOSS_LIMIT)

        if losses % MARTINGALE_STEP_LOSSES == 0:
            current = await self.get_level(slot)
            profile = await self.get_operation_profile()
            max_level = len(profile["martingale_levels"]) - 1
            if current < max_level:
                new_level = current + 1
                await self.set_level(slot, new_level)
                logger.info("%s level naik: %s -> %s", slot.upper(), current, new_level)
            else:
                logger.warning("%s sudah di level maksimum %s", slot.upper(), max_level)

        await db.update_daily_stats(_today_wib(), wagered, 0, is_win=False)

    async def midnight_reset(self) -> None:
        await self.reset_daily_loss()
        logger.info("Midnight reset: daily_loss direset")

    async def get_status_summary(self) -> dict:
        profile = await self.get_operation_profile()
        levels = profile["martingale_levels"]
        slots = {}
        for slot in SLOTS:
            level = await self.get_level(slot)
            losses = await self.get_consecutive_losses(slot)
            safe_level = min(level, len(levels) - 1)
            slots[slot] = {
                "level": safe_level,
                "losses": losses,
                "bet": levels[safe_level],
            }

        daily = await self.get_daily_loss()
        return {
            "mode": profile["key"],
            "mode_label": profile["label"],
            "threshold": profile["threshold"],
            "martingale_levels": levels,
            "slots": slots,
            "daily_loss": daily,
            "daily_limit": DAILY_LOSS_LIMIT,
            "limit_sisa": max(0, DAILY_LOSS_LIMIT - daily),
            "limit_hit": daily >= DAILY_LOSS_LIMIT,
        }
