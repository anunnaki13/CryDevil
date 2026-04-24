"""LLM predictor for 3 positions x 2 dimensions with global ranking."""

import json
import logging
import re
from typing import Optional

from openai import AsyncOpenAI

from config import (
    ADAPTIVE_SELECTION_WINDOW,
    DIMENSIONS,
    HISTORY_WINDOW,
    KNOWLEDGE_BASE_HISTORY_LIMIT,
    LLM_FALLBACK,
    LLM_MAX_TOKENS,
    LLM_PRIMARY,
    LLM_TEMPERATURE,
    LOW_CONFIDENCE_CUTOFF,
    OPENROUTER_API_KEY,
    OPENROUTER_BASE_URL,
    POSITIONS,
    PREDICTION_EVAL_WINDOW,
)
from modules.categories import get_target_result, parse_result_full
from modules import database as db

logger = logging.getLogger(__name__)

_PROMPT_TEMPLATE = """Kamu adalah engine analisis probabilistik Hokidraw.
Scope analisis aktif: {scope_label}.
Fokus HANYA pada posisi berikut: {active_targets_label}.

Data {n} periode terakhir hasil 4D:
{history_table}

Ringkasan statistik lokal per posisi:
{signal_summary}

Evaluasi akurasi prediksi historis bot:
{feedback_summary}

Pola fleksibilitas selection historis:
{adaptive_summary}

Knowledge base historis jangka menengah:
{knowledge_base_summary}

Tujuan:
- membaca pola yang noisy, acak, atau berubah regime
- tidak kaku mengikuti tren pendek
- membandingkan zigzag, trend, mean reversion, continuation, mixed, dan chaotic
- menghasilkan confidence yang jujur, bukan optimistis

Urutan berpikir yang WAJIB:
1. Tentukan regime lebih dulu untuk setiap kandidat:
   zigzag | trend | mean_reversion | continuation | mixed | chaotic
2. Nilai apakah edge kandidat benar-benar ada, tipis, atau tidak ada.
3. Bandingkan kandidat aktif secara langsung, jangan nilai per slot secara terpisah tanpa pembanding.
4. Jika sinyal saling bertabrakan, histori slot buruk, atau data terlalu ambigu, tekan confidence.

Aturan keputusan:
1. Jangan terpaku pada dominasi 5 atau 10 periode terakhir saja.
2. Jika flip_rate tinggi dan pola bolak-balik konsisten, pertimbangkan zigzag.
3. Jika continuation kuat dan streak masih sehat, pertimbangkan trend/continuation.
4. Jika streak panjang tetapi follow-through lemah, pertimbangkan mean reversion.
5. Jika flip dan continue sama-sama kuat atau sama-sama lemah, anggap mixed atau chaotic.
6. Jika kondisi chaotic/noisy, confidence idealnya 0.50-0.56.
7. Gunakan confidence >= 0.60 hanya jika beberapa sinyal utama searah.
8. Gunakan confidence >= 0.66 hanya bila edge sangat jelas dan histori slot tidak buruk.
9. Jika histori slot jelek atau adaptive diagnostics menunjukkan slot sering overconfident, turunkan confidence.
10. Kandidat terbaik boleh unggul tipis, tetapi jangan membuat selisih besar tanpa alasan statistik yang kuat.
11. Jika dua kandidat sama-sama lemah, turunkan keduanya; jangan memaksa ada kandidat tinggi.

Aturan alasan:
- reason harus singkat, operasional, dan menyebut basis utamanya
- reason harus menyebut salah satu: zigzag, trend, mean_reversion, continuation, mixed, chaotic
- hindari alasan generik seperti "peluang bagus" atau "analisis kuat" tanpa basis

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
  "global_note": "metode/ringkasan singkat"
}}"""

