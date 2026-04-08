"""LLM predictor via OpenRouter — prediksi BE/KE dan GE/GA untuk 2D Belakang."""

import json
import logging
import re
from typing import Optional

from openai import AsyncOpenAI

from config import (
    OPENROUTER_API_KEY, OPENROUTER_BASE_URL,
    LLM_PRIMARY, LLM_FALLBACK,
    LLM_TEMPERATURE, LLM_MAX_TOKENS,
    HISTORY_WINDOW,
)
from modules.categories import classify_result, CHOICE_LABELS

logger = logging.getLogger(__name__)

# ─── Prompt template (sesuai blueprint) ──────────────────────────────────────

_PROMPT_TEMPLATE = """Kamu adalah analis statistik togel. Analisis data 2D Belakang berikut
dan rekomendasikan taruhan untuk periode berikutnya.

Data {n} periode terakhir pasaran Hokidraw (2D Belakang):
{history_table}

Kolom data: Periode | 2D Belakang | Besar/Kecil | Genap/Ganjil

Analisis yang harus dilakukan:
1. Hitung frekuensi BESAR vs KECIL dari {n} periode terakhir
2. Hitung frekuensi GENAP vs GANJIL dari {n} periode terakhir
3. Analisis streak/pola terakhir (apakah ada pola berturut-turut?)
4. Analisis 10 dan 20 periode terakhir (trend terkini)
5. Identifikasi apakah ada bias signifikan

ATURAN KLASIFIKASI:
- BESAR (BE) = angka 50-99 (digit pertama 5-9)
- KECIL (KE) = angka 00-49 (digit pertama 0-4)
- GENAP (GE) = digit terakhir 0,2,4,6,8
- GANJIL (GA) = digit terakhir 1,3,5,7,9

Respond HANYA dalam format JSON berikut, tanpa teks lain:
{{
  "besar_kecil": {{
    "choice": "BE" atau "KE",
    "confidence": 0.XX,
    "reason": "penjelasan singkat"
  }},
  "genap_ganjil": {{
    "choice": "GE" atau "GA",
    "confidence": 0.XX,
    "reason": "penjelasan singkat"
  }},
  "stats": {{
    "besar_count": N,
    "kecil_count": N,
    "genap_count": N,
    "ganjil_count": N,
    "last_10_bk": "BBKBKKBKBB",
    "last_10_gj": "GGJGGGJGGG"
  }}
}}"""


class Predictor:
    def __init__(self) -> None:
        self._client = AsyncOpenAI(
            api_key=OPENROUTER_API_KEY,
            base_url=OPENROUTER_BASE_URL,
        )

    async def analyze(self, history: list[dict]) -> Optional[dict]:
        """
        Analisis history dan prediksi BE/KE + GE/GA untuk draw berikutnya.

        Args:
            history: list dict dari DB (kolom: period, number_2d_belakang, belakang_bk, belakang_gj)
                     atau dari scraper (kolom: periode/period, result)
                     Terbaru pertama.

        Returns:
            {
                "besar_kecil":  {"choice": "BE"|"KE", "confidence": float, "reason": str},
                "genap_ganjil": {"choice": "GE"|"GA", "confidence": float, "reason": str},
                "stats": {...}
            }
            atau None jika gagal.
        """
        if not history:
            logger.warning("History kosong")
            return None

        history_table = self._build_table(history[:HISTORY_WINDOW])
        n = len(history[:HISTORY_WINDOW])

        prompt = _PROMPT_TEMPLATE.format(n=n, history_table=history_table)

        result = await self._call_llm(LLM_PRIMARY, prompt)
        if result is None:
            logger.warning("Model utama gagal, coba fallback")
            result = await self._call_llm(LLM_FALLBACK, prompt)

        return result

    # ─── Build table ──────────────────────────────────────────────────────────

    def _build_table(self, history: list[dict]) -> str:
        rows = []
        for h in history:
            # Support dua format: dari DB (number_2d_belakang) atau scraper (result)
            period = h.get("period") or h.get("periode") or "?"

            if "number_2d_belakang" in h:
                belakang = h["number_2d_belakang"]
                bk = h.get("belakang_bk", "?")
                gj = h.get("belakang_gj", "?")
                bk_label = CHOICE_LABELS.get(bk, bk)
                gj_label = CHOICE_LABELS.get(gj, gj)
            else:
                # Format dari scraper: result = "1295" (4 digit)
                result_raw = str(h.get("result", "")).strip()
                import re as _re
                digits = _re.sub(r"\D", "", result_raw)
                if len(digits) >= 4:
                    belakang = digits[-2:]
                    cat = classify_result(belakang)
                    bk_label = cat["besar_kecil_label"]
                    gj_label = cat["genap_ganjil_label"]
                else:
                    belakang = result_raw[-2:] if len(result_raw) >= 2 else "??"
                    bk_label = "?"
                    gj_label = "?"

            rows.append(f"{period} | {belakang} | {bk_label} | {gj_label}")

        return "\n".join(rows)

    # ─── LLM call ─────────────────────────────────────────────────────────────

    async def _call_llm(self, model: str, prompt: str) -> Optional[dict]:
        try:
            response = await self._client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                temperature=LLM_TEMPERATURE,
                max_tokens=LLM_MAX_TOKENS,
            )
            content = response.choices[0].message.content
            logger.debug("LLM raw (%s): %s", model, content[:300])
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

        # Validasi besar_kecil
        bk_data = data.get("besar_kecil", {})
        bk_choice = str(bk_data.get("choice", "")).upper()
        if bk_choice not in ("BE", "KE"):
            logger.error("choice besar_kecil tidak valid: %s", bk_choice)
            return None

        # Validasi genap_ganjil
        gj_data = data.get("genap_ganjil", {})
        gj_choice = str(gj_data.get("choice", "")).upper()
        if gj_choice not in ("GE", "GA"):
            logger.error("choice genap_ganjil tidak valid: %s", gj_choice)
            return None

        return {
            "besar_kecil": {
                "choice":     bk_choice,
                "confidence": float(bk_data.get("confidence", 0.5)),
                "reason":     str(bk_data.get("reason", "")),
            },
            "genap_ganjil": {
                "choice":     gj_choice,
                "confidence": float(gj_data.get("confidence", 0.5)),
                "reason":     str(gj_data.get("reason", "")),
            },
            "stats": data.get("stats", {}),
        }
