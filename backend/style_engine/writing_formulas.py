"""nebula v1.6 Phase 4a -- WritingFormulaAnalyzer for L2 writing formula compliance."""
import json
import logging
from dataclasses import dataclass

from backend.style_engine.style_extractor import (
    _split_sentences,
    _DIALOGUE_PATTERN,
    _char_count,
)

logger = logging.getLogger(__name__)


@dataclass
class ComplianceResult:
    metric: str
    expected: str
    actual: str
    passed: bool


@dataclass
class WritingFormulaStats:
    """Statistics computed from chapter scene texts."""
    # Deterministic
    avg_sentence_length: float = 0.0
    short_ratio: float = 0.0
    medium_ratio: float = 0.0
    long_ratio: float = 0.0
    dialogue_ratio: float = 0.0
    max_consecutive_dialogue: int = 0
    max_para_sentences: int = 0
    max_para_words: int = 0
    # LLM-assisted (Tier 3 Haiku)
    emotional_beat_density: float = 0.0
    satisfaction_beat_count: int = 0
    suspense_hook_present: bool = False
    # Whether the LLM-assisted fields above were actually produced by a live
    # LLM call. False means the values are default zeros (either because the
    # sync path ran, or because the LLM call raised). Used by check_compliance
    # to auto-pass these checks only on genuine LLM unavailability, NOT when
    # the LLM legitimately reported zero emotional beats / no hook.
    llm_available: bool = False


