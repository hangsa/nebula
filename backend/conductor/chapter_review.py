"""nebula v1.6 Phase 3a — ChapterReviewBuilder + CoherenceScorer."""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from backend.config import settings
from backend.utils.file_manager import FileManager

logger = logging.getLogger(__name__)


class CoherenceScorer:
    """
    Scores chapter coherence: rule-based (three equal 1/3 weights) + Tier 3 LLM +/-10 delta.

    Rule part (equal weights, normalized to 0-100):
    - Narrative asset health: active asset completion rate
    - ReaderOS state avg: (addiction + satisfaction + curiosity) / 3
    - Fact Guard pass rate: passed / total checks

    LLM part: +/-10 delta adjustment + one-liner comment from Tier 3 Haiku.
    Final: clamp(rule_score + llm_delta, 0, 100).
    Falls back to rule-only score on LLM failure.
    """

    MAX_DELTA = 10

    def compute_rule_score(
        self,
        narrative_health: float,
        reader_os_avg: float,
        fact_guard_pass_rate: float,
    ) -> float:
        return round(
            (narrative_health + reader_os_avg + fact_guard_pass_rate * 100) / 3
        )

    def _clamp_delta(self, delta: int) -> int:
        return max(-self.MAX_DELTA, min(self.MAX_DELTA, delta))

    def _compute_final(self, base_score: float, llm_delta: int) -> int:
        return max(0, min(100, int(base_score + llm_delta)))

    async def score(
        self,
        base_score: float,
        scene_summaries: str,
        narrative_assets_summary: str,
    ) -> tuple[int, str]:
        """
        Compute final coherence score with LLM fine-tuning.

        Returns (final_score, comment).
        Falls back to rule-only score if LLM unavailable.
        """
        try:
            from backend.llm.model_router import get_model_router, ModelUnavailableError

            router = get_model_router()

            system_prompt = (
                "你是一位专业的小说编辑。根据章节摘要和叙事资产数据，"
                "评估本章的连贯性。只输出一个 JSON 对象。"
            )
            user_prompt = (
                f"规则基础分: {base_score:.0f}/100\n\n"
                f"【章节场景摘要】\n{scene_summaries}\n\n"
                f"【叙事资产数据】\n{narrative_assets_summary}\n\n"
                "请评分（在基础分 +/-10 范围内微调），并写一句中文点评。"
                '输出 JSON: {"delta": <int>, "comment": "<一句话点评>"}'
            )

            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ]

            result = await router.execute(
                agent_name="reviewer",
                task_name="coherence_scorer",
                messages=messages,
                json_mode=True,
            )

            content = result.get("content", "")
            parsed = json.loads(content) if isinstance(content, str) else content
            delta = self._clamp_delta(int(parsed.get("delta", 0)))
            comment = parsed.get("comment", "")
            return self._compute_final(base_score, delta), comment

        except Exception as e:
            logger.warning("CoherenceScorer LLM unavailable, using rule score only: %s", e)
            return int(base_score), ""


