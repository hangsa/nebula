"""
nebula v1.6 — RegistryTransactionManager: 叙事资产级联传播事务管理器。

当 StoryOS Agent 更新某个叙事资产状态时，自动级联触发关联资产的状态变更，
并执行三类校验（循环依赖 / 状态冲突 / 互斥冲突）。全部逻辑零 LLM。
"""

import json
import logging
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Optional

from backend.config import settings

logger = logging.getLogger(__name__)


# --- Enums & Data Classes ---


class CascadeTrigger(str, Enum):
    MYSTERY_REVEALED = "mystery_revealed"
    TWIST_REVEALED = "twist_revealed"
    REVEAL_REVEALED = "reveal_revealed"
    PROMISE_FULFILLED = "promise_fulfilled"
    CONFLICT_RESOLVED = "conflict_resolved"


@dataclass
class CascadeStep:
    trigger: CascadeTrigger
    source_asset_type: str       # "mystery" / "twist" / "reveal" / "promise" / "conflict"
    source_asset_id: str
    target_asset_type: str
    target_asset_id: str
    new_status: str
    reason: str = ""


@dataclass
class CascadeResult:
    success: bool
    steps_executed: list[CascadeStep] = field(default_factory=list)
    blocked_steps: list[CascadeStep] = field(default_factory=list)
    cycle_detected: bool = False
    cycle_path: list[str] = field(default_factory=list)
    conflict_details: list[str] = field(default_factory=list)
    orphaned_mysteries: list[str] = field(default_factory=list)


# --- RegistryTransactionManager ---


