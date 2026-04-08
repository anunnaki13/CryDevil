"""LLM-based predictor untuk kategori Besar/Kecil dan Genap/Ganjil."""

import json
import logging
import re
from typing import Optional

from openai import AsyncOpenAI

from config import (
    OPENROUTER_API_KEY, OPENROUTER_BASE_URL,
    LLM_PRIMARY, LLM_FALLBACK,
    LLM_TEMPERATURE, LLM_MAX_TOKENS,
    BET_POSITIONS,
)
from modules.categories import parse_result, result_summary

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """Kamu adalah analis statistik togel yang ahli analisis pola data.
Tugasmu menganalisis history hasil 2D togel Hokidraw dan memprediksi kategori untuk draw berikutnya.

Setiap hasil 4D dibagi 3 posisi:
- DEPAN    = 2 digit pertama (misal "12" dari "1295")
- TENGAH   = 2 digit tengah  (misal "29" dari "1295")
- BELAKANG = 2 digit terakhir (misal "95" dari "1295")

Aturan klasifikasi per pasangan:
- Besar/Kecil → digit PERTAMA pasangan: 0-4=Kecil, 5-9=Besar
- Genap/Ganjil → digit KEDUA pasangan: 0,2,4,6,8=Genap; 1,3,5,7,9=Ganjil

Analisis pola distribusi, streak, dan frekuensi untuk setiap posisi.
Jawab HANYA dengan JSON valid, tanpa teks lain:
{
  "predictions": [
    {
      "position": "belakang",
      "besar_kecil": "besar",
      "bk_confidence": 0.65,
      "bk_reason": "alasan singkat maks 15 kata",
      "genap_ganjil": "ganjil",
      "gj_confidence": 0.60,
      "gj_reason": "alasan singkat maks 15 kata"
    }
  ],
  "analysis": "ringkasan analisis maks 3 kalimat"
}

Satu objek dalam "predictions" per posisi yang diminta.
Confidence antara 0.01–0.99.
"""

_USER_TEMPLATE = """Berikut {count} hasil draw terakhir Hokidraw (terbaru di atas):

{history}

Prediksi kategori draw BERIKUTNYA untuk posisi: {positions}
Berikan analisis berdasarkan distribusi, streak berturut-turut, dan pola yang terlihat."""


class Predictor:
    def __init__(self) -> None:
        self._client = AsyncOpenAI(
            api_key=OPENROUTER_API_KEY,
            base_url=OPENROUTER_BASE_URL,
        )

    async def predict(self, history: list[dict]) -> Optional[dict]:
        """
        Prediksi kategori BK/GJ dari history draw.

        Args:
            history: list dict dengan key 'result' (4-digit string), terbaru pertama.

        Returns:
            dict dengan 'predictions' dan 'analysis', atau None jika gagal.
        """
        if not history:
            logger.warning("History kosong, tidak bisa prediksi")
            return None

        # Bangun teks history dengan kategori sudah dihitung
        lines = []
        for h in history[:200]:
            parsed = parse_result(h.get("result", ""))
            if parsed:
                lines.append(f"{h.get('periode', '?')} | {result_summary(parsed)}")
            else:
                lines.append(f"{h.get('periode', '?')} | {h.get('result', '?')}")

        positions_str = ", ".join(BET_POSITIONS)
        user_prompt = _USER_TEMPLATE.format(
            count=len(lines),
            history="\n".join(lines),
            positions=positions_str,
        )

        result = await self._call_llm(LLM_PRIMARY, user_prompt)
        if result is None:
            logger.warning("Model utama gagal, coba fallback")
            result = await self._call_llm(LLM_FALLBACK, user_prompt)

        return result

    async def _call_llm(self, model: str, user_prompt: str) -> Optional[dict]:
        try:
            response = await self._client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user",   "content": user_prompt},
                ],
                temperature=LLM_TEMPERATURE,
                max_tokens=LLM_MAX_TOKENS,
            )
            content = response.choices[0].message.content
            logger.debug("LLM raw (%s): %s", model, content)
            return self._parse_response(content)
        except Exception as e:
            logger.error("LLM call gagal (%s): %s", model, e)
            return None

    def _parse_response(self, content: str) -> Optional[dict]:
        # Hapus markdown code block jika ada
        content = re.sub(r"```(?:json)?\s*", "", content).strip().rstrip("`").strip()

        try:
            data = json.loads(content)
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", content, re.DOTALL)
            if match:
                try:
                    data = json.loads(match.group())
                except json.JSONDecodeError:
                    logger.error("Gagal parse JSON dari LLM")
                    return None
            else:
                logger.error("Tidak ada JSON dalam respons LLM")
                return None

        predictions = data.get("predictions", [])
        if not predictions:
            logger.error("Tidak ada predictions dalam respons LLM")
            return None

        validated = []
        for p in predictions:
            pos = str(p.get("position", "")).lower()
            if pos not in ("depan", "tengah", "belakang"):
                logger.warning("Posisi tidak valid dilewati: %s", pos)
                continue

            bk = str(p.get("besar_kecil", "")).lower()
            gj = str(p.get("genap_ganjil", "")).lower()

            if bk not in ("besar", "kecil"):
                logger.warning("besar_kecil tidak valid: %s", bk)
                continue
            if gj not in ("genap", "ganjil"):
                logger.warning("genap_ganjil tidak valid: %s", gj)
                continue

            validated.append({
                "position":      pos,
                "besar_kecil":   bk,
                "bk_confidence": float(p.get("bk_confidence", 0.5)),
                "bk_reason":     str(p.get("bk_reason", "")),
                "genap_ganjil":  gj,
                "gj_confidence": float(p.get("gj_confidence", 0.5)),
                "gj_reason":     str(p.get("gj_reason", "")),
            })

        if not validated:
            logger.error("Tidak ada prediksi valid setelah validasi")
            return None

        return {
            "predictions": validated,
            "analysis": data.get("analysis", ""),
        }
