"""nebula v1.6 Phase 4b -- TabooConstraintChecker for L3 taboo constraint detection."""
import re
import logging
import json
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class TabooViolation:
    pattern_name: str       # e.g. "虐主", "全局-元引用", "角色-林峰-禁止示弱"
    layer: str              # "global" | "genre" | "character"
    severity: str           # "error" | "warning"
    matched_text: str       # violating text snippet (~80 chars)
    context: str            # surrounding context (match_start-100 to match_end+100)


# --- Layer 1: Global taboo patterns (hardcoded, zero LLM) ---

_GLOBAL_TABOOS: list[dict] = [
    # Meta-reference -- error
    {"pattern": r"如果这是一本小说", "name": "全局-元引用", "severity": "error"},
    {"pattern": r"作者说", "name": "全局-元引用", "severity": "error"},
    {"pattern": r"读者朋友们", "name": "全局-元引用", "severity": "error"},
    {"pattern": r"本章完", "name": "全局-元引用", "severity": "error"},
    # Meta-reference -- warning
    {"pattern": r"按下不表", "name": "全局-元引用", "severity": "warning"},
    {"pattern": r"暂且不提", "name": "全局-元引用", "severity": "warning"},
    {"pattern": r"后文再续", "name": "全局-元引用", "severity": "warning"},
    # 4th wall breaks -- warning
    {"pattern": r"各位看官", "name": "全局-元引用", "severity": "warning"},
    {"pattern": r"书接上文", "name": "全局-元引用", "severity": "warning"},
    # Real brand names -- error
    # Chinese-aware boundary: \b is ineffective with CJK text (Unicode \w includes hanzi).
    # Use lookbehind/lookahead that assert no adjacent ASCII word chars.
    {"pattern": r"(?<![a-zA-Z0-9])(iPhone|iPad|MacBook|Apple Watch)(?![a-zA-Z0-9])", "name": "全局-真实品牌", "severity": "error"},
    {"pattern": r"(微信|支付宝|抖音|淘宝|百度|京东|美团|滴滴)", "name": "全局-真实品牌", "severity": "error"},
    {"pattern": r"(?<![a-zA-Z0-9])(Nike|Adidas|Starbucks|McDonald'?s?|Google|Microsoft)(?![a-zA-Z0-9])", "name": "全局-真实品牌", "severity": "error"},
    # Real platforms in-story -- error
    # Longer alternatives must come first in alternation groups
    {"pattern": r"(起点中文网|起点|番茄小说|晋江文学城|晋江)", "name": "全局-真实平台", "severity": "error"},
]


# --- Layer 3: Character taboo keyword mapping ---

TABOO_KEYWORD_MAP: dict[str, list[str]] = {
    "禁止说脏话": ["他妈", "混蛋", "废物", "找死", "该死"],
    "禁止主动求助": ["帮忙", "救我", "求求你", "帮帮我", "拜托", "救命"],
    "禁止示弱": ["我不行", "做不到", "放过我", "饶了我", "我输了"],
    "禁止撒谎": ["骗", "假的", "隐瞒", "没说"],
    "禁止背叛": ["出卖", "背叛", "投靠", "反水"],
}


def _extract_context(text: str, match_start: int, match_end: int) -> str:
    """Extract ~200 chars of context around a match."""
    start = max(0, match_start - 100)
    end = min(len(text), match_end + 100)
    return text[start:end]


def _extract_matched(text: str, match_start: int, match_end: int) -> str:
    """Extract ~80 chars of matched text snippet."""
    start = max(0, match_start - 10)
    end = min(len(text), match_end + 70)
    return text[start:end]


