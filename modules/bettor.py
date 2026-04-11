"""
Bet placement — submit 50 angka per kategori (BE/KE/GE/GA) ke /games/4d/send.

Satu "bet" = 50 angka × bet_amount_per_angka dikirim dalam 1 POST request.
BET FULL (type=B): hadiah x100 per angka yang cocok.
"""

import logging
from typing import Optional

import httpx
from bs4 import BeautifulSoup

from config import (
    BASE_URL, POOL_ID, GAME_TYPE, BET_TYPE, BET_POSISI,
    MIN_BET, MAX_BET_2D, AJAX_HEADERS,
)
from modules.auth import AuthManager
from modules.categories import get_numbers_for_category, classify_result, CHOICE_LABELS

logger = logging.getLogger(__name__)


class Bettor:
    def __init__(self, auth: AuthManager) -> None:
        self._auth = auth

    # ─── Place single bet (satu dimensi) ─────────────────────────────────────

    async def place_bet(
        self,
        choice: str,
        bet_amount_per_angka: int,
        dry_run: bool = False,
    ) -> Optional[dict]:
        """
        Pasang satu bet untuk pilihan BE/KE/GE/GA.

        Args:
            choice:               "BE" | "KE" | "GE" | "GA"
            bet_amount_per_angka: IDR per angka (min Rp 100)
            dry_run:              jika True tidak kirim ke API

        Returns:
            dict respons API atau dry-run placeholder.
        """
        if choice not in ("BE", "KE", "GE", "GA"):
            logger.error("Pilihan tidak valid: %s", choice)
            return None

        bet_amount_per_angka = max(MIN_BET, min(MAX_BET_2D, bet_amount_per_angka))
        numbers   = get_numbers_for_category(choice)
        bet_param = self._to_bet_param(bet_amount_per_angka)
        total_idr = bet_amount_per_angka * len(numbers)

        logger.info(
            "Bet %s (%s): %d angka × Rp%s = Rp%s | dry=%s",
            choice, CHOICE_LABELS[choice], len(numbers), bet_amount_per_angka, total_idr, dry_run,
        )

        if dry_run:
            return {
                "status":   "dry_run",
                "choice":   choice,
                "label":    CHOICE_LABELS[choice],
                "numbers":  numbers,
                "total_idr": total_idr,
            }

        # Build form payload
        payload: dict[str, str] = {
            "type":   BET_TYPE,
            "ganti":  "F",
            "game":   GAME_TYPE,
            "bet":    bet_param,
            "posisi": BET_POSISI,
            "sar":    POOL_ID,
        }
        for i, num in enumerate(numbers, start=1):
            payload[f"cek{i}"]   = "1"
            payload[f"tebak{i}"] = num

        client = await self._auth.get_client()
        try:
            resp = await client.post(
                BASE_URL + "/games/4d/send",
                data=payload,
                headers={
                    **AJAX_HEADERS,
                    "Referer": f"{BASE_URL}/games/4d/{POOL_ID}",
                },
            )
            raw = resp.text
            logger.debug("API resp (%s): %s", choice, raw[:300])

            try:
                data = resp.json()
            except Exception:
                data = {"raw": raw}

            data["_choice"]    = choice
            data["_label"]     = CHOICE_LABELS[choice]
            data["_total_idr"] = total_idr
            data["_accepted_count"] = self._count_accepted_transactions(data)

            ok = data.get("status") in (1, "1", True, "true", "ok", "success")
            if ok:
                logger.info(
                    "Bet OK — %s | periode=%s balance=%s accepted=%s",
                    choice, data.get("periode"), data.get("balance"), data["_accepted_count"],
                )
                if data["_accepted_count"] not in (0, len(numbers)):
                    logger.warning(
                        "Accepted count tidak penuh untuk %s: expected=%s actual=%s",
                        choice, len(numbers), data["_accepted_count"],
                    )
                data["_history_verify_count"] = await self._verify_latest_history(numbers)
            else:
                logger.error("Bet GAGAL — %s: %s", choice, data)

            return data

        except Exception as e:
            logger.error("Request gagal (%s): %s", choice, e)
            return None

    # ─── Double bet (BK + GJ sekaligus) ──────────────────────────────────────

    async def place_double_bet(
        self,
        bk_choice: str,
        gj_choice: str,
        bk_amount: int,
        gj_amount: int,
        dry_run: bool = False,
    ) -> list[dict]:
        """
        Pasang 2 bet: satu untuk BK, satu untuk GJ.

        Return list 2 respons: [bk_result, gj_result]
        """
        bk_result = await self.place_bet(bk_choice, bk_amount, dry_run=dry_run)
        gj_result = await self.place_bet(gj_choice, gj_amount, dry_run=dry_run)
        return [bk_result, gj_result]

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
        if status in (1, "1", True, "true", "ok", "success") and accepted > 0:
            return True
        return False

    @staticmethod
    def get_failure_reason(response: Optional[dict]) -> str:
        if not response:
            return "request_failed"

        for key in ("msg", "message", "raw"):
            text = str(response.get(key, "") or "").strip()
            if text:
                normalized = " ".join(text.split())
                if len(normalized) > 120:
                    normalized = normalized[:117] + "..."
                return normalized

        status = response.get("status")
        return f"status={status}" if status is not None else "bet_failed"

    # ─── Win check ────────────────────────────────────────────────────────────

    @staticmethod
    def check_win(bet_choice: str, result_2d: str) -> bool:
        """
        Cek apakah bet menang.

        Args:
            bet_choice: "BE" | "KE" | "GE" | "GA"
            result_2d:  2 digit belakang yang keluar, misal "95"

        Returns:
            True jika menang.
        """
        cat = classify_result(result_2d)
        if bet_choice in ("BE", "KE"):
            return cat["besar_kecil"] == bet_choice
        else:
            return cat["genap_ganjil"] == bet_choice

    @staticmethod
    def calculate_payout(
        bet_amount_per_angka: int,
        won: bool,
        payout_multiplier: int = 100,
    ) -> dict:
        """
        Hitung payout satu bet.
        Wagered = bet_amount_per_angka × 50 angka.
        Won     = bet_amount_per_angka × payout_multiplier (jika menang).
        """
        wagered = bet_amount_per_angka * 50
        win_amt = bet_amount_per_angka * payout_multiplier if won else 0
        return {
            "wagered": wagered,
            "won":     win_amt,
            "net":     win_amt - wagered,
        }

    @staticmethod
    def _to_bet_param(amount_idr: int) -> str:
        """Konversi IDR ke satuan ribu untuk API. Rp 100 → '0.1'"""
        val = amount_idr / 1000
        return str(int(val)) if val == int(val) else str(round(val, 3))

    @staticmethod
    def _count_accepted_transactions(data: dict) -> int:
        transaksi = str(data.get("transaksi", "") or "")
        if not transaksi:
            return 0
        return transaksi.count("//") + transaksi.count("\\/\\/") + 1

    async def _verify_latest_history(self, expected_numbers: list[str]) -> int:
        """
        Verifikasi ringan setelah submit:
        ambil history game aktif sekali, lalu hitung berapa angka expected yang muncul
        di 50 row teratas. Non-fatal jika gagal.
        """
        client = await self._auth.get_client()
        try:
            resp = await client.get(
                f"{BASE_URL}/games/4d/history/{GAME_TYPE}/{POOL_ID}",
                headers=AJAX_HEADERS,
            )
            soup = BeautifulSoup(resp.text, "lxml")
            rows = soup.select("table tbody tr")
            latest_numbers = []
            for tr in rows[:50]:
                cols = [td.get_text(strip=True) for td in tr.find_all("td")]
                if len(cols) >= 2:
                    latest_numbers.append(cols[1])

            matched = len(set(expected_numbers) & set(latest_numbers))
            if matched and matched != len(expected_numbers):
                logger.warning(
                    "History verify parsial: expected=%s matched=%s",
                    len(expected_numbers), matched,
                )
            elif matched == len(expected_numbers):
                logger.info("History verify OK: %s/%s angka terdeteksi", matched, len(expected_numbers))
            return matched
        except Exception as e:
            logger.debug("History verify failed: %s", e)
            return 0