class WritingFormulaAnalyzer:
    """L2 writing formula statistics + compliance checking.

    Deterministic (tier_0): sentence length, dialogue ratio, paragraph density.
    LLM-assisted (Tier 3): emotional beat density, satisfaction beat count, suspense hook.
    """

    def analyze_sync(self, scene_texts: list[str]) -> WritingFormulaStats:
        """Run deterministic analysis only. Zero LLM calls."""
        merged = "\n\n".join(scene_texts)
        if not merged.strip():
            return WritingFormulaStats()

        sentences = _split_sentences(merged)
        total_chars = _char_count(merged)

        # Sentence stats (note: short ≤15 matches spec; StyleExtractor uses <15)
        if sentences:
            lengths = [_char_count(s) for s in sentences]
            avg = sum(lengths) / len(lengths)
            total_len = len(lengths)
            short = sum(1 for l in lengths if l <= 15)
            medium = sum(1 for l in lengths if 16 <= l <= 40)
            long = sum(1 for l in lengths if l > 40)
            short_ratio = round(short / total_len, 2)
            medium_ratio = round(medium / total_len, 2)
            long_ratio = round(long / total_len, 2)
        else:
            avg = 0.0
            short_ratio = medium_ratio = long_ratio = 0.0

        # Dialogue stats
        if merged.strip():
            turns = _DIALOGUE_PATTERN.findall(merged)
            dialogue_chars = sum(_char_count(t) for t in turns)
            # Use total_chars as denominator if available, else fall back to raw text length
            denom = total_chars if total_chars > 0 else len(merged)
            dialogue_ratio = round(dialogue_chars / denom, 2)
            max_consecutive = self._count_max_consecutive_dialogue(merged)
        else:
            dialogue_ratio = 0.0
            max_consecutive = 0

        # Paragraph stats
        paragraphs = [p.strip() for p in merged.split("\n\n") if p.strip()]
        if paragraphs:
            max_para_sentences = max(
                len(_split_sentences(p)) for p in paragraphs
            )
            max_para_words = max(
                _char_count(p) for p in paragraphs
            )
        else:
            max_para_sentences = 0
            max_para_words = 0

        return WritingFormulaStats(
            avg_sentence_length=round(avg, 1),
            short_ratio=short_ratio,
            medium_ratio=medium_ratio,
            long_ratio=long_ratio,
            dialogue_ratio=dialogue_ratio,
            max_consecutive_dialogue=max_consecutive,
            max_para_sentences=max_para_sentences,
            max_para_words=max_para_words,
        )

    async def analyze_async(self, scene_texts: list[str]) -> WritingFormulaStats:
        """Run full analysis (deterministic + LLM-assisted). LLM failures are silent.

        `llm_available` is True only when BOTH LLM-assisted calls succeed.
        A successful emotional-beat call but a failed suspense-hook call leaves
        it False — the chain is all-or-nothing so check_compliance doesn't
        report a false alarm based on partial LLM coverage.
        """
        stats = self.analyze_sync(scene_texts)  # llm_available=False here
        merged = "\n\n".join(scene_texts)
        if not merged.strip():
            return stats

        try:
            emo_stats, emo_ok = await self._detect_emotional_beats(merged)
            stats.emotional_beat_density = emo_stats.get("density", 0.0)
            stats.satisfaction_beat_count = emo_stats.get("count", 0)

            hook_present, hook_ok = await self._detect_suspense_hook(merged)
            stats.suspense_hook_present = hook_present

            stats.llm_available = emo_ok and hook_ok
        except Exception as e:
            logger.warning("WritingFormulaAnalyzer LLM unavailable: %s", e)
            # stats.llm_available stays False

        return stats

    def check_compliance(
        self, stats: WritingFormulaStats, formula: dict
    ) -> list[ComplianceResult]:
        """Compare stats against formula thresholds. Returns list of results."""
        if not formula:
            return []

        results = []

        # Sentence checks
        sent = formula.get("sentence", {})
        if "avg_length_max" in sent:
            results.append(ComplianceResult(
                metric="avg_sentence_length",
                expected=f"≤{sent['avg_length_max']}",
                actual=str(stats.avg_sentence_length),
                passed=stats.avg_sentence_length <= sent["avg_length_max"],
            ))
        if "short_pct_min" in sent:
            actual_pct = round(stats.short_ratio * 100, 1)
            results.append(ComplianceResult(
                metric="short_sentence_ratio",
                expected=f"≥{sent['short_pct_min']}%",
                actual=f"{actual_pct}%",
                passed=actual_pct >= sent["short_pct_min"],
            ))
        if "long_pct_max" in sent:
            actual_pct = round(stats.long_ratio * 100, 1)
            results.append(ComplianceResult(
                metric="long_sentence_ratio",
                expected=f"≤{sent['long_pct_max']}%",
                actual=f"{actual_pct}%",
                passed=actual_pct <= sent["long_pct_max"],
            ))

        # Dialogue checks
        dial = formula.get("dialogue", {})
        if "ratio_min" in dial:
            actual_pct = round(stats.dialogue_ratio * 100, 1)
            results.append(ComplianceResult(
                metric="dialogue_ratio",
                expected=f"≥{int(dial['ratio_min'] * 100)}%",
                actual=f"{actual_pct}%",
                passed=stats.dialogue_ratio >= dial["ratio_min"],
            ))
        if "max_consecutive_lines" in dial:
            results.append(ComplianceResult(
                metric="max_consecutive_dialogue",
                expected=f"≤{dial['max_consecutive_lines']}",
                actual=str(stats.max_consecutive_dialogue),
                passed=stats.max_consecutive_dialogue <= dial["max_consecutive_lines"],
            ))

        # Paragraph checks
        para = formula.get("paragraph", {})
        if "max_sentences" in para:
            results.append(ComplianceResult(
                metric="max_para_sentences",
                expected=f"≤{para['max_sentences']}",
                actual=str(stats.max_para_sentences),
                passed=stats.max_para_sentences <= para["max_sentences"],
            ))
        if "max_words" in para:
            results.append(ComplianceResult(
                metric="max_para_words",
                expected=f"≤{para['max_words']}",
                actual=str(stats.max_para_words),
                passed=stats.max_para_words <= para["max_words"],
            ))

        # Emotional beat checks (LLM-assisted; auto-pass only when LLM was
        # genuinely unavailable, not when LLM legitimately reports zero).
        emo = formula.get("emotional_beat", {})
        if "min_per_1k" in emo:
            llm_ok = stats.llm_available
            results.append(ComplianceResult(
                metric="emotional_beat_density",
                expected=f"≥{emo['min_per_1k']}/千字",
                actual=f"{stats.emotional_beat_density}/千字",
                passed=stats.emotional_beat_density >= emo["min_per_1k"] or not llm_ok,
            ))

        sat = formula.get("satisfaction_beat", {})
        if "min_count" in sat:
            llm_ok = stats.llm_available
            results.append(ComplianceResult(
                metric="satisfaction_beat_count",
                expected=f"≥{sat['min_count']}",
                actual=str(stats.satisfaction_beat_count),
                passed=stats.satisfaction_beat_count >= sat["min_count"] or not llm_ok,
            ))

        hook = formula.get("suspense_hook", {})
        if "required" in hook:
            llm_ok = stats.llm_available
            results.append(ComplianceResult(
                metric="suspense_hook_present",
                expected="true",
                actual=str(stats.suspense_hook_present).lower(),
                passed=not hook["required"] or stats.suspense_hook_present or not llm_ok,
            ))

        return results

    def _count_max_consecutive_dialogue(self, text: str) -> int:
        """Count max consecutive lines containing quoted dialogue."""
        lines = text.split("\n")
        max_run = 0
        current_run = 0
        for line in lines:
            if _DIALOGUE_PATTERN.search(line):
                current_run += 1
                max_run = max(max_run, current_run)
            else:
                current_run = 0
        return max_run

    async def _detect_emotional_beats(self, text: str) -> tuple[dict, bool]:
        """Use Tier 3 LLM to detect emotional beats (satisfaction points).

        Returns (result, success):
            result: {"density": float, "count": int}
            success: True iff the LLM call completed and produced a parseable
                     response. False on any exception or parse error.
        """
        try:
            from backend.llm.model_router import get_model_router

            router = get_model_router()
            sample = text[:3000]

            system_prompt = (
                "你是一位网文编辑。分析以下章节文本，检测“爽点”（能力展示/打脸/逆袭/收获/突破）。"
                "只输出一个 JSON 对象。"
            )
            user_prompt = (
                f"分析以下文本的爽点：\n\n{sample}\n\n"
                "输出 JSON：\n"
                '{"emotional_beat_density": <每千字爽点数量，浮点数>,\n'
                ' "satisfaction_beats": [{"type": "能力展示|打脸|逆袭|收获|突破", "description": "一句话描述"}]}'
            )

            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ]

            result = await router.execute(
                agent_name="reviewer",
                task_name="emotional_beat_detection",
                messages=messages,
                json_mode=True,
            )

            content = result.get("content", "")
            parsed = json.loads(content) if isinstance(content, str) else content
            density = float(parsed.get("emotional_beat_density", 0))
            beats = parsed.get("satisfaction_beats", [])
            return {"density": density, "count": len(beats)}, True

        except Exception as e:
            logger.warning("Emotional beat detection failed: %s", e)
            return {"density": 0.0, "count": 0}, False

    async def _detect_suspense_hook(self, text: str) -> tuple[bool, bool]:
        """Use Tier 3 LLM to detect chapter-end suspense hook.

        Returns (present, success):
            present: True if the LLM detected a hook
            success: True iff the LLM call completed and produced a parseable
                     response. False on any exception or parse error.
        """
        try:
            from backend.llm.model_router import get_model_router

            router = get_model_router()
            ending = text[-1000:] if len(text) > 1000 else text

            system_prompt = (
                "你是一位网文编辑。检查章节结尾是否有悬念钩子（新敌人/新目标/新线索/未解谜题）。"
                "只输出一个 JSON 对象。"
            )
            user_prompt = (
                f"章节结尾文本：\n\n{ending}\n\n"
                "输出 JSON：{\"suspense_hook_present\": true|false}"
            )

            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ]

            result = await router.execute(
                agent_name="reviewer",
                task_name="suspense_hook_detection",
                messages=messages,
                json_mode=True,
            )

            content = result.get("content", "")
            parsed = json.loads(content) if isinstance(content, str) else content
            return bool(parsed.get("suspense_hook_present", False)), True

        except Exception as e:
            logger.warning("Suspense hook detection failed: %s", e)
            return False, False
