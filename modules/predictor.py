"""LLM predictor for 3 positions x 2 dimensions with global ranking."""

import json
import logging
import re
from typing import Optional

from openai import AsyncOpenAI

from config import (
    DIMENSIONS,
    HISTORY_WINDOW,
    KNOWLEDGE_BASE_HISTORY_LIMIT,
    LLM_FALLBACK,
    LLM_MAX_TOKENS,
    LLM_PRIMARY,
    LLM_TEMPERATURE,
    OPENROUTER_API_KEY,
    OPENROUTER_BASE_URL,
    POSITIONS,
    PREDICTION_EVAL_WINDOW,
)
from modules.categories import get_target_result, parse_result_full
from modules import database as db

logger = logging.getLogger(__name__)

_PROMPT_TEMPLATE = """Kamu adalah analis statistik togel Hokidraw.
Analisis 3 posisi 2D sekaligus: depan, tengah, belakang.

Data {n} periode terakhir hasil 4D:
{history_table}

Ringkasan statistik lokal per posisi:
{signal_summary}

Evaluasi akurasi prediksi historis bot:
{feedback_summary}

Knowledge base historis jangka menengah:
{knowledge_base_summary}

Tugasmu:
1. Untuk setiap posisi (depan, tengah, belakang), beri prediksi untuk:
   - besar_kecil
   - genap_ganjil
2. Berikan confidence realistis untuk masing-masing dari 6 kandidat
3. Buat ranking global dari 6 kandidat, confidence tertinggi ke terendah
4. Jangan terlalu percaya diri jika sinyal bertabrakan atau riwayat akurasi slot buruk
5. Reason singkat harus menyebut basis utamanya: trend, zigzag, mean reversion, continuation, atau mixed
6. Jika edge tipis atau riwayat slot buruk, confidence idealnya dekat 0.50-0.59
7. Gunakan confidence >= 0.60 hanya bila beberapa sinyal mendukung arah yang sama
8. Bandingkan keenam kandidat secara langsung, jangan beri confidence yang terlalu rapat jika kualitas sinyalnya jelas berbeda
9. Kandidat terbaik boleh unggul tipis, tetapi jangan buat selisih besar tanpa alasan statistik yang jelas
10. Jika dua kandidat sama-sama lemah atau saling bertabrakan, turunkan keduanya mendekati 0.50-0.56

Aturan klasifikasi:
- besar/kecil: 00-49 = KE, 50-99 = BE
- genap/ganjil: digit terakhir genap = GE, ganjil = GA

Balas HANYA JSON dengan format:
{{
  "positions": {{
    "depan": {{
      "besar_kecil": {{"choice":"BE|KE","confidence":0.XX,"reason":"..."}},
      "genap_ganjil": {{"choice":"GE|GA","confidence":0.XX,"reason":"..."}}
    }},
    "tengah": {{
      "besar_kecil": {{"choice":"BE|KE","confidence":0.XX,"reason":"..."}},
      "genap_ganjil": {{"choice":"GE|GA","confidence":0.XX,"reason":"..."}}
    }},
    "belakang": {{
      "besar_kecil": {{"choice":"BE|KE","confidence":0.XX,"reason":"..."}},
      "genap_ganjil": {{"choice":"GE|GA","confidence":0.XX,"reason":"..."}}
    }}
  }},
  "ranking": [
    {{"slot":"depan_bk","target":"depan","dimension":"besar_kecil","choice":"BE|KE","confidence":0.XX}},
    {{"slot":"depan_gj","target":"depan","dimension":"genap_ganjil","choice":"GE|GA","confidence":0.XX}}
  ],
  "global_note": "catatan singkat"
}}"""

