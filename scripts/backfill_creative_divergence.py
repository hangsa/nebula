"""Backfill creative_divergence.json for projects committed before v2.x.

nebula v2.x writes both `concept_and_dna.json` and
`creative_divergence.json` on /commit (dual-write for Stage 1 guard compat —
see `backend/api/stage1_concept.py:_read_creative_intent`). Projects committed
before v2.x only have `concept_and_dna.json`; this script backfills the compat
file so the Stage 1 prompt guard can read `prompt` and proceed.

Idempotent — skips projects that already have the file.

Usage:
    python scripts/backfill_creative_divergence.py
    python scripts/backfill_creative_divergence.py --apply
    python scripts/backfill_creative_divergence.py --projects-dir /path/to/projects --apply

Default is dry-run; pass --apply to actually write.
"""
import argparse
import json
import sys
from pathlib import Path


def migrate_one(projects_dir: Path, project_id: str, apply: bool) -> tuple[bool, str]:
    """Backfill creative_divergence.json for one project.

    Returns (migrated, message) — migrated=True if a write happened (or
    would happen in dry-run). message is a human-readable summary.
    """
    project_dir = projects_dir / project_id
    cd_path = project_dir / "concept_and_dna.json"
    cd_compat_path = project_dir / "creative_divergence.json"

    if not cd_path.exists():
        return False, f"  skip {project_id} (no concept_and_dna.json)"
    if cd_compat_path.exists():
        return False, f"  skip {project_id} (creative_divergence.json exists)"

    try:
        cd = json.loads(cd_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return False, f"  skip {project_id} (concept_and_dna.json unreadable: {exc})"

    if cd.get("source") != "canvas":
        return False, f"  skip {project_id} (source={cd.get('source')!r}, not canvas)"

    # Extract prompt from canvas_state.json raw_intent (or empty fallback).
    prompt = ""
    canvas_state_path = project_dir / "creative_os" / "canvas_state.json"
    if canvas_state_path.exists():
        try:
            canvas = json.loads(canvas_state_path.read_text(encoding="utf-8"))
            raw_intent = canvas.get("raw_intent") or {}
            prompt = (raw_intent.get("prompt", "") or "")
        except Exception as exc:
            print(
                f"  warn {project_id}: canvas_state.json unreadable, prompt='' ({exc})",
                file=sys.stderr,
            )

    canvas_snapshot = cd.get("canvas_snapshot", {})

    cd_compat = {
        "prompt": prompt,
        "variants": [],
        "selected_id": None,
        "selected_at": canvas_snapshot.get("committed_at", ""),
        "source": "canvas",
    }

    if apply:
        cd_compat_path.write_text(
            json.dumps(cd_compat, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return True, f"  wrote {project_id} (prompt={len(prompt)} chars)"
    return True, f"  would write {project_id} (prompt={len(prompt)} chars)"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument(
        "--projects-dir",
        type=Path,
        default=Path("projects"),
        help="Path to projects directory (default: ./projects)",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually write files (default: dry-run)",
    )
    args = parser.parse_args()

    projects_dir = args.projects_dir.resolve()
    if not projects_dir.exists():
        print(f"ERROR: projects-dir does not exist: {projects_dir}", file=sys.stderr)
        return 2

    mode = "APPLY" if args.apply else "dry-run"
    print(f"Backfill creative_divergence.json ({mode})")
    print(f"  projects_dir = {projects_dir}")
    print()

    migrated = 0
    skipped = 0
    for project_dir in sorted(projects_dir.iterdir()):
        if not project_dir.is_dir():
            continue
        ok, msg = migrate_one(projects_dir, project_dir.name, args.apply)
        print(msg)
        if ok:
            migrated += 1
        else:
            skipped += 1

    print()
    print(
        f"Summary: {migrated} {'written' if args.apply else 'to write'}, {skipped} skipped"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
