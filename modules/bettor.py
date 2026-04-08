"""
Bet placement untuk kategori Besar/Kecil dan Genap/Ganjil.

Cara kerja:
  Betting "Besar belakang" = pasang semua 50 angka yang digit pertamanya 5-9.
  Betting "Genap belakang" = pasang semua 50 angka yang digit keduanya 0/2/4/6/8.
  Setiap kategori = 50 angka, dikirim dalam satu POST request.

  Pot win per kategori (type=B, full):
    Rp 100/nomor × 50 nomor = Rp 5.000 total taruhan
    Menang: 1 nomor cocok × 100x = Rp 10.000  →  profit bersih Rp 5.000
"""

import logging
import re
from typing import Optional

import httpx

from config import (
    BASE_URL, POOL_ID, GAME_TYPE, BET_TYPE, BET_POSISI,
    MIN_BET, MAX_BET_2D, AJAX_HEADERS,
)
from modules.auth import AuthManager
from modules.categories import get_numbers_for_category, parse_result

logger = logging.getLogger(__name__)

# Peta posisi → nilai `posisi` di API
_POSISI_MAP = {
    "depan":    "depan",
    "tengah":   "tengah",
    "belakang": "belakang",
}


class Bettor:
    def __init__(self, auth: AuthManager) -> None:
        self._auth = auth

    # ─── Bet per kategori ─────────────────────────────────────────────────────

    async def place_category_bet(
        self,
        position: str,
        bk_category: str,
        gj_category: str,
        bet_per_number_idr: int,
        dry_run: bool = False,
    ) -> Optional[dict]:
        """
        Pasang 2 bet dalam 1 round: Besar/Kecil + Genap/Ganjil untuk satu posisi.

        Args:
            position:          "depan" | "tengah" | "belakang"
            bk_category:       "besar" | "kecil"
            gj_category:       "genap" | "ganjil"
            bet_per_number_idr: IDR per nomor (min Rp 100)
            dry_run:           jika True, tidak kirim ke API

        Returns:
            dict hasil API atau dry-run placeholder.
        """
        if position not in _POSISI_MAP:
            logger.error("Posisi tidak valid: %s", position)
            return None

        bet_per_number_idr = max(MIN_BET, min(MAX_BET_2D, bet_per_number_idr))

        # Bet 1: Besar atau Kecil
        result_bk = await self._submit_category(
            position, bk_category, bet_per_number_idr, dry_run
        )

        # Bet 2: Genap atau Ganjil
        result_gj = await self._submit_category(
            position, gj_category, bet_per_number_idr, dry_run
        )

        return {
            "bk": result_bk,
            "gj": result_gj,
            "position": position,
            "bk_category": bk_category,
            "gj_category": gj_category,
            "bet_per_number": bet_per_number_idr,
        }

    async def _submit_category(
        self,
        position: str,
        category: str,
        bet_per_number_idr: int,
        dry_run: bool,
    ) -> Optional[dict]:
        """Submit satu kategori (50 angka) sebagai satu request bet."""
        numbers = get_numbers_for_category(category)
        bet_param = self._idr_to_bet_param(bet_per_number_idr)
        total = bet_per_number_idr * len(numbers)

        logger.info(
            "Bet %s %s: %d angka × Rp%s = Rp%s total | dry_run=%s",
            position, category, len(numbers), bet_per_number_idr, total, dry_run,
        )

        payload: dict[str, str] = {
            "type":   BET_TYPE,
            "ganti":  "F",
            "game":   GAME_TYPE,
            "bet":    bet_param,
            "posisi": _POSISI_MAP[position],
            "sar":    POOL_ID,
        }
        for i, num in enumerate(numbers, start=1):
            payload[f"cek{i}"]   = "1"
            payload[f"tebak{i}"] = num

        if dry_run:
            return {
                "status":   "dry_run",
                "category": category,
                "position": position,
                "numbers":  numbers,
                "total_idr": total,
            }

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
            logger.debug("API response (%s %s): %s", position, category, raw[:300])

            try:
                data = resp.json()
            except Exception:
                data = {"raw": raw}

            data["_category"] = category
            data["_position"] = position
            data["_total_idr"] = total

            if data.get("status") in (1, "1", True, "true", "ok", "success"):
                logger.info(
                    "Bet OK — %s %s | periode=%s balance=%s",
                    position, category, data.get("periode"), data.get("balance"),
                )
            else:
                logger.error("Bet GAGAL — %s %s: %s", position, category, data)

            return data

        except Exception as e:
            logger.error("Request gagal (%s %s): %s", position, category, e)
            return None

    # ─── Win check ────────────────────────────────────────────────────────────

    @staticmethod
    def check_category_win(
        position: str,
        bk_category: str,
        gj_category: str,
        result_4d: str,
    ) -> dict:
        """
        Cek apakah hasil draw cocok dengan prediksi kategori.

        Returns:
            {
                "bk_win": bool,
                "gj_win": bool,
                "actual_bk": str,
                "actual_gj": str,
            }
        """
        parsed = parse_result(result_4d)
        if not parsed or position not in parsed:
            return {"bk_win": False, "gj_win": False, "actual_bk": "?", "actual_gj": "?"}

        pos_data   = parsed[position]
        actual_bk  = pos_data["besar_kecil"]
        actual_gj  = pos_data["genap_ganjil"]

        return {
            "bk_win":    actual_bk == bk_category,
            "gj_win":    actual_gj == gj_category,
            "actual_bk": actual_bk,
            "actual_gj": actual_gj,
        }

    @staticmethod
    def calculate_category_payout(
        bet_per_number_idr: int,
        win_bk: bool,
        win_gj: bool,
        payout_multiplier: int = 100,
    ) -> dict:
        """
        Hitung payout dan total kerugian/keuntungan.

        Setiap kategori: 50 nomor × bet_per_number.
        Menang = 1 nomor cocok → payout × bet_per_number.
        """
        cost_per_cat  = bet_per_number_idr * 50
        total_wagered = cost_per_cat * 2  # BK + GJ

        won_bk = bet_per_number_idr * payout_multiplier if win_bk else 0
        won_gj = bet_per_number_idr * payout_multiplier if win_gj else 0
        total_won = won_bk + won_gj

        return {
            "total_wagered": total_wagered,
            "total_won":     total_won,
            "net":           total_won - total_wagered,
            "win_bk":        win_bk,
            "win_gj":        win_gj,
        }

    @staticmethod
    def _idr_to_bet_param(amount_idr: int) -> str:
        """Konversi IDR ke parameter bet (satuan ribu). Rp 100 → '0.1'"""
        val = amount_idr / 1000
        return str(int(val)) if val == int(val) else str(round(val, 3))