class TabooConstraintChecker:
    """L3 taboo constraint detection -- 3 layers, only character taboos use Tier 3 LLM."""

    def check_sync(
        self, scene_text: str, genre_taboos: list[dict], character_taboos: list[dict]
    ) -> list[TabooViolation]:
        """
        Layers 1-2 only (zero LLM).

        Args:
            scene_text: Scene draft text to check
            genre_taboos: List of taboo entries from genre template YAML taboos block
            character_taboos: List of dicts with {"name": str, "taboos": list[str]}

        Returns:
            list[TabooViolation] -- merged results from Layers 1-2
        """
        if not scene_text or not scene_text.strip():
            return []

        violations = []
        violations.extend(self._check_global_taboos(scene_text))
        violations.extend(self._check_genre_taboos(scene_text, genre_taboos))
        return violations

    def _check_global_taboos(self, scene_text: str) -> list[TabooViolation]:
        """Global taboo detection (regex + keyword, zero LLM)."""
        violations = []
        for entry in _GLOBAL_TABOOS:
            pattern = entry["pattern"]
            matches = list(re.finditer(pattern, scene_text))
            for m in matches:
                violations.append(TabooViolation(
                    pattern_name=entry["name"],
                    layer="global",
                    severity=entry["severity"],
                    matched_text=_extract_matched(scene_text, m.start(), m.end()),
                    context=_extract_context(scene_text, m.start(), m.end()),
                ))
        return violations

    def _check_genre_taboos(
        self, scene_text: str, genre_taboos: list[dict]
    ) -> list[TabooViolation]:
        """Genre taboo detection (YAML-driven, zero LLM).

        Supports three types:
        - keyword: simple keyword match against words list
        - sliding_window: keyword density in sliding windows
        - consecutive_match: consecutive paragraphs containing failure keywords
        """
        if not genre_taboos:
            return []

        violations = []
        for taboo in genre_taboos:
            taboo_type = taboo.get("type", "")
            if taboo_type == "keyword":
                violations.extend(self._check_keyword_taboo(scene_text, taboo))
            elif taboo_type == "sliding_window":
                violations.extend(self._check_sliding_window(scene_text, taboo))
            elif taboo_type == "consecutive_match":
                violations.extend(self._check_consecutive_match(scene_text, taboo))
            # Unknown type → skip silently
        return violations

    def _check_keyword_taboo(
        self, scene_text: str, taboo: dict
    ) -> list[TabooViolation]:
        words = taboo.get("words", [])
        if not words:
            return []
        violations = []
        for word in words:
            for m in re.finditer(re.escape(word), scene_text):
                violations.append(TabooViolation(
                    pattern_name=taboo["name"],
                    layer="genre",
                    severity=taboo.get("severity", "error"),
                    matched_text=_extract_matched(scene_text, m.start(), m.end()),
                    context=_extract_context(scene_text, m.start(), m.end()),
                ))
        return violations

    def _check_sliding_window(
        self, scene_text: str, taboo: dict
    ) -> list[TabooViolation]:
        keywords = taboo.get("keywords", [])
        max_chars = taboo.get("max_chars", 300)
        if not keywords or max_chars <= 0:
            return []

        step = max(1, max_chars // 4)
        violations = []
        last_reported_end = -max_chars  # ensure first window always reports

        for start in range(0, len(scene_text), step):
            window_end = min(len(scene_text), start + max_chars)
            window = scene_text[start:window_end]

            hit_count = 0
            first_hit_start = None
            first_hit_end = None
            for kw in keywords:
                for m in re.finditer(re.escape(kw), window):
                    hit_count += 1
                    if first_hit_start is None:
                        first_hit_start = start + m.start()
                        first_hit_end = start + m.end()

            if hit_count >= 3:
                if start >= last_reported_end:
                    last_reported_end = start + max_chars
                    ms = first_hit_start if first_hit_start is not None else start
                    me = first_hit_end if first_hit_end is not None else start + 10
                    violations.append(TabooViolation(
                        pattern_name=taboo["name"],
                        layer="genre",
                        severity=taboo.get("severity", "error"),
                        matched_text=_extract_matched(scene_text, ms, me),
                        context=_extract_context(scene_text, start, window_end),
                    ))

        return violations

    def _check_consecutive_match(
        self, scene_text: str, taboo: dict
    ) -> list[TabooViolation]:
        failure_keywords = taboo.get("failure_keywords", [])
        max_consecutive = taboo.get("max_consecutive", 2)
        if not failure_keywords:
            return []

        # Split paragraphs and track original text offsets to avoid find() ambiguity
        raw_paragraphs = scene_text.split("\n\n")
        para_entries: list[tuple[str, int]] = []  # (text, offset_in_original)
        offset = 0
        for raw in raw_paragraphs:
            stripped = raw.strip()
            if stripped:
                # Find this stripped text within the original raw block
                local_offset = raw.find(stripped)
                para_entries.append((stripped, offset + local_offset))
            offset += len(raw) + 2  # +2 for the \n\n separator

        if not para_entries:
            return []

        # Find all consecutive runs
        hit_runs: list[list[int]] = []
        current_run: list[int] = []

        for i, (para_text, _para_offset) in enumerate(para_entries):
            hit = any(
                re.search(re.escape(kw), para_text)
                for kw in failure_keywords
            )
            if hit:
                current_run.append(i)
            else:
                if len(current_run) >= max_consecutive:
                    hit_runs.append(current_run)
                current_run = []

        # Don't forget trailing run
        if len(current_run) >= max_consecutive:
            hit_runs.append(current_run)

        violations = []
        for run in hit_runs:
            first_para_text, match_start = para_entries[run[0]]
            violations.append(TabooViolation(
                pattern_name=taboo["name"],
                layer="genre",
                severity=taboo.get("severity", "error"),
                matched_text=first_para_text[:80],
                context=_extract_context(scene_text, match_start, match_start + len(first_para_text)),
            ))

        return violations

    async def check_async(
        self, scene_text: str, genre_taboos: list[dict], character_taboos: list[dict]
    ) -> list[TabooViolation]:
        """Full 3-layer check. Layers 1-2 sync + Layer 3 with LLM confirmation."""
        if not scene_text or not scene_text.strip():
            return []

        violations = self.check_sync(scene_text, genre_taboos, character_taboos)
        violations.extend(
            await self._check_character_taboos_async(scene_text, character_taboos)
        )
        return violations

    def _check_character_taboos(
        self, scene_text: str, character_taboos: list[dict]
    ) -> list[TabooViolation]:
        """Character taboo keyword matching (sync, zero LLM).

        Args:
            scene_text: Scene draft text
            character_taboos: [{"name": str, "taboos": [str, ...]}, ...]

        Returns:
            Candidate violations from keyword matching (no LLM confirmation)
        """
        if not character_taboos:
            return []

        violations = []
        for char_entry in character_taboos:
            char_name = char_entry.get("name", "未知角色")
            taboos = char_entry.get("taboos", [])
            if not taboos:
                continue

            for taboo_phrase in taboos:
                keywords = TABOO_KEYWORD_MAP.get(taboo_phrase)
                if not keywords:
                    continue  # unknown taboo phrase → skip

                for kw in keywords:
                    for m in re.finditer(re.escape(kw), scene_text):
                        violations.append(TabooViolation(
                            pattern_name=f"角色-{char_name}-{taboo_phrase}",
                            layer="character",
                            severity="warning",
                            matched_text=_extract_matched(scene_text, m.start(), m.end()),
                            context=_extract_context(scene_text, m.start(), m.end()),
                        ))
        return violations

    async def _check_character_taboos_async(
        self, scene_text: str, character_taboos: list[dict]
    ) -> list[TabooViolation]:
        """Character taboo: keyword match → Tier 3 LLM confirmation.

        LLM unavailable → all keyword candidates pass through (conservative).
        """
        # Get keyword-match candidates first
        candidates = self._check_character_taboos(scene_text, character_taboos)
        if not candidates:
            return []

        try:
            from backend.llm.model_router import get_model_router

            router = get_model_router()

            # Build candidate list text
            candidates_text = "\n".join(
                f"[{i}] {c.pattern_name} | matched: \"{c.matched_text}\" | context: \"{c.context[:100]}\""
                for i, c in enumerate(candidates)
            )

            # Build all character-taboo pairs for the prompt
            char_taboo_lines = []
            for ce in character_taboos:
                cn = ce.get("name", "未知角色")
                ct = ce.get("taboos", [])
                if ct:
                    char_taboo_lines.append(f"  {cn}: {', '.join(ct)}")
            all_char_taboos = "\n".join(char_taboo_lines)

            system_prompt = (
                "你是一位网文编辑。以下是从角色禁忌关键词匹配中检测到的候选违规列表。"
                "判断每个候选是否构成真实的禁忌违规（考虑语境：引述他人、虚构假设、讽刺反语不算违规）。"
                "只输出一个 JSON 对象。"
            )
            user_prompt = (
                f"角色禁忌列表:\n{all_char_taboos}\n\n"
                f"候选违规列表:\n{candidates_text}\n\n"
                '输出 JSON:\n'
                '{"violations": [{"index": <int>, "confirmed": true|false, "reason": "<一句话>"}]}'
            )

            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ]

            result = await router.execute(
                agent_name="reviewer",
                task_name="character_taboo_confirmation",
                messages=messages,
                json_mode=True,
            )

            content = result.get("content", "")
            parsed = json.loads(content) if isinstance(content, str) else content
            confirmed_indices = {
                item["index"]
                for item in parsed.get("violations", [])
                if item.get("confirmed", False)
            }

            # Filter to confirmed only
            return [
                c for i, c in enumerate(candidates)
                if i in confirmed_indices
            ]

        except Exception as e:
            logger.warning("Character taboo LLM confirmation failed, passing all candidates: %s", e)
            # Conservative: pass all candidates through
            return candidates
