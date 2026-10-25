# nebula maintenance scripts

One-off repair and migration scripts that operate directly on the `projects/`
directory. Most are idempotent and default to **dry-run**; pass `--apply` to
actually write.

## `backfill_creative_divergence.py`

One-time migration for v2.x. For projects with `source=canvas` in
`concept_and_dna.json` but no `creative_divergence.json`, creates the compat
file so the Stage 1 prompt guard (`backend/api/stage1_concept.py`
→ `_read_creative_intent`) can read the `prompt` field and proceed.

**Dry-run first:**

```bash
python scripts/backfill_creative_divergence.py
```

**Apply:**

```bash
python scripts/backfill_creative_divergence.py --apply
```

**Custom projects dir:**

```bash
python scripts/backfill_creative_divergence.py --projects-dir /path/to/projects --apply
```

Idempotent — skips projects that already have `creative_divergence.json`.
Only projects whose `concept_and_dna.json` has `source="canvas"` are touched.
