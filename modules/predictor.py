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
    HISTORY_WINDOW, BET_TARGET,
)
from modules.categories import classify_result, CHOICE_LABELS, get_target_result, parse_result_full

logger = logging.getLogger(__name__)

# ─── Prompt template (sesuai blueprint) ──────────────────────────────────────

_PROMPT_TEMPLATE = """Kamu adalah analis statistik togel. Analisis data 2D {target_label} berikut
dan rekomendasikan taruhan untuk periode berikutnya.

Data {n} periode terakhir pasaran Hokidraw (2D {target_label}):
{history_table}

Kolom data: Periode | 2D {target_label} | Besar/Kecil | Genap/Ganjil

Analisis yang harus dilakukan:
1. Hitung frekuensi BESAR vs KECIL dari {n} periode terakhir
2. Hitung frekuensi GENAP vs GANJIL dari {n} periode terakhir
3. Analisis streak/pola terakhir (apakah ada pola berturut-turut?)
4. Analisis 10 dan 20 periode terakhir (trend terkini)
5. Identifikasi apakah ada bias signifikan
6. Berikan confidence yang realistis, bukan asal tinggi

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

_FLEET_PROMPT_TEMPLATE = """Kamu mengelola 3 bot togel yang memakai akun berbeda.
Lakukan SATU analisa saja untuk semua bot aktif berikut, dengan fokus memaksimalkan profit
namun tetap menjaga manajemen risiko yang baik.

Bot aktif/nonaktif dan status ringkas:
{fleet_status}

Data {n} periode terakhir hasil 4D Hokidraw:
{history_4d}

Aturan posisi 2D:
- depan  = digit ke-1 dan ke-2
- tengah = digit ke-2 dan ke-3
- belakang = digit ke-3 dan ke-4

Untuk setiap bot aktif:
- analisis posisi target bot tersebut
- tentukan apakah bot harus BET atau SKIP
- jika BET, berikan pilihan besar_kecil dan genap_ganjil
- boleh sarankan mode_risiko: normal, conservative, atau skip

Pertimbangan risiko:
- jika daily loss bot sudah tinggi, martingale level tinggi, atau status bot tidak sehat, lebih konservatif
- jika bot nonaktif, action harus SKIP
- gunakan confidence yang realistis; jika prediksi lemah, lebih baik sarankan SKIP