class ChapterReviewBuilder:
    """Assembles chapter review data from all subsystems."""

    def __init__(self, project_id: str, projects_dir: Optional[Path] = None):
        self.project_id = project_id
        self._projects_dir = Path(projects_dir) if projects_dir else settings.projects_dir
        self._project_dir = self._projects_dir / project_id
        self._fm = FileManager(self._projects_dir)
        self._scorer = CoherenceScorer()

    def _all_scenes_done(self, chapter_number: int) -> bool:
        """Check if all scenes in the chapter are completed."""
        progress = self._fm.read_json(self.project_id, "progress.json") or {}
        chapters = progress.get("chapters", [])
        chapter = next(
            (ch for ch in chapters if ch.get("chapter_number") == chapter_number),
            None,
        )
        if not chapter:
            return False
        scenes = chapter.get("scenes", [])
        if not scenes:
            return False
        return all(
            s.get("status") in ("completed", "force_passed", "skipped")
            for s in scenes
        )

    def build_review(self, chapter_number: int) -> dict:
        """Build complete chapter review data (synchronous, rule score only)."""
        now = datetime.now(timezone.utc).isoformat()

        # 1. ReaderOS snapshot
        from backend.reader_os.calculator import ReaderOS
        reader_os = ReaderOS(self.project_id, self._projects_dir)
        genre = self._detect_genre()
        reader_snapshot = reader_os.snapshot(chapter_number, genre)

        # 2. Narrative asset summary
        narrative_assets = self._summarize_narrative_assets()

        # 3. Fact Guard summary
        fact_guard = self._summarize_fact_guard(chapter_number)

        # 4. Narrative Guard warnings
        ng_warnings = self._collect_narrative_guard_warnings(chapter_number)

        # 5. Coherence score (rule-based only)
        rule_score = self._compute_base_coherence(reader_snapshot, narrative_assets, fact_guard)

        # 6. Writing formula compliance (extracted to reuse for discussion topics)
        formula_compliance = self._check_writing_formula(chapter_number)

        review = {
            "chapter_number": chapter_number,
            "timestamp": now,
            "coherence_score": int(rule_score),
            "coherence_comment": "",
            "reader_os": {
                "addiction": reader_snapshot.get("addiction", 0),
                "fatigue": reader_snapshot.get("fatigue", 0),
                "curiosity": reader_snapshot.get("curiosity", 0),
                "tension": reader_snapshot.get("tension", 0),
                "satisfaction": reader_snapshot.get("satisfaction", 0),
                "frustration": reader_snapshot.get("frustration", 0),
                "discussion": reader_snapshot.get("discussion", 0),
            },
            "narrative_assets": narrative_assets,
            "narrative_guard_warnings": ng_warnings,
            "fact_guard_summary": fact_guard,
            "writing_formula_compliance": formula_compliance,
            "pacing_compliance": self._check_pacing(chapter_number),
            "style_guard_violations": self._collect_style_guard_violations(chapter_number),
            "discussion_topics": self._derive_discussion_topics(
                reader_snapshot, ng_warnings, fact_guard, formula_compliance,
            ),
            "decision": None,
            "decision_feedback": None,
        }
        return review

    async def build_review_async(self, chapter_number: int) -> dict:
        """Build review with async LLM coherence scoring."""
        review = self.build_review(chapter_number)
        rule_score = review["coherence_score"]

        scene_summaries = self._build_scene_summaries(chapter_number)
        narrative_assets_summary = json.dumps(review["narrative_assets"], ensure_ascii=False)

        final_score, comment = await self._scorer.score(
            base_score=rule_score,
            scene_summaries=scene_summaries,
            narrative_assets_summary=narrative_assets_summary,
        )
        review["coherence_score"] = final_score
        review["coherence_comment"] = comment
        # Upgrade writing formula compliance with LLM-assisted metrics
        review["writing_formula_compliance"] = await self._check_writing_formula_async(chapter_number)
        review["pacing_compliance"] = await self._check_pacing_async(chapter_number)
        return review

    def _compute_base_coherence(
        self,
        reader_snapshot: dict,
        narrative_assets: dict,
        fact_guard: dict,
    ) -> float:
        """Compute rule-based base coherence (60% of final score)."""
        active_keys = {"new_conflicts", "escalated_conflicts", "new_clues", "planted_foreshadowing"}
        resolved_keys = {"resolved_conflicts", "fulfilled_promises", "revealed_twists", "fulfilled_expectations"}
        total_active = sum(v for k, v in narrative_assets.items() if k in active_keys)
        total_resolved = sum(v for k, v in narrative_assets.items() if k in resolved_keys)
        narrative_health = min(100, (total_resolved / max(1, total_active + total_resolved)) * 100)

        reader_os_avg = (
            reader_snapshot.get("addiction", 50)
            + reader_snapshot.get("satisfaction", 50)
            + reader_snapshot.get("curiosity", 50)
        ) / 3

        fg_pass_rate = fact_guard.get("pass_rate", 0.0)

        return self._scorer.compute_rule_score(
            narrative_health=narrative_health,
            reader_os_avg=reader_os_avg,
            fact_guard_pass_rate=fg_pass_rate,
        )

    def _detect_genre(self) -> str:
        dna = self._fm.read_json(self.project_id, "story_dna.json") or {}
        return dna.get("genre", "cool_novel")

    def _summarize_narrative_assets(self) -> dict:
        """Count narrative assets by status across all registries."""
        registry_files = {
            "conflict": "conflicts",
            "mystery": "mysteries",
            "twist": "twists",
            "goal": "goals",
            "promise": "promises",
            "reveal": "reveals",
            "expectation": "expectations",
            "foreshadowing": "foreshadowing",
        }

        counts: dict[str, int] = {}
        for reg_type, key in registry_files.items():
            data = self._fm.read_json(self.project_id, f"storyos/{key}.json") or []
            if isinstance(data, dict):
                data = data.get(key, [])
            for item in data:
                status = item.get("status", "unknown")
                count_key = f"{reg_type}_{status}"
                counts[count_key] = counts.get(count_key, 0) + 1

        return {
            "new_conflicts": counts.get("conflict_active", 0),
            "escalated_conflicts": counts.get("conflict_escalated", 0),
            "resolved_conflicts": counts.get("conflict_resolved", 0),
            "new_clues": counts.get("mystery_active", 0) + counts.get("foreshadowing_planted", 0),
            "fulfilled_promises": counts.get("promise_fulfilled", 0),
            "revealed_twists": counts.get("twist_revealed", 0) + counts.get("twist_partially_revealed", 0),
            "fulfilled_expectations": counts.get("expectation_fulfilled", 0),
            "planted_foreshadowing": counts.get("foreshadowing_planted", 0),
        }

    def _summarize_fact_guard(self, chapter_number: int) -> dict:
        """Count Fact Guard pass/fail from scene meta files (chapter-level)."""
        total_checks = 0
        total_passed = 0
        chapters_dir = self._project_dir / "chapters"
        if not chapters_dir.exists():
            return {"passed": 0, "failed": 0, "total": 0, "pass_rate": 0.0}

        for meta_file in sorted(chapters_dir.glob(f"ch{chapter_number:02d}_scene_*_meta.json")):
            try:
                meta = json.loads(meta_file.read_text(encoding="utf-8"))
                fgr = meta.get("fact_guard_results", {})
                checks = fgr.get("checks", [])
                total_checks += len(checks)
                total_passed += sum(1 for c in checks if c.get("passed"))
            except Exception:
                continue

        return {
            "passed": total_passed,
            "failed": total_checks - total_passed,
            "total": total_checks,
            "pass_rate": round(total_passed / max(1, total_checks), 2) if total_checks > 0 else 0.0,
        }

    def _collect_narrative_guard_warnings(self, chapter_number: int) -> list[dict]:
        """Collect Narrative Guard warnings from scene meta files."""
        warnings = []
        chapters_dir = self._project_dir / "chapters"
        if not chapters_dir.exists():
            return warnings

        for meta_file in sorted(chapters_dir.glob(f"ch{chapter_number:02d}_scene_*_meta.json")):
            try:
                meta = json.loads(meta_file.read_text(encoding="utf-8"))
                ng_warnings = meta.get("narrative_guard_warnings", [])
                if isinstance(ng_warnings, list):
                    warnings.extend(ng_warnings)
            except Exception:
                continue

        return warnings

    def _collect_style_guard_violations(self, chapter_number: int) -> list[dict]:
        """Collect Style Guard violations from scene meta files."""
        violations = []
        chapters_dir = self._project_dir / "chapters"
        if not chapters_dir.exists():
            return violations

        for meta_file in sorted(chapters_dir.glob(f"ch{chapter_number:02d}_scene_*_meta.json")):
            try:
                meta = json.loads(meta_file.read_text(encoding="utf-8"))
                sg_violations = meta.get("style_guard_violations", [])
                if isinstance(sg_violations, list):
                    violations.extend(sg_violations)
            except Exception:
                continue

        return violations

    def _build_scene_summaries(self, chapter_number: int) -> str:
        """Build ~800 char summary of chapter scenes for LLM coherence scoring."""
        chapters_dir = self._project_dir / "chapters"
        summaries = []

        for draft_file in sorted(chapters_dir.glob(f"ch{chapter_number:02d}_scene_*_draft.md")):
            try:
                text = draft_file.read_text(encoding="utf-8")
                scene_name = draft_file.stem.replace("_draft", "")
                snippet = text[:300].replace("\n", " ")
                summaries.append(f"[{scene_name}] {snippet}...")
            except Exception:
                continue

        if not summaries:
            return f"Chapter {chapter_number} — no scene drafts found"

        return "\n".join(summaries)[:800]

    def _collect_scene_texts(self, chapter_number: int) -> list[str]:
        """Collect all scene draft texts for a chapter."""
        texts = []
        chapters_dir = self._project_dir / "chapters"
        if not chapters_dir.exists():
            return texts
        for draft_file in sorted(chapters_dir.glob(f"ch{chapter_number:02d}_scene_*_draft.md")):
            try:
                text = draft_file.read_text(encoding="utf-8")
                if text.strip():
                    texts.append(text)
            except Exception:
                continue
        return texts

    def _derive_discussion_topics(
        self,
        reader_snapshot: dict,
        ng_warnings: list,
        fact_guard: dict,
        formula_compliance: list,
    ) -> list[str]:
        """Derive 2-3 narrative discussion questions from review data (zero LLM)."""
        topics: list[str] = []

        discussion_score = reader_snapshot.get("discussion", 0)
        if discussion_score < 40:
            topics.append("本章讨论潜力偏低，是否存在悬念过早揭示或冲突解决过快？")

        seen_drift_types: set[tuple[str, str]] = set()
        for w in ng_warnings:
            drift_type = w.get("drift_type", "")
            character = w.get("character", "")
            if not drift_type or not character:
                continue
            key = (drift_type, character)
            if key in seen_drift_types:
                continue
            seen_drift_types.add(key)
            if drift_type == "emotion_surge":
                topics.append(f"{character}的情绪转折是否铺垫充分？是否存在内在逻辑断层？")
            elif drift_type == "relation_shift":
                topics.append(f"{character}的关系变化是否过于突兀？需要哪些前文呼应以增强说服力？")
            elif drift_type == "knowledge_leak":
                topics.append(f"{character}的信息获取方式是否合理？知识泄露可能削弱悬念张力。")

        for item in formula_compliance:
            metric = item.get("metric", "")
            if "sentence" in metric:
                topics.append("本章句式结构单一，是否需要调整长短句比例以改善阅读节奏？")
                break

        fail_count = fact_guard.get("failed", 0) if fact_guard else 0
        if fail_count > 0:
            topics.append(f"事实守卫发现 {fail_count} 项一致性问题，是否需要在上一章或后续补充修正？")

        if len(topics) < 2:
            topics.append("本章的叙事节奏是否与整体故事曲线匹配？")
        if len(topics) < 2:
            topics.append("主要角色的行为决策是否与其性格设定一致？")

        return topics[:3]

    def _check_writing_formula(self, chapter_number: int) -> list[dict]:
        """Synchronous writing formula compliance check (deterministic only)."""
        try:
            from backend.style_engine.writing_formulas import WritingFormulaAnalyzer
            from backend.style_engine.genre_template import GenreTemplate

            texts = self._collect_scene_texts(chapter_number)
            if not texts:
                return []

            formula = GenreTemplate().get_style_formula(self._detect_genre())
            if not formula:
                return []

            analyzer = WritingFormulaAnalyzer()
            stats = analyzer.analyze_sync(texts)
            results = analyzer.check_compliance(stats, formula)
            return [
                {"metric": r.metric, "expected": r.expected, "actual": r.actual, "passed": r.passed}
                for r in results
            ]
        except Exception as e:
            logger.warning("Writing formula check failed (non-blocking): %s", e)
            return []

    async def _check_writing_formula_async(self, chapter_number: int) -> list[dict]:
        """Async writing formula compliance check (adds LLM metrics)."""
        try:
            from backend.style_engine.writing_formulas import WritingFormulaAnalyzer
            from backend.style_engine.genre_template import GenreTemplate

            texts = self._collect_scene_texts(chapter_number)
            if not texts:
                return []

            formula = GenreTemplate().get_style_formula(self._detect_genre())
            if not formula:
                return []

            analyzer = WritingFormulaAnalyzer()
            stats = await analyzer.analyze_async(texts)
            results = analyzer.check_compliance(stats, formula)
            return [
                {"metric": r.metric, "expected": r.expected, "actual": r.actual, "passed": r.passed}
                for r in results
            ]
        except Exception as e:
            logger.warning("Writing formula async check failed (non-blocking): %s", e)
            return []

    def _check_pacing(self, chapter_number: int) -> list[dict]:
        """Synchronous pacing compliance check. Mirrors _check_writing_formula."""
        try:
            from backend.style_engine.pacing import PacingAnalyzer
            from backend.style_engine.genre_template import GenreTemplate

            texts = self._collect_scene_texts(chapter_number)
            if not texts:
                return []

            pacing = GenreTemplate().get_pacing(self._detect_genre())
            if not pacing:
                return []

            stats = PacingAnalyzer().analyze_sync(texts)
            tolerances = self._extract_pacing_tolerances(pacing)
            results = PacingAnalyzer().check_compliance(stats, pacing, tolerances)
            return [
                {
                    "metric": r.metric,
                    "expected": r.expected,
                    "actual": r.actual,
                    "passed": r.passed,
                    "delta_pct": r.delta_pct,
                }
                for r in results
            ]
        except Exception as e:
            logger.warning("Pacing check failed (non-blocking): %s", e)
            return []

    async def _check_pacing_async(self, chapter_number: int) -> list[dict]:
        """Async pacing compliance — still deterministic (no LLM for pacing)."""
        return self._check_pacing(chapter_number)

    @staticmethod
    def _extract_pacing_tolerances(pacing: dict) -> dict:
        """Pull every `<metric>_tolerance` field out of the pacing dict."""
        out = {}
        for key, val in pacing.items():
            if key.endswith("_tolerance") and isinstance(val, (int, float)) and not isinstance(val, bool):
                out[key] = val
        return out

    def _save_review(self, review: dict) -> None:
        """Save review to project directory."""
        reviews_dir = self._project_dir / "chapter_reviews"
        reviews_dir.mkdir(parents=True, exist_ok=True)
        chapter = review["chapter_number"]
        path = reviews_dir / f"ch{chapter}_review.json"
        tmp = path.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(review, f, ensure_ascii=False, indent=2)
        tmp.replace(path)

    def get_review_data(self, chapter_number: int) -> Optional[dict]:
        """Get review data for a chapter. Returns None if not found."""
        path = self._project_dir / "chapter_reviews" / f"ch{chapter_number}_review.json"
        if not path.exists():
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None

    def set_decision(self, chapter_number: int, decision: str, feedback: str = "") -> bool:
        """Set author's decision on chapter review."""
        if decision not in ("approved", "revise"):
            return False
        review = self.get_review_data(chapter_number)
        if review is None:
            return False
        review["decision"] = decision
        review["decision_feedback"] = feedback
        self._save_review(review)
        return True

    def save_review(self, review: dict) -> None:
        """Public wrapper for saving review data."""
        self._save_review(review)