_KNOWLEDGE_BASE_PROMPT = """Kamu sedang membangun knowledge base untuk bot prediksi Hokidraw.
Gunakan 400 history draw berikut sebagai bahan belajar jangka menengah.

Data {n} periode hasil 4D:
{history_table}

Tugasmu:
1. Temukan pola yang cukup stabil atau berulang untuk posisi depan, tengah, belakang.
2. Untuk masing-masing posisi, evaluasi dua dimensi:
   - besar_kecil
   - genap_ganjil
3. Bedakan antara pola yang cukup berguna dan pola yang lemah/menipu.
4. Tulis ringkasan knowledge base yang padat, operasional, dan bisa dipakai ulang pada prediksi berikutnya.
5. Jangan mengklaim kepastian. Jika pola lemah, katakan lemah.

Balas HANYA JSON dengan format:
{{
  "summary_text": "ringkasan maksimum 16 baris, singkat dan operasional",
  "global_patterns": ["...", "..."],
  "positions": {{
    "depan": {{
      "besar_kecil": {{"bias":"BE|KE|NETRAL","strength":"lemah|sedang|kuat","note":"..." }},
      "genap_ganjil": {{"bias":"GE|GA|NETRAL","strength":"lemah|sedang|kuat","note":"..." }}
    }},
    "tengah": {{
      "besar_kecil": {{"bias":"BE|KE|NETRAL","strength":"lemah|sedang|kuat","note":"..." }},
      "genap_ganjil": {{"bias":"GE|GA|NETRAL","strength":"lemah|sedang|kuat","note":"..." }}
    }},
    "belakang": {{
      "besar_kecil": {{"bias":"BE|KE|NETRAL","strength":"lemah|sedang|kuat","note":"..." }},
      "genap_ganjil": {{"bias":"GE|GA|NETRAL","strength":"lemah|sedang|kuat","note":"..." }}
    }}
  }},
  "dos": ["..."],
  "donts": ["..."]
}}"""