_KNOWLEDGE_BASE_PROMPT = """Kamu sedang membangun knowledge base untuk bot prediksi Hokidraw.
Gunakan history draw berikut sebagai bahan belajar jangka menengah.
Scope knowledge base aktif: {scope_label}.
Fokus HANYA pada posisi berikut: {active_targets_label}.

Data {n} periode hasil 4D:
{history_table}

Tugasmu:
1. Temukan pola yang cukup stabil atau berulang hanya untuk posisi dalam scope aktif.
2. Untuk masing-masing posisi dalam scope aktif, evaluasi dua dimensi:
   - besar_kecil
   - genap_ganjil
3. Bedakan antara pola yang cukup berguna dan pola yang lemah/menipu.
4. Tulis ringkasan knowledge base yang padat, operasional, dan bisa dipakai ulang pada prediksi berikutnya.
5. Jangan mengklaim kepastian. Jika pola lemah, katakan lemah.
6. JANGAN menulis disclaimer umum seperti "gunakan hati-hati", "tidak menjamin", atau "kombinasikan dengan metode lain".
7. Summary harus langsung siap pakai untuk keputusan bet, bukan penjelasan umum.
8. Summary harus menyebut untuk setiap posisi aktif:
   - bias BK: BE/KE/NETRAL + strength
   - bias GJ: GE/GA/NETRAL + strength
   - catatan operasional singkat
9. Jika scope hanya satu posisi, fokus penuh pada posisi itu dan jangan bahas posisi lain.

Balas HANYA JSON dengan format:
{{
  "summary_text": "maksimum 10 baris, format taktis, tanpa disclaimer umum",
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

    async def analyze(self, history: list[dict], scope: str = "all", strategy_mode: str = "auto") -> Optional[dict]:
        if not history:
            logger.warning("History kosong")
            return None

        active_targets = self._resolve_active_targets(scope)
        trimmed = history[:HISTORY_WINDOW]
        normalized_mode = self._normalize_strategy_mode(strategy_mode)

        if normalized_mode == "auto":
            return await self._auto_prediction(trimmed, active_targets, scope)

        return await self._run_strategy_prediction(normalized_mode, trimmed, active_targets, scope)

    async def rebuild_knowledge_base(
        self,
        history: list[dict],
        source: str = "manual",
        scope: str = "all",
    ) -> Optional[dict]:
        if not history:
            logger.warning("Knowledge base build gagal: history kosong")
            return None

        active_targets = self._resolve_active_targets(scope)
        trimmed = history[:KNOWLEDGE_BASE_HISTORY_LIMIT]
        prompt = _KNOWLEDGE_BASE_PROMPT.format(
            n=len(trimmed),
            scope_label=self._format_scope_label(active_targets),
            active_targets_label=", ".join(active_targets),
            history_table=self._build_4d_table(trimmed),
        )

        result = await self._call_llm_json(LLM_PRIMARY, prompt)
        model_used = LLM_PRIMARY
        if result is None:
            logger.warning("Knowledge base model utama gagal, coba fallback")
            result = await self._call_llm_json(LLM_FALLBACK, prompt)
            model_used = LLM_FALLBACK
        if result is None:
            logger.error("Knowledge base build fallback ke local summary: kedua model tidak menghasilkan JSON yang valid")
            parsed = self._build_local_knowledge_base(trimmed, active_targets)
            model_used = "local_fallback"
        else:
            parsed = self._parse_knowledge_base_response(result)
            if parsed is None:
                logger.error(
                    "Knowledge base build gagal: respons JSON tidak sesuai format KB. keys=%s",
                    list(result.keys()) if isinstance(result, dict) else type(result).__name__,
                )
                parsed = self._build_local_knowledge_base(trimmed, active_targets)
                model_used = f"{model_used}+local_fallback"

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
            "scope": scope,
            "active_targets": active_targets,
        }

    async def _build_feedback_summary(self, active_targets: list[str]) -> str:
        feedback = await db.get_prediction_feedback(PREDICTION_EVAL_WINDOW)
        filtered = [item for item in feedback if str(item.get("slot", "")).split("_")[0] in active_targets]
        if not filtered:
            return "Belum ada feedback historis."
        lines = []
        for item in filtered:
            total = int(item.get("total", 0) or 0)
            wins = int(item.get("wins", 0) or 0)
            acc = wins / total if total else 0.0
            avg_conf = float(item.get("avg_confidence", 0.0) or 0.0)
            lines.append(f"{item['slot']}: acc={acc:.0%} ({wins}/{total}) avg_conf={avg_conf:.2f}")
        return "\n".join(lines)

    async def _build_adaptive_summary(self, active_targets: list[str]) -> str:
        diagnostics = await db.get_prediction_diagnostics(
            recent_periods=ADAPTIVE_SELECTION_WINDOW,
            low_conf_cutoff=LOW_CONFIDENCE_CUTOFF,
            source="auto",
        )
        filtered = [item for item in diagnostics if str(item.get("slot", "")).split("_")[0] in active_targets]
        if not filtered:
            return "Belum ada data adaptive selection."

        lines = []
        for item in filtered:
            total = int(item.get("total", 0) or 0)
            wins = int(item.get("wins", 0) or 0)
            skipped_total = int(item.get("skipped_total", 0) or 0)
            skipped_wins = int(item.get("skipped_wins", 0) or 0)
            low_total = int(item.get("low_conf_total", 0) or 0)
            low_wins = int(item.get("low_conf_wins", 0) or 0)
            overall_acc = wins / total if total else 0.0
            skipped_acc = skipped_wins / skipped_total if skipped_total else 0.0
            low_acc = low_wins / low_total if low_total else 0.0
            lines.append(
                f"{item['slot']}: acc={overall_acc:.0%} skipped={skipped_acc:.0%} "
                f"({skipped_wins}/{skipped_total}) low<{LOW_CONFIDENCE_CUTOFF:.0%}={low_acc:.0%} "
                f"({low_wins}/{low_total}) avg_conf={float(item.get('avg_confidence', 0.0) or 0.0):.2f}"
            )
        return "\n".join(lines)

    async def _build_knowledge_base_summary(self) -> str:
        kb = await db.get_active_knowledge_base()
        if not kb:
            return (
                "Belum ada knowledge base manual. "
                f"Gunakan command Telegram untuk build dari {KNOWLEDGE_BASE_HISTORY_LIMIT} history."
            )
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

    async def _get_adaptive_map(self) -> dict[str, dict]:
        diagnostics = await db.get_prediction_diagnostics(
            recent_periods=ADAPTIVE_SELECTION_WINDOW,
            low_conf_cutoff=LOW_CONFIDENCE_CUTOFF,
            source="auto",
        )
        mapping: dict[str, dict] = {}
        for item in diagnostics:
            total = int(item.get("total", 0) or 0)
            wins = int(item.get("wins", 0) or 0)
            picked_total = int(item.get("picked_total", 0) or 0)
            picked_wins = int(item.get("picked_wins", 0) or 0)
            skipped_total = int(item.get("skipped_total", 0) or 0)
            skipped_wins = int(item.get("skipped_wins", 0) or 0)
            low_total = int(item.get("low_conf_total", 0) or 0)
            low_wins = int(item.get("low_conf_wins", 0) or 0)

            overall_acc = wins / total if total else 0.0
            picked_acc = picked_wins / picked_total if picked_total else overall_acc
            skipped_acc = skipped_wins / skipped_total if skipped_total else overall_acc
            low_conf_acc = low_wins / low_total if low_total else overall_acc
            avg_conf = float(item.get("avg_confidence", 0.0) or 0.0)

            bonus = 0.0
            penalty = 0.0
            if skipped_total >= 8 and skipped_acc > 0.50:
                bonus += min(0.05, (skipped_acc - 0.50) * 0.18)
            if low_total >= 6 and low_conf_acc > 0.50:
                bonus += min(0.06, (low_conf_acc - 0.50) * 0.22)
            if low_total >= 6 and low_conf_acc > overall_acc + 0.08:
                bonus += min(0.03, (low_conf_acc - overall_acc) * 0.18)
            if avg_conf > 0 and overall_acc > avg_conf + 0.04:
                bonus += min(0.03, (overall_acc - avg_conf) * 0.30)

            if total >= 8 and overall_acc < 0.42:
                penalty += min(0.04, (0.42 - overall_acc) * 0.16)
            if avg_conf > 0 and avg_conf > overall_acc + 0.12:
                penalty += min(0.03, (avg_conf - overall_acc) * 0.20)
            if picked_total >= 4 and picked_acc < 0.40:
                penalty += min(0.02, (0.40 - picked_acc) * 0.15)

            mapping[item["slot"]] = {
                "total": total,
                "overall_accuracy": overall_acc,
                "picked_total": picked_total,
                "picked_accuracy": picked_acc,
                "skipped_total": skipped_total,
                "skipped_accuracy": skipped_acc,
                "low_conf_total": low_total,
                "low_conf_accuracy": low_conf_acc,
                "avg_confidence": avg_conf,
                "bonus": max(0.0, bonus - penalty),
                "penalty": penalty,
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
        summary_text = str(data.get("summary_text", "")).strip()
        if not summary_text:
            return None
        positions = data.get("positions")
        if not isinstance(positions, dict):
            positions = {}

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

    def _build_local_knowledge_base(self, history: list[dict], active_targets: list[str]) -> dict:
        positions_payload: dict[str, dict] = {}
        summary_lines: list[str] = []
        dos: list[str] = []
        donts: list[str] = []

        for target in POSITIONS:
            if target not in active_targets:
                positions_payload[target] = {
                    "besar_kecil": {"bias": "NETRAL", "strength": "lemah", "note": ""},
                    "genap_ganjil": {"bias": "NETRAL", "strength": "lemah", "note": ""},
                }
                continue

            rows = self._extract_target_rows(history, target)
            bk_payload = self._build_local_kb_dimension(
                [row["besar_kecil"] for row in rows],
                ("BE", "KE"),
                self._compute_dimension_signal([row["besar_kecil"] for row in rows], ("BE", "KE")),
            )
            gj_payload = self._build_local_kb_dimension(
                [row["genap_ganjil"] for row in rows],
                ("GE", "GA"),
                self._compute_dimension_signal([row["genap_ganjil"] for row in rows], ("GE", "GA")),
            )
            positions_payload[target] = {
                "besar_kecil": bk_payload,
                "genap_ganjil": gj_payload,
            }
            summary_lines.append(
                f"{target.upper()}: BK {bk_payload['bias']}/{bk_payload['strength']} | "
                f"GJ {gj_payload['bias']}/{gj_payload['strength']}"
            )
            note = " | ".join(part for part in (bk_payload["note"], gj_payload["note"]) if part)
            if note:
                summary_lines.append(note)
            if bk_payload["strength"] == "kuat" or gj_payload["strength"] == "kuat":
                dos.append(f"Utamakan {target.upper()} saat bias lokal terlihat kuat.")
            if bk_payload["strength"] == "lemah" and gj_payload["strength"] == "lemah":
                donts.append(f"Jangan paksa entry di {target.upper()} saat dua dimensi sama-sama lemah.")

        if not dos:
            dos.append("Prioritaskan dimensi dengan bias sedang atau kuat saja.")
        if not donts:
            donts.append("Hindari entry saat sinyal campuran dan confidence dekat 50%.")

        summary_text = "\n".join(summary_lines[:10]).strip() or "Bias lokal belum cukup kuat; tunggu sinyal yang lebih bersih."
        return {
            "summary_text": summary_text,
            "global_patterns": [f"KB lokal fallback untuk {', '.join(active_targets)}"],
            "positions": positions_payload,
            "dos": dos[:3],
            "donts": donts[:3],
        }

    def _build_local_kb_dimension(self, seq: list[str], choices: tuple[str, str], signal: dict) -> dict:
        a, b = choices
        if not seq:
            return {"bias": "NETRAL", "strength": "lemah", "note": "data kosong"}

        recent10 = seq[-10:]
        count_a = recent10.count(a)
        count_b = recent10.count(b)
        gap = abs(count_a - count_b)
        mode = str(signal.get("mode", "mixed"))
        last = str(signal.get("last", "?"))
        streak = int(signal.get("streak", 0) or 0)

        if gap >= 4:
            strength = "kuat"
        elif gap >= 2:
            strength = "sedang"
        else:
            strength = "lemah"

        if gap <= 1:
            bias = "NETRAL"
        else:
            bias = a if count_a > count_b else b

        note = (
            f"recent10 {count_a}/{count_b}, last={last}, streak={streak}, "
            f"mode={mode}, conf={float(signal.get('confidence', 0.5)):.2f}"
        )
        return {
            "bias": bias,
            "strength": strength,
            "note": note,
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

    def _build_signal_summary_for_targets(self, history: list[dict], active_targets: list[str]) -> str:
        parts = []
        for target in active_targets:
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

    def _trend_only_prediction(self, history: list[dict]) -> dict:
        positions = {}
        for target in POSITIONS:
            rows = self._extract_target_rows(history, target)
            bk_sig = self._compute_trend_signal([row["besar_kecil"] for row in rows], ("BE", "KE"))
            gj_sig = self._compute_trend_signal([row["genap_ganjil"] for row in rows], ("GE", "GA"))
            positions[target] = {
                "besar_kecil": {
                    "choice": bk_sig["choice"],
                    "confidence": bk_sig["confidence"],
                    "reason": f"trend_only:{bk_sig['mode']}",
                },
                "genap_ganjil": {
                    "choice": gj_sig["choice"],
                    "confidence": gj_sig["confidence"],
                    "reason": f"trend_only:{gj_sig['mode']}",
                },
            }
        return {
            "positions": positions,
            "ranking": self._build_ranking_from_positions(positions),
            "global_note": "trend_only_live_mode",
        }

    def _zigzag_only_prediction(self, history: list[dict]) -> dict:
        positions = {}
        for target in POSITIONS:
            rows = self._extract_target_rows(history, target)
            bk_sig = self._compute_zigzag_signal([row["besar_kecil"] for row in rows], ("BE", "KE"))
            gj_sig = self._compute_zigzag_signal([row["genap_ganjil"] for row in rows], ("GE", "GA"))
            positions[target] = {
                "besar_kecil": {
                    "choice": bk_sig["choice"],
                    "confidence": bk_sig["confidence"],
                    "reason": f"zigzag_only:{bk_sig['mode']}",
                },
                "genap_ganjil": {
                    "choice": gj_sig["choice"],
                    "confidence": gj_sig["confidence"],
                    "reason": f"zigzag_only:{gj_sig['mode']}",
                },
            }
        ranking = self._build_ranking_from_positions(positions)
        ranking = [
            {
                **item,
                "score": item["confidence"],
            }
            for item in ranking
        ]
        ranking.sort(key=lambda item: (-float(item.get("score", item["confidence"])), -item["confidence"], item["slot"]))
        return {
            "positions": positions,
            "ranking": ranking,
            "global_note": "zigzag_only_live_mode",
        }

    def _resolve_active_targets(self, scope: str | None) -> list[str]:
        normalized = (scope or "all").strip().lower()
        if normalized in POSITIONS:
            return [normalized]
        return list(POSITIONS)

    def _normalize_strategy_mode(self, value: str | None) -> str:
        normalized = (value or "auto").strip().lower()
        return normalized if normalized in {"auto", "zigzag", "trend", "heuristic", "llm", "hybrid"} else "auto"

    def _format_scope_label(self, active_targets: list[str]) -> str:
        return "semua posisi" if len(active_targets) == len(POSITIONS) else ", ".join(active_targets)

    def _apply_scope_filter(self, prediction: dict, active_targets: list[str], scope: str) -> dict:
        ranking = [
            item for item in prediction.get("ranking", [])
            if item.get("target") in active_targets
        ]
        ranking.sort(key=lambda item: (-float(item.get("score", item["confidence"])), -item["confidence"], item["slot"]))
        return {
            "positions": prediction.get("positions", {}),
            "ranking": ranking,
            "global_note": prediction.get("global_note", ""),
            "scope": scope,
            "active_targets": active_targets,
            "strategy_mode": prediction.get("strategy_mode"),
            "selected_method": prediction.get("selected_method"),
            "method_candidates": prediction.get("method_candidates", []),
        }

    def _annotate_prediction(self, prediction: dict, method: str, *, strategy_mode: str | None = None) -> dict:
        ranking = [
            {
                **item,
                "score": round(float(item.get("score", item.get("confidence", 0.5))), 3),
                "method": method,
            }
            for item in prediction.get("ranking", [])
        ]
        ranking.sort(key=lambda item: (-float(item.get("score", item["confidence"])), -item["confidence"], item["slot"]))
        return {
            "positions": prediction.get("positions", {}),
            "ranking": ranking,
            "global_note": prediction.get("global_note", ""),
            "strategy_mode": strategy_mode or method,
            "selected_method": method,
            "method_candidates": prediction.get("method_candidates", []),
        }

    async def _llm_prediction(self, history: list[dict], active_targets: list[str]) -> Optional[dict]:
        prompt = _PROMPT_TEMPLATE.format(
            scope_label=self._format_scope_label(active_targets),
            active_targets_label=", ".join(active_targets),
            n=len(history),
            history_table=self._build_4d_table(history),
            signal_summary=self._build_signal_summary_for_targets(history, active_targets),
            feedback_summary=await self._build_feedback_summary(active_targets),
            adaptive_summary=await self._build_adaptive_summary(active_targets),
            knowledge_base_summary=await self._build_knowledge_base_summary(),
        )

        result = await self._call_llm_json(LLM_PRIMARY, prompt)
        model_used = LLM_PRIMARY
        if result is None:
            logger.warning("Prediksi model utama gagal, coba fallback")
            result = await self._call_llm_json(LLM_FALLBACK, prompt)
            model_used = LLM_FALLBACK
        if result is None:
            return None

        parsed = self._parse_response(result)
        if parsed is None:
            return None
        parsed["global_note"] = f"llm:{model_used}"
        return parsed

    async def _hybrid_prediction(
        self,
        history: list[dict],
        active_targets: list[str],
        llm_prediction: dict | None = None,
    ) -> dict:
        if llm_prediction is None:
            llm_prediction = await self._llm_prediction(history, active_targets)
        heuristic_prediction = self._heuristic_prediction(history)
        merged = self._ensemble_prediction(llm_prediction, heuristic_prediction) if llm_prediction else heuristic_prediction
        feedback_map = await self._get_feedback_map()
        adjusted = self._apply_feedback_adjustments(merged, feedback_map)
        adaptive_map = await self._get_adaptive_map()
        return self._apply_adaptive_selection_overlay(adjusted, adaptive_map)

    async def _run_strategy_prediction(
        self,
        strategy_mode: str,
        history: list[dict],
        active_targets: list[str],
        scope: str,
    ) -> Optional[dict]:
        normalized = self._normalize_strategy_mode(strategy_mode)

        if normalized == "zigzag":
            raw_prediction = self._zigzag_only_prediction(history)
        elif normalized == "trend":
            raw_prediction = self._trend_only_prediction(history)
        elif normalized == "heuristic":
            raw_prediction = self._heuristic_prediction(history)
        elif normalized == "llm":
            raw_prediction = await self._llm_prediction(history, active_targets)
            if raw_prediction is None:
                return None
        elif normalized == "hybrid":
            raw_prediction = await self._hybrid_prediction(history, active_targets)
        else:
            return None

        annotated = self._annotate_prediction(raw_prediction, normalized, strategy_mode=normalized)
        return self._apply_scope_filter(annotated, active_targets, scope)

    async def _auto_prediction(self, history: list[dict], active_targets: list[str], scope: str) -> Optional[dict]:
        candidates: list[dict] = []
        llm_prediction = await self._llm_prediction(history, active_targets)
        raw_predictions: dict[str, dict | None] = {
            "zigzag": self._zigzag_only_prediction(history),
            "trend": self._trend_only_prediction(history),
            "heuristic": self._heuristic_prediction(history),
            "llm": llm_prediction,
            "hybrid": await self._hybrid_prediction(history, active_targets, llm_prediction=llm_prediction),
        }

        for method, raw_prediction in raw_predictions.items():
            if raw_prediction is None:
                continue
            annotated = self._annotate_prediction(raw_prediction, method, strategy_mode="auto")
            prediction = self._apply_scope_filter(annotated, active_targets, scope)
            if not prediction.get("ranking"):
                continue
            best = prediction["ranking"][0]
            candidates.append(
                {
                    "method": method,
                    "score": float(best.get("score", best.get("confidence", 0.5))),
                    "confidence": float(best.get("confidence", 0.5)),
                    "slot": best.get("slot", "-"),
                    "choice": best.get("choice", "-"),
                    "prediction": prediction,
                }
            )

        if not candidates:
            return None

        candidates.sort(key=lambda item: (-item["score"], -item["confidence"], item["method"]))
        selected = candidates[0]["prediction"]
        return {
            **selected,
            "strategy_mode": "auto",
            "selected_method": candidates[0]["method"],
            "method_candidates": [
                {
                    "method": item["method"],
                    "slot": item["slot"],
                    "choice": item["choice"],
                    "score": round(item["score"], 3),
                    "confidence": round(item["confidence"], 3),
                }
                for item in candidates
            ],
            "global_note": f"auto:{candidates[0]['method']}",
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
        recent12_seq = seq[-12:]
        recent12 = {a: recent12_seq.count(a), b: recent12_seq.count(b)}

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
        recent10_gap = abs(recent10[a] - recent10[b])
        recent5_gap = abs(recent5[a] - recent5[b])
        recent12_gap = abs(recent12[a] - recent12[b])
        regime_gap = abs(continue_rate - flip_rate)

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
        chaotic = False
        ambiguous_regime = regime_gap < 0.10
        weak_recent_bias = recent10_gap <= 2 and recent5_gap <= 1 and recent12_gap <= 2
        if alt8 >= 0.78 and flip_rate >= 0.56:
            score[opposite] += 0.16
            mode = "zigzag"
        elif continue_rate >= 0.60 and alt8 <= 0.50:
            score[last] += 0.16
            mode = "trend"
        elif ambiguous_regime and 0.42 <= alt8 <= 0.72 and weak_recent_bias:
            chaotic = True
            mode = "chaotic"
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
        confidence_cap = 0.78

        if chaotic:
            edge *= 0.38
            confidence_cap = 0.58
        elif mode == "mixed" and ambiguous_regime:
            edge *= 0.62
            confidence_cap = 0.62
        elif mode == "zigzag" and flip_rate < 0.60:
            edge *= 0.80
            confidence_cap = 0.66

        confidence = max(0.5, min(0.78, 0.5 + edge))
        confidence = min(confidence, confidence_cap)
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

    def _compute_trend_signal(self, seq: list[str], choices: tuple[str, str]) -> dict:
        if not seq:
            return {
                "choice": choices[0],
                "confidence": 0.5,
                "mode": "trend_no_data",
            }

        a, b = choices
        last = seq[-1]
        opposite = b if last == a else a
        recent20_seq = seq[-20:]
        recent10_seq = seq[-10:]
        recent5_seq = seq[-5:]
        recent20 = {a: recent20_seq.count(a), b: recent20_seq.count(b)}
        recent10 = {a: recent10_seq.count(a), b: recent10_seq.count(b)}
        recent5 = {a: recent5_seq.count(a), b: recent5_seq.count(b)}

        streak = 1
        for item in reversed(seq[:-1]):
            if item == last:
                streak += 1
            else:
                break

        trigger_count = 0
        continue_after_last = 0
        for idx in range(len(seq) - 1):
            if seq[idx] == last:
                trigger_count += 1
                if seq[idx + 1] == last:
                    continue_after_last += 1
        continue_rate = continue_after_last / trigger_count if trigger_count else 0.5

        changes8 = sum(1 for i in range(1, len(seq[-8:])) if seq[-8:][i] != seq[-8:][i - 1])
        alt8 = changes8 / max(1, len(seq[-8:]) - 1)

        recent10_bias = abs(recent10[a] - recent10[b]) / max(1, len(recent10_seq))
        recent5_bias = abs(recent5[a] - recent5[b]) / max(1, len(recent5_seq))
        recent20_bias = abs(recent20[a] - recent20[b]) / max(1, len(recent20_seq))
        direction_bias = last if recent10[last] >= recent10[opposite] else opposite

        trend_strength = (
            continue_rate * 0.34
            + recent10_bias * 0.24
            + recent5_bias * 0.16
            + recent20_bias * 0.10
            + min(1.0, streak / 4) * 0.20
            + max(0.0, 0.60 - alt8) * 0.12
        )
        trend_strength = max(0.0, min(1.0, trend_strength))

        choice = last if direction_bias == last or continue_rate >= 0.56 else opposite
        if trend_strength >= 0.74:
            mode = "trend_strong"
        elif trend_strength >= 0.64:
            mode = "trend_active"
        elif trend_strength >= 0.57:
            mode = "trend_weak"
        else:
            mode = "trend_low_edge"

        confidence = 0.5 + min(0.24, max(0.0, trend_strength - 0.42) * 0.62)
        if alt8 > 0.70:
            confidence = min(confidence, 0.58)
        elif trend_strength < 0.64:
            confidence = min(confidence, 0.61)

        return {
            "choice": choice,
            "confidence": round(max(0.5, min(0.76, confidence)), 3),
            "mode": mode,
        }

    def _compute_zigzag_signal(self, seq: list[str], choices: tuple[str, str]) -> dict:
        if not seq:
            return {
                "choice": choices[0],
                "confidence": 0.5,
                "mode": "zigzag_no_data",
            }

        a, b = choices
        last = seq[-1]
        opposite = b if last == a else a
        recent10_seq = seq[-10:]
        recent8_seq = seq[-8:]
        recent6_seq = seq[-6:]

        recent10 = {a: recent10_seq.count(a), b: recent10_seq.count(b)}
        recent6 = {a: recent6_seq.count(a), b: recent6_seq.count(b)}

        changes8 = sum(1 for i in range(1, len(recent8_seq)) if recent8_seq[i] != recent8_seq[i - 1])
        alt8 = changes8 / max(1, len(recent8_seq) - 1)

        changes10 = sum(1 for i in range(1, len(recent10_seq)) if recent10_seq[i] != recent10_seq[i - 1])
        alt10 = changes10 / max(1, len(recent10_seq) - 1)

        trigger_count = 0
        flip_after_last = 0
        for idx in range(len(seq) - 1):
            if seq[idx] == last:
                trigger_count += 1
                if seq[idx + 1] != last:
                    flip_after_last += 1
        flip_rate = flip_after_last / trigger_count if trigger_count else 0.5

        streak = 1
        for item in reversed(seq[:-1]):
            if item == last:
                streak += 1
            else:
                break

        balance10 = 1.0 - (abs(recent10[a] - recent10[b]) / max(1, len(recent10_seq)))
        balance6 = 1.0 - (abs(recent6[a] - recent6[b]) / max(1, len(recent6_seq)))
        streak_boost = min(1.0, max(0.0, (streak - 1) / 3))

        zigzag_strength = (
            alt8 * 0.34
            + alt10 * 0.24
            + flip_rate * 0.28
            + balance10 * 0.08
            + balance6 * 0.06
            + streak_boost * 0.12
        )
        zigzag_strength = max(0.0, min(1.0, zigzag_strength))

        if zigzag_strength >= 0.72:
            mode = "zigzag_strong"
        elif zigzag_strength >= 0.62:
            mode = "zigzag_active"
        elif zigzag_strength >= 0.56:
            mode = "zigzag_weak"
        else:
            mode = "zigzag_low_edge"

        confidence = 0.5 + min(0.22, max(0.0, zigzag_strength - 0.45) * 0.60)
        if streak >= 2:
            confidence += min(0.04, 0.015 * streak)
        if alt8 < 0.55 and flip_rate < 0.55:
            confidence = min(confidence, 0.57)
        elif zigzag_strength < 0.62:
            confidence = min(confidence, 0.61)

        return {
            "choice": opposite,
            "confidence": round(max(0.5, min(0.76, confidence)), 3),
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

    def _apply_adaptive_selection_overlay(self, prediction: dict, adaptive_map: dict[str, dict]) -> dict:
        ranking = []
        for item in prediction.get("ranking", []):
            slot = item["slot"]
            confidence = self._normalize_confidence(item.get("confidence", 0.5))
            adaptive = adaptive_map.get(slot, {})
            score = confidence
            raw_bonus = float(adaptive.get("bonus", 0.0) or 0.0)

            low_conf_acc = float(adaptive.get("low_conf_accuracy", 0.0) or 0.0)
            low_conf_total = int(adaptive.get("low_conf_total", 0) or 0)
            overall_acc = float(adaptive.get("overall_accuracy", 0.0) or 0.0)
            avg_conf = float(adaptive.get("avg_confidence", 0.0) or 0.0)
            adaptive_bonus = raw_bonus
            adaptive_penalty = 0.0

            if overall_acc < 0.50:
                adaptive_bonus *= 0.35
            elif overall_acc < 0.55:
                adaptive_bonus *= 0.70

            score += adaptive_bonus

            if (
                confidence < LOW_CONFIDENCE_CUTOFF
                and low_conf_total >= 6
                and low_conf_acc > 0.55
                and (overall_acc >= 0.50 or low_conf_acc >= overall_acc + 0.12)
            ):
                score += min(0.03, (low_conf_acc - 0.55) * 0.18)

            if confidence >= 0.68 and avg_conf > 0 and overall_acc + 0.08 < avg_conf:
                adaptive_penalty += min(0.04, (avg_conf - overall_acc - 0.08) * 0.20)
            if overall_acc > 0 and overall_acc < 0.48:
                adaptive_penalty += min(0.03, (0.48 - overall_acc) * 0.20)

            score -= adaptive_penalty

            adaptive_note = (
                f"adaptive(score={score:.3f},bonus={adaptive_bonus:+.3f},"
                f"penalty={adaptive_penalty:+.3f},low={low_conf_acc:.0%}/{low_conf_total},"
                f"overall={overall_acc:.0%})"
            )
            ranking.append({
                **item,
                "score": round(max(0.5, min(0.86, score)), 3),
                "reason": f"{item.get('reason', '')} | {adaptive_note}",
            })

        ranking.sort(key=lambda item: (-float(item.get("score", item["confidence"])), -item["confidence"], item["slot"]))
        return {
            "positions": prediction.get("positions", {}),
            "ranking": ranking,
            "global_note": prediction.get("global_note", ""),
            "scope": prediction.get("scope", "all"),
            "active_targets": prediction.get("active_targets", list(POSITIONS)),
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
        heuristic_mode = str(heuristic_dim.get("reason", "")).split("heuristic:", 1)[-1].split("|", 1)[0].strip().lower()

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

        if heuristic_mode == "chaotic":
            final_conf = min(final_conf, 0.58)
            if llm_choice != heu_choice:
                final_conf = min(final_conf, 0.55)
        elif heuristic_mode == "mixed" and llm_choice != heu_choice:
            final_conf = min(final_conf, 0.59)
        elif heuristic_mode == "zigzag" and llm_choice != heu_choice:
            final_conf = min(final_conf, 0.61)

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
