"""
Bet placement — submit 50 angka per kategori (BE/KE/GE/GA) ke /games/4d/send.
"""

import logging
from typing import Optional

from bs4 import BeautifulSoup

from config import AJAX_HEADERS, BASE_URL, BET_TYPE, GAME_TYPE, MAX_BET_2D, MIN_BET, POOL_ID, POSITIONS
from modules.auth import AuthManager
from modules.categories import CHOICE_LABELS, classify_result, get_numbers_for_category

logger = logging.getLogger(__name__)


class Bettor:
    def __init__(self, auth: AuthManager) -> None:
        self._auth = auth

    async def place_bet(
        self,
        choice: str,
        bet_amount_per_angka: int,
        target_position: str,
        dry_run: bool = False,
    ) -> Optional[dict]:
        if choice not in ("BE", "KE", "GE", "GA"):
            logger.error("Pilihan tidak valid: %s", choice)
            return None
        if target_position not in POSITIONS:
            logger.error("Posisi tidak valid: %s", target_position)
            return None

        bet_amount_per_angka = max(MIN_BET, min(MAX_BET_2D, bet_amount_per_angka))
        numbers = get_numbers_for_category(choice)
        bet_param = self._to_bet_param(bet_amount_per_angka)
        total_idr = bet_amount_per_angka * len(numbers)

        logger.info(
            "Bet %s @ %s: %d angka × Rp%s = Rp%s | dry=%s",
            choice, target_position, len(numbers), bet_amount_per_angka, total_idr, dry_run,
        )

        if dry_run:
            return {
                "status": "dry_run",
                "choice": choice,
                "label": CHOICE_LABELS[choice],
                "target_position": target_position,
                "numbers": numbers,
                "total_idr": total_idr,
            }

        payload: dict[str, str] = {
            "type": BET_TYPE,
            "ganti": "F",
            "game": GAME_TYPE,
            "bet": bet_param,
            "posisi": target_position,
            "sar": POOL_ID,
        }
        for idx, num in enumerate(numbers, start=1):
            payload[f"cek{idx}"] = "1"
            payload[f"tebak{idx}"] = num

        client = await self._auth.get_client()
        try:
            resp = await client.post(
                BASE_URL + "/games/4d/send",
                data=payload,
                headers={**AJAX_HEADERS, "Referer": f"{BASE_URL}/games/4d/{POOL_ID}"},
            )
            raw = resp.text
            try:
                data = resp.json()
            except Exception:
                data = {"raw": raw}

            data["_choice"] = choice
            data["_label"] = CHOICE_LABELS[choice]
            data["_target_position"] = target_position
            data["_total_idr"] = total_idr
            data["_accepted_count"] = self._count_accepted_transactions(data)

            if self.is_bet_successful(data):
                data["_history_verify_count"] = await self._verify_latest_history(numbers)
                logger.info("Bet OK — %s @ %s", choice, target_position)
            else:
                logger.error("Bet gagal — %s @ %s: %s", choice, target_position, data)
            return data
        except Exception as exc:
            logger.error("Request gagal (%s @ %s): %s", choice, target_position, exc)
            return None

    @staticmethod
    def is_bet_successful(response: Optional[dict]) -> bool:
        if not response:
            return False
        if response.get("status") == "dry_run":
            return True
        raw = str(response.get("raw", "") or response.get("msg", "") or response.get("message", ""))
        if "bet close" in raw.lower():
            return False
        status = response.get("status")
        accepted = int(response.get("_accepted_count", 0) or 0)
        return status in (1, "1", True, "true", "ok", "success") and accepted > 0

    @staticmethod
    def get_failure_reason(response: Optional[dict]) -> str:
        if not response:
            return "request_failed"
        for key in ("msg", "message", "raw"):
            text = str(response.get(key, "") or "").strip()
            if text:
                normalized = " ".join(text.split())
                return normalized[:117] + "..." if len(normalized) > 120 else normalized
        return f"status={response.get('status')}"

    @staticmethod
    def check_win(bet_choice: str, result_2d: str) -> bool:
        categories = classify_result(result_2d)
        if bet_choice in ("BE", "KE"):
            return categories["besar_kecil"] == bet_choice
        return categories["genap_ganjil"] == bet_choice

    @staticmethod
    def calculate_payout(bet_amount_per_angka: int, won: bool, payout_multiplier: int = 100) -> dict:
        wagered = bet_amount_per_angka * 50
        win_amount = bet_amount_per_angka * payout_multiplier if won else 0
        return {"wagered": wagered, "won": win_amount, "net": win_amount - wagered}

    @staticmethod
    def _to_bet_param(amount_idr: int) -> str:
        value = amount_idr / 1000
        return str(int(value)) if value == int(value) else str(round(value, 3))

    @staticmethod
    def _count_accepted_transactions(data: dict) -> int:
        transaksi = str(data.get("transaksi", "") or "")
        if not transaksi:
            return 0
        return transaksi.count("//") + transaksi.count("\\/\\/") + 1

    async def _verify_latest_history(self, expected_numbers: list[str]) -> int:
        client = await self._auth.get_client()
        try:
            resp = await client.get(f"{BASE_URL}/games/4d/history/{GAME_TYPE}/{POOL_ID}", headers=AJAX_HEADERS)
            soup = BeautifulSoup(resp.text, "lxml")
            rows = soup.select("table tbody tr")
            latest_numbers = []
            for tr in rows[:50]:
                cols = [td.get_text(strip=True) for td in tr.find_all("td")]
                if len(cols) >= 2:
                    latest_numbers.append(cols[1])
            return len(set(expected_numbers) & set(latest_numbers))
        except Exception as exc:
            logger.debug("History verify gagal: %s", exc)
            return 0
