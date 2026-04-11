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

Ringkasan statistik lokal yang SUDAH dihitung:
{signal_summary}

Kolom data: Periode | 2D {target_label} | Besar/Kecil | Genap/Ganjil

Analisis yang harus dilakukan:
1. Hitung frekuensi BESAR vs KECIL dari {n} periode terakhir
2. Hitung frekuensi GENAP vs GANJIL dari {n} periode terakhir
3. Analisis streak/pola terakhir (apakah ada pola berturut-turut?)
4. Analisis 10 dan 20 periode terakhir (trend terkini)
5. Identifikasi apakah ada bias signifikan
6. Berikan confidence yang realistis, bukan asal tinggi
7. Jangan keras kepala mengikuti trend bila statistik lokal menunjukkan pola zig-zag atau mean reversion
8. Jangan memaksa zig-zag jika continuation/trend secara statistik lebih kuat

Aturan interpretasi:
- jika sinyal saling bentrok, confidence harus turun
- jika edge tipis, confidence idealnya dekat 0.50-0.59
- gunakan confidence >= 0.60 hanya bila beberapa sinyal mendukung arah yang sama
- reason harus menyebut apakah basisnya trend, zig-zag, transition, atau mean reversion

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

Ringkasan statistik lokal per posisi:
{signal_summary}

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
- jangan keras kepala mengikuti satu pola jika summary lokal menunjukkan konflik trend vs zig-zag

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
        signal_summary = self._build_signal_summary_for_target(history[:HISTORY_WINDOW], BET_TARGET)

        prompt = _PROMPT_TEMPLATE.format(
            n=n,
            history_table=history_table,
            target_label=BET_TARGET.title(),
            signal_summary=signal_summary,
        )

        result = await self._call_llm_json(LLM_PRIMARY, prompt)
        if result is None:
            logger.warning("Model utama gagal, coba fallback")
            result = await self._call_llm_json(LLM_FALLBACK, prompt)

        if result is None:
            return self._heuristic_prediction(history, BET_TARGET)
        parsed = self._parse_response(result)
        if parsed is None:
            return self._heuristic_prediction(history, BET_TARGET)
        return self._ensemble_prediction(parsed, self._heuristic_prediction(history, BET_TARGET))

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
        signal_summary = self._build_signal_summary_for_all_targets(history[:HISTORY_WINDOW])
        prompt = _FLEET_PROMPT_TEMPLATE.format(
            fleet_status="\n".join(status_lines) or "-",
            n=len(history[:HISTORY_WINDOW]),
            history_4d=history_4d,
            signal_summary=signal_summary,
        )

        result = await self._call_llm_json(LLM_PRIMARY, prompt)
        if result is None:
            logger.warning("Model utama fleet gagal, coba fallback")
            result = await self._call_llm_json(LLM_FALLBACK, prompt)

        if result is None:
            return None

        parsed = self._parse_fleet_response(result)
        if parsed is None:
            return None
        return self._ensemble_fleet_prediction(parsed, history)

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
                    "confidence": self._normalize_confidence(bk_data.get("confidence", 0.5)),
                    "reason": str(bk_data.get("reason", "")),
                },
                "genap_ganjil": {
                    "choice": gj_choice if gj_choice in ("GE", "GA") else "GE",
                    "confidence": self._normalize_confidence(gj_data.get("confidence", 0.5)),
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
                "confidence": self._normalize_confidence(bk_data.get("confidence", 0.5)),
                "reason":     str(bk_data.get("reason", "")),
            },
            "genap_ganjil": {
                "choice":     gj_choice,
                "confidence": self._normalize_confidence(gj_data.get("confidence", 0.5)),
                "reason":     str(gj_data.get("reason", "")),
            },
            "stats": data.get("stats", {}),
        }

    @staticmethod
    def _normalize_confidence(value: float | int | str) -> float:
        try:
            conf = float(value)
        except (TypeError, ValueError):
            conf = 0.5
        return max(0.5, min(0.85, conf))

    def _build_signal_summary_for_all_targets(self, history: list[dict]) -> str:
        parts = []
        for target in ("depan", "tengah", "belakang"):
            parts.append(f"[{target.upper()}]\n{self._build_signal_summary_for_target(history, target)}")
        return "\n\n".join(parts)

    def _build_signal_summary_for_target(self, history: list[dict], target: str) -> str:
        target_rows = self._extract_target_rows(history, target)
        bk_seq = [row["besar_kecil"] for row in target_rows]
        gj_seq = [row["genap_ganjil"] for row in target_rows]
        bk_sig = self._compute_dimension_signal(bk_seq, ("BE", "KE"))
        gj_sig = self._compute_dimension_signal(gj_seq, ("GE", "GA"))
        return (
            f"BK -> {self._format_signal_summary(bk_sig)}\n"
            f"GJ -> {self._format_signal_summary(gj_sig)}"
        )

    def _format_signal_summary(self, sig: dict) -> str:
        return (
            f"overall {sig['counts']} | recent20 {sig['recent20']} | recent10 {sig['recent10']} | "
            f"last={sig['last']} streak={sig['streak']} | alt8={sig['alt8']:.2f} | "
            f"continue={sig['continue_rate']:.2f} flip={sig['flip_rate']:.2f} | "
            f"heuristic={sig['choice']}@{sig['confidence']:.2f} ({sig['mode']})"
        )

    def _extract_target_rows(self, history: list[dict], target: str) -> list[dict]:
        rows = []
        for h in reversed(history):
            if "target_number_2d" in h and h.get("target_position") == target:
                rows.append({
                    "number_2d": h["target_number_2d"],
                    "besar_kecil": h["target_bk"],
                    "genap_ganjil": h["target_gj"],
                })
                continue

            result_raw = str(h.get("result", h.get("full_number", ""))).strip()
            parsed = parse_result_full(result_raw)
            if not parsed:
                continue
            target_data = get_target_result(parsed, target)
            rows.append(target_data)
        return rows

    def _heuristic_prediction(self, history: list[dict], target: str) -> dict:
        rows = self._extract_target_rows(history, target)
        bk_seq = [row["besar_kecil"] for row in rows]
        gj_seq = [row["genap_ganjil"] for row in rows]
        bk_sig = self._compute_dimension_signal(bk_seq, ("BE", "KE"))
        gj_sig = self._compute_dimension_signal(gj_seq, ("GE", "GA"))
        return {
            "besar_kecil": {
                "choice": bk_sig["choice"],
                "confidence": bk_sig["confidence"],
                "reason": f"heuristic:{bk_sig['mode']}",
            },
            "genap_ganjil": {
                "choice": gj_sig["choice"],
                "confidence": gj_sig["confidence"],
                "reason": f"heuristic:{gj_sig['mode']}",
            },
            "stats": {
                "bk_signal": bk_sig,
                "gj_signal": gj_sig,
            },
        }

    def _compute_dimension_signal(self, seq: list[str], choices: tuple[str, str]) -> dict:
        if not seq:
            return {
                "choice": choices[0],
                "confidence": 0.5,
                "counts": {},
                "recent20": {},
                "recent10": {},
                "last": "?",
                "streak": 0,
                "alt8": 0.0,
                "continue_rate": 0.5,
                "flip_rate": 0.5,
                "mode": "neutral",
            }

        a, b = choices
        counts = {a: seq.count(a), b: seq.count(b)}
        recent20_seq = seq[-20:]
        recent10_seq = seq[-10:]
        recent5_seq = seq[-5:]
        recent20 = {a: recent20_seq.count(a), b: recent20_seq.count(b)}
        recent10 = {a: recent10_seq.count(a), b: recent10_seq.count(b)}
        recent5 = {a: recent5_seq.count(a), b: recent5_seq.count(b)}

        last = seq[-1]
        streak = 1
        for item in reversed(seq[:-1]):
            if item == last:
                streak += 1
            else:
                break

        recent8 = seq[-8:]
        changes = sum(1 for i in range(1, len(recent8)) if recent8[i] != recent8[i - 1])
        alt8 = changes / max(1, len(recent8) - 1)

        same_after_last = 0
        flip_after_last = 0
        trigger_count = 0
        for i in range(len(seq) - 1):
            if seq[i] == last:
                trigger_count += 1
                if seq[i + 1] == last:
                    same_after_last += 1
                else:
                    flip_after_last += 1
        continue_rate = same_after_last / trigger_count if trigger_count else 0.5
        flip_rate = flip_after_last / trigger_count if trigger_count else 0.5

        score = {a: 0.0, b: 0.0}

        def apply_bias(window_counts: dict, weight: float) -> None:
            diff = window_counts[a] - window_counts[b]
            total = max(1, window_counts[a] + window_counts[b])
            bias = diff / total
            score[a] += bias * weight
            score[b] -= bias * weight

        apply_bias(counts, 0.10)
        apply_bias(recent20, 0.18)
        apply_bias(recent10, 0.24)
        apply_bias(recent5, 0.20)

        opposite = b if last == a else a
        if alt8 >= 0.70:
            score[opposite] += 0.16
            mode = "zigzag"
        elif continue_rate >= 0.58:
            score[last] += 0.16
            mode = "trend"
        else:
            mode = "mixed"

        if streak >= 3 and continue_rate < 0.55:
            score[opposite] += min(0.18, 0.06 * streak)
            mode = "mean_reversion"
        elif streak >= 3 and continue_rate >= 0.60:
            score[last] += min(0.18, 0.05 * streak)
            mode = "trend"

        diff = score[a] - score[b]
        choice = a if diff >= 0 else b
        edge = min(0.28, abs(diff))
        confidence = max(0.5, min(0.78, 0.5 + edge))

        return {
            "choice": choice,
            "confidence": round(confidence, 3),
            "counts": counts,
            "recent20": recent20,
            "recent10": recent10,
            "last": last,
            "streak": streak,
            "alt8": round(alt8, 2),
            "continue_rate": round(continue_rate, 2),
            "flip_rate": round(flip_rate, 2),
            "mode": mode,
        }

    def _ensemble_prediction(self, llm: dict, heuristic: dict) -> dict:
        return {
            "besar_kecil": self._merge_dimension(llm["besar_kecil"], heuristic["besar_kecil"]),
            "genap_ganjil": self._merge_dimension(llm["genap_ganjil"], heuristic["genap_ganjil"]),
            "stats": {
                "llm": llm.get("stats", {}),
                "heuristic": heuristic.get("stats", {}),
            },
        }

    def _ensemble_fleet_prediction(self, llm_plan: dict, history: list[dict]) -> dict:
        bots = {}
        for bot_name, item in (llm_plan.get("bots") or {}).items():
            target = item.get("target")
            if target not in ("depan", "tengah", "belakang"):
                bots[bot_name] = item
                continue
            heuristic = self._heuristic_prediction(history, target)
            merged_bk = self._merge_dimension(item["besar_kecil"], heuristic["besar_kecil"])
            merged_gj = self._merge_dimension(item["genap_ganjil"], heuristic["genap_ganjil"])
            action = item["action"]
            if max(merged_bk["confidence"], merged_gj["confidence"]) < 0.60:
                action = "SKIP"
            bots[bot_name] = {
                **item,
                "action": action,
                "besar_kecil": merged_bk,
                "genap_ganjil": merged_gj,
            }
        return {
            **llm_plan,
            "bots": bots,
        }

    def _merge_dimension(self, llm_dim: dict, heuristic_dim: dict) -> dict:
        llm_choice = llm_dim["choice"]
        heu_choice = heuristic_dim["choice"]
        llm_conf = self._normalize_confidence(llm_dim.get("confidence", 0.5))
        heu_conf = self._normalize_confidence(heuristic_dim.get("confidence", 0.5))

        if llm_choice == heu_choice:
            final_choice = llm_choice
            final_conf = max(llm_conf, heu_conf) * 0.55 + min(llm_conf, heu_conf) * 0.45
            reason_mode = "aligned"
        else:
            llm_edge = llm_conf - 0.5
            heu_edge = heu_conf - 0.5
            if heu_edge >= llm_edge + 0.05:
                final_choice = heu_choice
                final_conf = 0.5 + max(0.0, heu_edge * 0.70 - llm_edge * 0.20)
                reason_mode = "heuristic_override"
            elif llm_edge >= heu_edge + 0.08:
                final_choice = llm_choice
                final_conf = 0.5 + max(0.0, llm_edge * 0.60 - heu_edge * 0.25)
                reason_mode = "llm_override"
            else:
                final_choice = heu_choice if heu_conf >= llm_conf else llm_choice
                final_conf = 0.5 + max(0.0, abs(llm_edge - heu_edge) * 0.25)
                reason_mode = "conflict"

        final_conf = round(max(0.5, min(0.82, final_conf)), 3)
        return {
            "choice": final_choice,
            "confidence": final_conf,
            "reason": f"{reason_mode} | llm={llm_dim.get('reason', '')} | {heuristic_dim.get('reason', '')}",
        }