class Predictor:
    def __init__(self) -> None:
        self._client = AsyncOpenAI(api_key=OPENROUTER_API_KEY, base_url=OPENROUTER_BASE_URL)

    async def analyze(self, history: list[dict]) -> Optional[dict]:
        if not history:
            logger.warning("History kosong")
            return None

        trimmed = history[:HISTORY_WINDOW]
        feedback_map = await self._get_feedback_map()
        prompt = _PROMPT_TEMPLATE.format(
            n=len(trimmed),
            history_table=self._build_4d_table(trimmed),
            signal_summary=self._build_signal_summary_for_all_targets(trimmed),
            feedback_summary=await self._build_feedback_summary(),
            knowledge_base_summary=await self._build_knowledge_base_summary(),
        )

        result = await self._call_llm_json(LLM_PRIMARY, prompt)
        if result is None:
            logger.warning("Model utama gagal, coba fallback")
            result = await self._call_llm_json(LLM_FALLBACK, prompt)

        heuristic = self._heuristic_prediction(trimmed)
        if result is None:
            return self._apply_feedback_adjustments(heuristic, feedback_map)

        parsed = self._parse_response(result)
        if parsed is None:
            return self._apply_feedback_adjustments(heuristic, feedback_map)
        return self._apply_feedback_adjustments(self._ensemble_prediction(parsed, heuristic), feedback_map)

    async def rebuild_knowledge_base(self, history: list[dict], source: str = "manual") -> Optional[dict]:
        if not history:
            logger.warning("Knowledge base build gagal: history kosong")
            return None

        trimmed = history[:KNOWLEDGE_BASE_HISTORY_LIMIT]
        prompt = _KNOWLEDGE_BASE_PROMPT.format(
            n=len(trimmed),
            history_table=self._build_4d_table(trimmed),
        )

        result = await self._call_llm_json(LLM_PRIMARY, prompt)
        model_used = LLM_PRIMARY
        if result is None:
            logger.warning("Knowledge base model utama gagal, coba fallback")
            result = await self._call_llm_json(LLM_FALLBACK, prompt)
            model_used = LLM_FALLBACK
        if result is None:
            return None

        parsed = self._parse_knowledge_base_response(result)
        if parsed is None:
            return None

        periods = [
            str(item.get("period") or item.get("periode") or "").strip()
            for item in trimmed
            if str(item.get("period") or item.get("periode") or "").strip()
        ]
        period_to = periods[0] if periods else "-"
        period_from = periods[-1] if periods else "-"
        snapshot_id = await db.save_knowledge_base_snapshot(
            source_count=len(trimmed),
            period_from=period_from,
            period_to=period_to,
            summary_text=parsed["summary_text"],
            knowledge_json=json.dumps(parsed, ensure_ascii=True),
            model=model_used,
            source=source,
        )
        return {
            "id": snapshot_id,
            "source_count": len(trimmed),
            "period_from": period_from,
            "period_to": period_to,
            "summary_text": parsed["summary_text"],
            "model": model_used,
            "knowledge": parsed,
        }

    async def _build_feedback_summary(self) -> str:
        feedback = await db.get_prediction_feedback(PREDICTION_EVAL_WINDOW)
        if not feedback:
            return "Belum ada feedback historis."
        lines = []
        for item in feedback:
            total = int(item.get("total", 0) or 0)
            wins = int(item.get("wins", 0) or 0)
            acc = wins / total if total else 0.0
            avg_conf = float(item.get("avg_confidence", 0.0) or 0.0)
            lines.append(f"{item['slot']}: acc={acc:.0%} ({wins}/{total}) avg_conf={avg_conf:.2f}")
        return "\n".join(lines)

    async def _build_knowledge_base_summary(self) -> str:
        kb = await db.get_active_knowledge_base()
        if not kb:
            return "Belum ada knowledge base manual. Gunakan command Telegram untuk build dari 400 history."
        return (
            f"KB aktif ({kb['source_count']} hasil, period {kb['period_from']} -> {kb['period_to']}):\n"
            f"{kb['summary_text']}"
        )

    async def _get_feedback_map(self) -> dict[str, dict]:
        feedback = await db.get_prediction_feedback(PREDICTION_EVAL_WINDOW)
        mapping: dict[str, dict] = {}
        for item in feedback:
            total = int(item.get("total", 0) or 0)
            wins = int(item.get("wins", 0) or 0)
            mapping[item["slot"]] = {
                "total": total,
                "wins": wins,
                "accuracy": (wins / total) if total else 0.0,
                "avg_confidence": float(item.get("avg_confidence", 0.0) or 0.0),
            }
        return mapping

    def _build_4d_table(self, history: list[dict]) -> str:
        rows = []
        for h in history:
            period = h.get("period") or h.get("periode") or "?"
            raw = str(h.get("result", h.get("full_number", ""))).strip()
            parsed = parse_result_full(raw)
            if parsed:
                rows.append(
                    f"{period} | {parsed['full']} | depan={parsed['depan']} | "
                    f"tengah={parsed['tengah']} | belakang={parsed['belakang']}"
                )
            else:
                rows.append(f"{period} | {raw}")
        return "\n".join(rows)

    async def _call_llm_json(self, model: str, prompt: str) -> Optional[dict]:
        try:
            response = await self._client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                temperature=LLM_TEMPERATURE,
                max_tokens=LLM_MAX_TOKENS,
            )
            if not response.choices or response.choices[0].message is None:
                return None
            content = self._coerce_message_content(response.choices[0].message.content)
            if not content:
                return None
            return self._extract_json(content)
        except Exception as exc:
            logger.error("LLM call gagal (%s): %s", model, exc)
            return None

    def _coerce_message_content(self, content: object) -> str:
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            parts = []
            for item in content:
                if isinstance(item, str) and item.strip():
                    parts.append(item.strip())
                elif isinstance(item, dict):
                    text = item.get("text")
                    if isinstance(text, str) and text.strip():
                        parts.append(text.strip())
                else:
                    text = getattr(item, "text", None)
                    if isinstance(text, str) and text.strip():
                        parts.append(text.strip())
            return "\n".join(parts).strip()
        return ""

    def _extract_json(self, content: str) -> Optional[dict]:
        content = re.sub(r"```(?:json)?\s*", "", content).strip().rstrip("`").strip()
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", content, re.DOTALL)
            if not match:
                return None
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                return None

    def _parse_response(self, data: dict) -> Optional[dict]:
        if not isinstance(data, dict):
            return None
        positions_data = data.get("positions")
        if not isinstance(positions_data, dict):
            return None

        cleaned_positions = {}
        for target in POSITIONS:
            item = positions_data.get(target, {})
            if not isinstance(item, dict):
                item = {}
            cleaned_positions[target] = {
                "besar_kecil": self._clean_dimension(item.get("besar_kecil"), "besar_kecil"),
                "genap_ganjil": self._clean_dimension(item.get("genap_ganjil"), "genap_ganjil"),
            }

        cleaned_ranking = self._build_ranking_from_positions(cleaned_positions)
        return {
            "positions": cleaned_positions,
            "ranking": cleaned_ranking,
            "global_note": str(data.get("global_note", "")),
        }

    def _parse_knowledge_base_response(self, data: dict) -> Optional[dict]:
        if not isinstance(data, dict):
            return None
        positions = data.get("positions")
        if not isinstance(positions, dict):
            return None
        summary_text = str(data.get("summary_text", "")).strip()
        if not summary_text:
            return None

        cleaned_positions: dict[str, dict] = {}
        for target in POSITIONS:
            item = positions.get(target, {})
            if not isinstance(item, dict):
                item = {}
            cleaned_positions[target] = {
                "besar_kecil": self._clean_kb_dimension(item.get("besar_kecil"), ("BE", "KE")),
                "genap_ganjil": self._clean_kb_dimension(item.get("genap_ganjil"), ("GE", "GA")),
            }

        return {
            "summary_text": summary_text,
            "global_patterns": self._coerce_string_list(data.get("global_patterns")),
            "positions": cleaned_positions,
            "dos": self._coerce_string_list(data.get("dos")),
            "donts": self._coerce_string_list(data.get("donts")),
        }

    def _clean_kb_dimension(self, data: object, allowed_biases: tuple[str, str]) -> dict:
        if not isinstance(data, dict):
            data = {}
        bias = str(data.get("bias", "NETRAL")).upper()
        if bias not in (*allowed_biases, "NETRAL"):
            bias = "NETRAL"
        strength = str(data.get("strength", "lemah")).lower()
        if strength not in ("lemah", "sedang", "kuat"):
            strength = "lemah"
        return {
            "bias": bias,
            "strength": strength,
            "note": str(data.get("note", "")).strip(),
        }

    def _coerce_string_list(self, value: object) -> list[str]:
        if not isinstance(value, list):
            return []
        return [str(item).strip() for item in value if str(item).strip()]

    def _clean_dimension(self, data: object, dimension: str) -> dict:
        allowed = ("BE", "KE") if dimension == "besar_kecil" else ("GE", "GA")
        if not isinstance(data, dict):
            data = {}
        choice = str(data.get("choice", allowed[0])).upper()
        if choice not in allowed:
            choice = allowed[0]
        return {
            "choice": choice,
            "confidence": self._normalize_confidence(data.get("confidence", 0.5)),
            "reason": str(data.get("reason", "")),
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
        for target in POSITIONS:
            rows = self._extract_target_rows(history, target)
            bk_sig = self._compute_dimension_signal([row["besar_kecil"] for row in rows], ("BE", "KE"))
            gj_sig = self._compute_dimension_signal([row["genap_ganjil"] for row in rows], ("GE", "GA"))
            parts.append(
                f"[{target.upper()}]\n"
                f"BK -> {self._format_signal_summary(bk_sig)}\n"
                f"GJ -> {self._format_signal_summary(gj_sig)}"
            )
        return "\n\n".join(parts)

    def _format_signal_summary(self, sig: dict) -> str:
        return (
            f"overall={sig['counts']} recent20={sig['recent20']} recent10={sig['recent10']} "
            f"last={sig['last']} streak={sig['streak']} alt8={sig['alt8']:.2f} "
            f"continue={sig['continue_rate']:.2f} flip={sig['flip_rate']:.2f} "
            f"heuristic={sig['choice']}@{sig['confidence']:.2f} ({sig['mode']})"
        )

    def _extract_target_rows(self, history: list[dict], target: str) -> list[dict]:
        rows = []
        for h in reversed(history):
            if "full_number" in h and f"{target}_bk" in h:
                rows.append({
                    "number_2d": h[f"{target}_number_2d"],
                    "besar_kecil": h[f"{target}_bk"],
                    "genap_ganjil": h[f"{target}_gj"],
                })
                continue
            raw = str(h.get("result", h.get("full_number", ""))).strip()
            parsed = parse_result_full(raw)
            if not parsed:
                continue
            rows.append(get_target_result(parsed, target))
        return rows

    def _heuristic_prediction(self, history: list[dict]) -> dict:
        positions = {}
        for target in POSITIONS:
            rows = self._extract_target_rows(history, target)
            bk_sig = self._compute_dimension_signal([row["besar_kecil"] for row in rows], ("BE", "KE"))
            gj_sig = self._compute_dimension_signal([row["genap_ganjil"] for row in rows], ("GE", "GA"))
            positions[target] = {
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
            }
        return {
            "positions": positions,
            "ranking": self._build_ranking_from_positions(positions),
            "global_note": "heuristic_fallback",
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
        for idx in range(len(seq) - 1):
            if seq[idx] == last:
                trigger_count += 1
                if seq[idx + 1] == last:
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
        merged_positions = {}
        for target in POSITIONS:
            merged_positions[target] = {}
            for dimension in DIMENSIONS:
                merged_positions[target][dimension] = self._merge_dimension(
                    llm["positions"][target][dimension],
                    heuristic["positions"][target][dimension],
                )
        return {
            "positions": merged_positions,
            "ranking": self._build_ranking_from_positions(merged_positions),
            "global_note": llm.get("global_note", ""),
        }

    def _apply_feedback_adjustments(self, prediction: dict, feedback_map: dict[str, dict]) -> dict:
        adjusted_positions = {}
        for target in POSITIONS:
            adjusted_positions[target] = {}
            for dimension in DIMENSIONS:
                slot = f"{target}_{'bk' if dimension == 'besar_kecil' else 'gj'}"
                adjusted_positions[target][dimension] = self._apply_feedback_to_dimension(
                    prediction["positions"][target][dimension],
                    feedback_map.get(slot),
                )
        return {
            "positions": adjusted_positions,
            "ranking": self._build_ranking_from_positions(adjusted_positions),
            "global_note": prediction.get("global_note", ""),
        }

    def _apply_feedback_to_dimension(self, data: dict, feedback: dict | None) -> dict:
        if not feedback or feedback.get("total", 0) < 5:
            return data

        confidence = self._normalize_confidence(data.get("confidence", 0.5))
        accuracy = float(feedback.get("accuracy", 0.0))
        avg_conf = float(feedback.get("avg_confidence", 0.0))
        penalty = 0.0
        reward = 0.0

        if accuracy < 0.45:
            penalty += min(0.08, (0.45 - accuracy) * 0.25)
        if avg_conf > 0 and (avg_conf - accuracy) > 0.12:
            penalty += min(0.06, (avg_conf - accuracy) * 0.20)
        if accuracy >= 0.60:
            reward += min(0.03, (accuracy - 0.60) * 0.10)
        if avg_conf > 0 and accuracy > avg_conf + 0.08:
            reward += min(0.02, (accuracy - avg_conf) * 0.10)

        delta = reward - penalty
        if abs(delta) < 0.001:
            return data

        adjusted = max(0.5, min(0.82, confidence + delta))
        note = f"feedback_adj(acc={accuracy:.0%},avg={avg_conf:.2f},delta={delta:+.2f})"
        return {
            **data,
            "confidence": round(adjusted, 3),
            "reason": f"{data.get('reason', '')} | {note}",
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

        return {
            "choice": final_choice,
            "confidence": round(max(0.5, min(0.82, final_conf)), 3),
            "reason": f"{reason_mode} | llm={llm_dim.get('reason', '')} | {heuristic_dim.get('reason', '')}",
        }

    def _build_ranking_from_positions(self, positions: dict) -> list[dict]:
        ranking = []
        for target in POSITIONS:
            for dimension in DIMENSIONS:
                data = positions[target][dimension]
                slot = f"{target}_{'bk' if dimension == 'besar_kecil' else 'gj'}"
                ranking.append({
                    "slot": slot,
                    "target": target,
                    "dimension": dimension,
                    "choice": data["choice"],
                    "confidence": self._normalize_confidence(data["confidence"]),
                    "reason": data.get("reason", ""),
                })
        ranking.sort(key=lambda item: (-item["confidence"], item["slot"]))
        return ranking