class RegistryTransactionManager:
    """
    叙事资产级联传播事务管理器。

    核心行为：
    1. 接收一个触发事件（如 "mystery mys_001 → revealed"）
    2. 展开所有级联影响（BFS 遍历关联资产）
    3. 每步执行前运行三类校验（循环依赖 / 状态冲突 / 互斥冲突）
    4. 全部通过则原子写入，任一失败则全部回滚
    """

    # 级联规则表：trigger → [(target_asset_type, new_status), ...]
    CASCADE_RULES: dict[CascadeTrigger, list[tuple[str, str]]] = {
        CascadeTrigger.MYSTERY_REVEALED: [
            ("reveal", "revealed"),
            ("expectation", "fulfilled"),
        ],
        CascadeTrigger.TWIST_REVEALED: [
            ("expectation", "ready_to_fulfill"),
        ],
        CascadeTrigger.REVEAL_REVEALED: [
            ("conflict", "escalated"),
            ("expectation", "fulfilled"),
        ],
        CascadeTrigger.PROMISE_FULFILLED: [
            ("expectation", "fulfilled"),
        ],
        CascadeTrigger.CONFLICT_RESOLVED: [
            # 特殊处理：检查 Mystery 依赖 → 标记 orphaned（警告，不阻断）
        ],
    }

    # 禁止状态转换：(from_status, to_status)
    FORBIDDEN_TRANSITIONS: set[tuple[str, str]] = {
        ("resolved", "active"),
        ("revealed", "foreshadowing"),
        ("fulfilled", "accumulating"),
    }

    # 注册表文件名映射
    REGISTRY_FILES = {
        "conflict": "conflicts.json",
        "mystery": "mysteries.json",
        "twist": "twists.json",
        "goal": "goals.json",
        "promise": "promises.json",
        "reveal": "reveals.json",
        "expectation": "expectations.json",
        "foreshadowing": "foreshadowing.json",
    }

    def __init__(self, projects_dir: Optional[Path] = None):
        self._projects_dir = Path(projects_dir) if projects_dir else settings.projects_dir

    # --- Public API ---

    def propagate(
        self,
        project_id: str,
        trigger: CascadeTrigger,
        source_asset_type: str,
        source_asset_id: str,
    ) -> CascadeResult:
        """
        执行级联传播。

        Step 1: 展开级联路径（BFS）
        Step 2: 对每步执行校验
        Step 3: 原子写入或回滚
        """
        storyos_dir = self._projects_dir / project_id / "storyos"

        # 1. Expand cascade path
        steps = self._expand_cascade_path(
            trigger, source_asset_type, source_asset_id, storyos_dir
        )

        if not steps:
            return CascadeResult(success=True)

        # 2. Validate each step
        executed: list[CascadeStep] = []
        blocked: list[CascadeStep] = []
        cycle_detected = False
        cycle_path: list[str] = []
        conflict_details: list[str] = []

        visited_in_chain: set[str] = {source_asset_id}

        for step in steps:
            # Check cycle
            if step.target_asset_id in visited_in_chain:
                cycle_detected = True
                cycle_path = list(visited_in_chain) + [step.target_asset_id]
                conflict_details.append(
                    f"循环依赖检测：{step.target_asset_id} 在级联路径中重复出现，"
                    f"路径：{' → '.join(cycle_path)}"
                )
                blocked.append(step)
                continue

            # Check forbidden transition
            current_asset = self._read_asset(
                storyos_dir, step.target_asset_type, step.target_asset_id
            )
            if current_asset:
                old_status = current_asset.get("status", "")
                transition = (old_status, step.new_status)
                if transition in self.FORBIDDEN_TRANSITIONS:
                    conflict_details.append(
                        f"禁止状态转换：{step.target_asset_type}/{step.target_asset_id} "
                        f"「{old_status}」→「{step.new_status}」"
                    )
                    blocked.append(step)
                    continue

            # Check mutual exclusion (Twist mutual exclusion)
            if step.source_asset_type == "twist" and step.target_asset_type == "twist":
                mx_conflict = self._check_twist_mutual_exclusion(
                    storyos_dir, step.source_asset_id, step.target_asset_id
                )
                if mx_conflict:
                    conflict_details.append(mx_conflict)
                    blocked.append(step)
                    continue

            visited_in_chain.add(step.target_asset_id)
            executed.append(step)

        # 3. Atomic commit
        if executed:
            try:
                self._atomic_commit(storyos_dir, executed)
            except Exception as e:
                logger.error("Cascade atomic commit failed: %s", e)
                return CascadeResult(
                    success=False,
                    steps_executed=[],
                    blocked_steps=steps,
                    conflict_details=[f"原子写入失败：{e}"],
                )

        result = CascadeResult(
            success=len(blocked) == 0,
            steps_executed=executed,
            blocked_steps=blocked,
            cycle_detected=cycle_detected,
            cycle_path=cycle_path,
            conflict_details=conflict_details,
        )

        self._log_cascade(
            storyos_dir, trigger, source_asset_type, source_asset_id,
            executed, blocked, cycle_detected,
        )

        return result

    # --- Cascade Path Expansion (BFS) ---

    def _expand_cascade_path(
        self,
        trigger: CascadeTrigger,
        source_type: str,
        source_id: str,
        storyos_dir: Path,
    ) -> list[CascadeStep]:
        """BFS 遍历关联资产，展开完整级联路径。Queue 条目携带其自身的锚定源。"""
        rules = self.CASCADE_RULES.get(trigger, [])
        if not rules:
            return []

        source_asset = self._read_asset(storyos_dir, source_type, source_id)
        if not source_asset:
            return []

        steps: list[CascadeStep] = []
        # queue: (target_type, new_status, anchor_type, anchor_id)
        queue: list[tuple[str, str, str, str]] = [
            (target_type, new_status, source_type, source_id)
            for target_type, new_status in rules
        ]
        # Prevent duplicate sub-cascade expansion for (type, id) pairs
        visited_anchors: set[tuple[str, str]] = {(source_type, source_id)}

        while queue:
            target_type, new_status, anchor_type, anchor_id = queue.pop(0)

            # Find linked assets from the intermediate anchor, not the original source
            linked_ids = self._find_linked_assets(
                storyos_dir, anchor_type, anchor_id, target_type
            )

            for linked_id in linked_ids:
                step = CascadeStep(
                    trigger=trigger,
                    source_asset_type=anchor_type,
                    source_asset_id=anchor_id,
                    target_asset_type=target_type,
                    target_asset_id=linked_id,
                    new_status=new_status,
                    reason=f"级联触发：{anchor_type}/{anchor_id} → {trigger.value}",
                )
                steps.append(step)

                # BFS: if this target can itself trigger further cascades,
                # enqueue sub-cascades anchored from the newly found asset
                sub_rules = self._get_sub_cascade(target_type, new_status)
                anchor_key = (target_type, linked_id)
                if anchor_key not in visited_anchors:
                    visited_anchors.add(anchor_key)
                    for sub_type, sub_status in sub_rules:
                        queue.append((sub_type, sub_status, target_type, linked_id))

        return steps

    def _get_sub_cascade(
        self, asset_type: str, new_status: str
    ) -> list[tuple[str, str]]:
        """Determine if a status change triggers further cascade rules."""
        trigger_map = {
            ("reveal", "revealed"): CascadeTrigger.REVEAL_REVEALED,
            ("mystery", "revealed"): CascadeTrigger.MYSTERY_REVEALED,
            ("twist", "revealed"): CascadeTrigger.TWIST_REVEALED,
            ("promise", "fulfilled"): CascadeTrigger.PROMISE_FULFILLED,
        }
        sub_trigger = trigger_map.get((asset_type, new_status))
        if sub_trigger:
            return self.CASCADE_RULES.get(sub_trigger, [])
        return []

    def _find_linked_assets(
        self,
        storyos_dir: Path,
        source_type: str,
        source_id: str,
        target_type: str,
    ) -> list[str]:
        """Find target_type assets linked to the source asset."""
        all_targets = self._read_registry(storyos_dir, target_type)
        linked: list[str] = []

        for asset in all_targets:
            asset_id = asset.get("id", "")
            if not asset_id:
                continue

            # Check for cross-references
            related_mystery = asset.get("related_mystery") or asset.get("mystery_id")
            related_twist = asset.get("related_twist") or asset.get("twist_id")
            related_conflict = asset.get("related_conflict") or asset.get("conflict_id")
            related_promise = asset.get("related_promise") or asset.get("promise_id")
            related_reveal = asset.get("related_reveal") or asset.get("reveal_id")

            refs = [
                related_mystery, related_twist, related_conflict,
                related_promise, related_reveal,
            ]

            if source_id in [r for r in refs if r]:
                linked.append(asset_id)

        return linked

    # --- Validation ---

    def _check_twist_mutual_exclusion(
        self, storyos_dir: Path, twist_id_a: str, twist_id_b: str
    ) -> Optional[str]:
        """Check if two twists linked to the same mystery have conflicting statuses."""
        if twist_id_a == twist_id_b:
            return None

        twist_a = self._read_asset(storyos_dir, "twist", twist_id_a)
        twist_b = self._read_asset(storyos_dir, "twist", twist_id_b)

        if not twist_a or not twist_b:
            return None

        mystery_a = twist_a.get("related_mystery") or twist_a.get("mystery_id", "")
        mystery_b = twist_b.get("related_mystery") or twist_b.get("mystery_id", "")

        if mystery_a and mystery_a == mystery_b:
            status_a = twist_a.get("status", "")
            status_b = twist_b.get("status", "")
            # If one is revealed and the other is still planned, they conflict
            conflicting_pairs = [
                ({"revealed"}, {"planned", "foreshadowing", "hidden"}),
                ({"revealed"}, {"revealed"}),  # Can't both be revealed for same mystery
            ]
            for set_a, set_b in conflicting_pairs:
                if status_a in set_a and status_b in set_b:
                    return (
                        f"互斥资产冲突：Twist {twist_id_a}（{status_a}）和 "
                        f"Twist {twist_id_b}（{status_b}）关联同一 Mystery {mystery_a}，"
                        f"两者状态互斥"
                    )

        return None

    def check_orphaned_mysteries(
        self, project_id: str, conflict_id: str
    ) -> list[str]:
        """
        Conflict → resolved: 检查是否有未揭示的 Mystery 依赖此冲突。
        返回 orphaned mystery ID 列表（警告，不阻断）。
        """
        storyos_dir = self._projects_dir / project_id / "storyos"
        mysteries = self._read_registry(storyos_dir, "mystery")
        orphaned: list[str] = []

        for m in mysteries:
            if m.get("status") in ("revealed", "resolved"):
                continue
            related_conflict = m.get("related_conflict") or m.get("conflict_id", "")
            if related_conflict == conflict_id:
                orphaned.append(m.get("id", ""))

        if orphaned:
            logger.warning(
                "Conflict %s resolved, but %d mysteries still depend on it: %s",
                conflict_id, len(orphaned), orphaned,
            )

        return orphaned

    # --- Atomic Commit ---

    def _atomic_commit(
        self, storyos_dir: Path, steps: list[CascadeStep]
    ) -> None:
        """原子写入所有级联步骤，写入前备份原始状态。"""
        # Group steps by target asset type
        updates: dict[str, dict[str, str]] = {}  # asset_type → {asset_id: new_status}
        for step in steps:
            if step.target_asset_type not in updates:
                updates[step.target_asset_type] = {}
            updates[step.target_asset_type][step.target_asset_id] = step.new_status

        # Backup original state for each affected registry
        backups: dict[str, list[dict]] = {}
        for asset_type in updates:
            backups[asset_type] = deepcopy(
                self._read_registry(storyos_dir, asset_type)
            )

        try:
            # Apply all updates
            for asset_type, asset_updates in updates.items():
                items = self._read_registry(storyos_dir, asset_type)
                for item in items:
                    asset_id = item.get("id", "")
                    if asset_id in asset_updates:
                        item["status"] = asset_updates[asset_id]
                        item["cascade_updated_at"] = datetime.utcnow().isoformat()
                self._write_registry(storyos_dir, asset_type, items)
        except Exception:
            # Rollback
            for asset_type, backup_data in backups.items():
                self._write_registry(storyos_dir, asset_type, backup_data)
            raise

    # --- Registry I/O ---

    def _read_registry(self, storyos_dir: Path, registry_type: str) -> list[dict]:
        filename = self.REGISTRY_FILES.get(registry_type, f"{registry_type}s.json")
        path = storyos_dir / filename
        if not path.exists():
            return []
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    def _write_registry(
        self, storyos_dir: Path, registry_type: str, data: list[dict]
    ) -> None:
        filename = self.REGISTRY_FILES.get(registry_type, f"{registry_type}s.json")
        storyos_dir.mkdir(parents=True, exist_ok=True)
        path = storyos_dir / filename
        tmp = path.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        tmp.replace(path)

    def _read_asset(
        self, storyos_dir: Path, asset_type: str, asset_id: str
    ) -> Optional[dict]:
        items = self._read_registry(storyos_dir, asset_type)
        for item in items:
            if item.get("id") == asset_id:
                return item
        return None

    # --- Logging ---

    def _log_cascade(
        self,
        storyos_dir: Path,
        trigger: CascadeTrigger,
        source_type: str,
        source_id: str,
        executed: list[CascadeStep],
        blocked: list[CascadeStep],
        cycle_detected: bool,
    ) -> None:
        """写入级联传播日志到 cascade_log.jsonl。"""
        log_entry = {
            "timestamp": datetime.utcnow().isoformat(),
            "trigger": trigger.value,
            "source": f"{source_type}:{source_id}",
            "steps_executed": len(executed),
            "steps_blocked": len(blocked),
            "cycle_detected": cycle_detected,
            "blocked_reason": (
                "; ".join(
                    f"{s.target_asset_type}/{s.target_asset_id}: {s.reason}"
                    for s in blocked
                )
                if blocked
                else ""
            ),
        }

        log_path = storyos_dir / "cascade_log.jsonl"
        storyos_dir.mkdir(parents=True, exist_ok=True)
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(log_entry, ensure_ascii=False) + "\n")