Balas HANYA JSON dengan format:
{{
  "bots": {{
    "bot-1": {{
      "action": "BET" atau "SKIP",
      "target": "depan|tengah|belakang",
      "mode_risiko": "normal|conservative|skip",
      "besar_kecil": {{"choice": "BE|KE", "confidence": 0.XX, "reason": "singkat"}},
      "genap_ganjil": {{"choice": "GE|GA", "confidence": 0.XX, "reason": "singkat"}},
      "note": "catatan singkat"
    }}
  }},
  "global_note": "catatan ringkas keseluruhan"
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

        prompt = _PROMPT_TEMPLATE.format(
            n=n,
            history_table=history_table,
            target_label=BET_TARGET.title(),
        )

        result = await self._call_llm_json(LLM_PRIMARY, prompt)
        if result is None:
            logger.warning("Model utama gagal, coba fallback")
            result = await self._call_llm_json(LLM_FALLBACK, prompt)

        return self._parse_response(result) if result is not None else None

    async def analyze_fleet(self, history: list[dict], fleet_snapshots: dict) -> Optional[dict]:
        if not history:
            logger.warning("History kosong")
            return None

        history_4d = self._build_4d_table(history[:HISTORY_WINDOW])
        status_lines = []
        for bot_name, snapshot in fleet_snapshots.items():
            status_lines.append(
                f"{bot_name} | enabled={snapshot.get('enabled', True)} | "
                f"target={snapshot.get('target', '?')} | "
                f"balance={snapshot.get('balance', '?')} | "
                f"daily_loss={snapshot.get('daily_loss', '?')} | "
                f"bk_level={snapshot.get('bk_level', '?')} | "
                f"gj_level={snapshot.get('gj_level', '?')}"
            )
        prompt = _FLEET_PROMPT_TEMPLATE.format(
            fleet_status="\n".join(status_lines) or "-",
            n=len(history[:HISTORY_WINDOW]),
            history_4d=history_4d,
        )

        result = await self._call_llm_json(LLM_PRIMARY, prompt)
        if result is None:
            logger.warning("Model utama fleet gagal, coba fallback")
            result = await self._call_llm_json(LLM_FALLBACK, prompt)

        if result is None:
            return None

        return self._parse_fleet_response(result)

    # ─── Build table ──────────────────────────────────────────────────────────

    def _build_table(self, history: list[dict]) -> str:
        rows = []
        for h in history:
            # Support dua format: dari DB (target_number_2d) atau scraper (result)
            period = h.get("period") or h.get("periode") or "?"

            if "target_number_2d" in h:
                belakang = h["target_number_2d"]
                bk = h.get("target_bk", "?")
                gj = h.get("target_gj", "?")
                bk_label = CHOICE_LABELS.get(bk, bk)
                gj_label = CHOICE_LABELS.get(gj, gj)
            else:
                result_raw = str(h.get("result", "")).strip()
                parsed = parse_result_full(result_raw)
                if parsed:
                    belakang = get_target_result(parsed, BET_TARGET)["number_2d"]
                    cat = classify_result(belakang)
                    bk_label = cat["besar_kecil_label"]
                    gj_label = cat["genap_ganjil_label"]
                else:
                    belakang = result_raw[-2:] if len(result_raw) >= 2 else "??"
                    bk_label = "?"
                    gj_label = "?"

            rows.append(f"{period} | {belakang} | {bk_label} | {gj_label}")

        return "\n".join(rows)

    def _build_4d_table(self, history: list[dict]) -> str:
        rows = []
        for h in history:
            period = h.get("period") or h.get("periode") or "?"
            result_raw = str(h.get("result", h.get("full_number", ""))).strip()
            parsed = parse_result_full(result_raw)
            if parsed:
                rows.append(
                    f"{period} | {parsed['full']} | depan={parsed['depan']} | tengah={parsed['tengah']} | belakang={parsed['belakang']}"
                )
            else:
                rows.append(f"{period} | {result_raw}")
        return "\n".join(rows)

    # ─── LLM call ─────────────────────────────────────────────────────────────

    async def _call_llm_json(self, model: str, prompt: str) -> Optional[dict]:
        try:
            response = await self._client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                temperature=LLM_TEMPERATURE,
                max_tokens=LLM_MAX_TOKENS,
            )
            content = response.choices[0].message.content
            logger.debug("LLM raw (%s): %s", model, content[:300])
            return self._extract_json(content)
        except Exception as e:
            logger.error("LLM call gagal (%s): %s", model, e)
            return None

    def _parse_fleet_response(self, data: dict) -> Optional[dict]:
        if not isinstance(data, dict):
            return None
        bots = data.get("bots")
        if not isinstance(bots, dict):
            logger.error("Fleet response tidak punya field bots")
            return None

        cleaned = {}
        for bot_name, item in bots.items():
            if not isinstance(item, dict):
                continue
            action = str(item.get("action", "SKIP")).upper()
            target = str(item.get("target", "")).lower()
            risk_mode = str(item.get("mode_risiko", "skip")).lower()
            bk_data = item.get("besar_kecil", {}) if isinstance(item.get("besar_kecil"), dict) else {}
            gj_data = item.get("genap_ganjil", {}) if isinstance(item.get("genap_ganjil"), dict) else {}

            bk_choice = str(bk_data.get("choice", "")).upper()
            gj_choice = str(gj_data.get("choice", "")).upper()

            cleaned[bot_name] = {
                "action": "BET" if action == "BET" else "SKIP",
                "target": target,
                "mode_risiko": risk_mode if risk_mode in ("normal", "conservative", "skip") else "skip",
                "besar_kecil": {
                    "choice": bk_choice if bk_choice in ("BE", "KE") else "KE",
                    "confidence": float(bk_data.get("confidence", 0.5)),
                    "reason": str(bk_data.get("reason", "")),
                },
                "genap_ganjil": {
                    "choice": gj_choice if gj_choice in ("GE", "GA") else "GE",
                    "confidence": float(gj_data.get("confidence", 0.5)),
                    "reason": str(gj_data.get("reason", "")),
                },
                "note": str(item.get("note", "")),
            }

        return {
            "bots": cleaned,
            "global_note": str(data.get("global_note", "")),
        }

    def _extract_json(self, content: str) -> Optional[dict]:
        # Hapus markdown code block jika ada
        content = re.sub(r"```(?:json)?\s*", "", content).strip().rstrip("`").strip()

        try:
            return json.loads(content)
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", content, re.DOTALL)
            if match:
                try:
                    return json.loads(match.group())
                except json.JSONDecodeError:
                    logger.error("Gagal parse JSON dari LLM")
                    return None
            else:
                logger.error("Tidak ada JSON dalam respons LLM")
                return None

    def _parse_response(self, data: dict) -> Optional[dict]:
        if not isinstance(data, dict):
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
