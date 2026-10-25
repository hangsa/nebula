# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**nebula** (UI design system: "Nebula Forge") is an AI-powered Creative Narrative Operating System for generating Chinese web novels (网文). It orchestrates multiple LLM agents through a deterministic state-machine backbone to produce long-form fiction with high creativity, narrative tension, and character consistency.

**Core principle:** Deterministic code controls the skeleton; LLMs fill the flesh. Consistency checks, state tracking, and reader metrics are all formula/rule-based — never LLM-blindsided.

**Current state:** Active development on the `nebula` branch (v2.x era). Backend, frontend, and ~140 test files are in place. The codebase is **far past v1.7** — the `v1.7 active` status table in earlier revisions of this file is stale and has been removed.

**Stack:** Python (FastAPI) backend · React 18 + Vite + Tailwind frontend · JSON for project data · YAML for prompts/config · Qdrant + BM25 hybrid search with bge-m3 embeddings (L3 cold memory, optional). UI design system in `frontend/src/components/ds/` is Material 3–inspired (Nebula Forge tokens in `docs/design/webmain/DESIGN.md`).

> **Note:** Spec docs referenced in code comments as `docs/design/storyForge-design-v1.x.md` do not exist on disk; the only authoritative design doc currently present is `docs/design/webmain/DESIGN.md` (UI tokens). Treat code as the source of truth.

## User Flow

A typical session walks two pages:

1. **`/`** — `HomePage` (`frontend/src/pages/HomePage.tsx`) rendered inside `HomeLayout` (`frontend/src/components/layout/HomeLayout.tsx`). Layout: `StatsSidebar` (left rail with global stats + Prompt Plaza / AI Console / More Actions triggers) + `BookShelf` (sortable/filterable **table** of all projects, with bulk-delete). Project creation goes through `CreateProjectModal` (opened from the sidebar or table), then auto-navigates to `/project/:id/workspace?tab=settings`. Replaces the older `InitPage` route.
2. **`/project/:id/workspace`** — `WorkspacePage`. The project's day-to-day cockpit. Layout: `WorkspaceTopBar` with a **two-tab switcher** (`项目设定` = wizard, `正文手稿` = writing — manuscript tab is locked until all 7 wizard steps finish) + tab-specific panel:
   - `tab=settings` → `WorkspaceWizardPanel` (7 steps: `CreativeDivergenceStep` → `ConceptStep` → `WorldStep` → `CharacterStep` → `MapStep` → `OutlineStep` → `ChapterOutlineStep`, all under `frontend/src/components/wizard/`).
   - `tab=manuscript` → `WorkspaceWritingPanel` (`ChapterTreePanel` left · `WritingArea`/`AutopilotMiddlePanel`/`ChapterStreamPanel` center · `ContextPanel` right with `ModeSwitchConfirmModal` for manual ↔ managed switch). Right-column editors (concept/world/character/outline tabs) were **merged into the wizard** in v2.x — `ContextPanel` now only carries diagnostics + export.

Top-level route, **NOT** wrapped in `MainLayout` (its own chrome).

Legacy routes still resolve but redirect: `/project/:id/wizard` → `WizardDeepLinkPage` (redirect-only shell, forwards to `/workspace?tab=settings`). `InitWizardModal` is **DEPRECATED** (kept for 3 test files under `frontend/src/test/`) but no longer used by any active route.

For deep-dive architecture on the wizard ↔ workspace split and the 7-step creative pipeline (Stage 0 divergence → 1 concept → 2 world/character → 3a novel outline → 3b chapter outline → 4 writing), see `docs/ARCHITECTURE.md` and `docs/wizard-inventory.md`. The wizard-inventory doc is verbose but its §9 (Workspace 融合完成) is the authoritative state for the wizard merge.

## Dual-Mode Writing: Manual vs Managed

The workspace supports two writing modes, both backed by `backend/api/stage4_writing.py`:

- **Manual** — User-driven one-shot writes via `ManualStartModal`. Each scene calls `AsyncStage4Executor` directly.
- **Managed (autopilot)** — Long-running autonomous writing loop driven by `backend/conductor/autopilot_*.py`:
  - `AutopilotLoopService` (`backend/conductor/autopilot_loop.py`) — long-lived asyncio service owning per-project runner tasks on `app.state`.
  - `AutopilotRunner` / `AutopilotRunnerAsync` — drives a session's queue one step at a time; Stage 2 swapped RecordingExecutor for the real `AsyncStage4Executor`.
  - `AutopilotSession` + `AutopilotSessionManager` — write-through persistence to `<project>/autopilot/session.json` (atomic .tmp + replace). Crash-recovery: stale `running` sessions (>30s without heartbeat) are downgraded to `paused` on startup.
  - `SceneChunkStore` — SSE chunk buffer keyed by chapter so the cockpit can replay missed chunks on fresh browser connect (without `scene_start` — frontend had a stale-chunk guard that dropped them; fixed via lazy-init `currentSceneRef` in `useChapterStream`).
  - **Circuit breaker integration:** `CircuitBreaker.check(attempt=N)` + auto-pause on `force_pass` threshold (3). Runner retries `write_scene` 3× with 30s+60s backoff; on exhaustion it pauses with `reason="scene_write_failed:..."`, which the cockpit surfaces as a red banner.
  - **Queue semantics:** `seed_queue` deduplicates on restart (without dedup it doubled every restart — fix shipped 2026-07-17). Items where the outline scene is missing get short-circuited as `scene_missing` (no retry/pause).
  - **Pause/resume reasons:** cleared on resume; UI distinguishes `pause_reason="scene_write_failed:..."` from `pause_reason="user_requested"`.

The autopilot SSE channel is the **same broadcaster** that `/chapter-stream` subscribes to — both must be wired to `app.state.autopilot_broadcaster` (see `backend/main.py` lifespan). Instantiating the executor with a private broadcaster silently drops every event ("cockpit shows 等待 AI 输出第一个字 forever" — found and fixed 2026-07-17 on proj_cc4ca4ae).

## Architecture — Three Capability Chains

| Goal | Chain | Systems |
|---|---|---|
| Creative divergence | Creative Chain | CreativeOS (`backend/creative_os/`) + Creative Director Agent |
| Narrative reversals & hooks | Narrative Chain | StoryOS + ReaderOS + Scene Engine + Style Engine |
| Character/plot consistency | Consistency Chain | MemoryOS + State Machines |

### System Components (stable across v1.7 → v2.x)

- **Conductor** — `backend/conductor/`: 8-stage phase state machine (`state_machine.py`), checkpoint/resume (`checkpoint.py`), circuit breaker (`circuit_breaker.py`), branch simulation (`branch_simulator.py`), impact analysis (`impact_analyzer.py`), chapter review (`chapter_review.py`), outline term guard (`outline_term_guard.py`). Now also owns the autopilot stack (see above).
- **CreativeOS** — `backend/creative_os/`: idea/trope pools, mutation engine (4 ops), contradiction engine (5 templates), what-if engine (depth=3 breadth=4), genre fusion engine, novelty evaluator (4 dimensions: market_saturation 30% + trope_similarity 25% + contradiction_depth 25% + discussion_potential 20%).
- **StoryOS** — `backend/story_os/`: 7+1 narrative asset registries (Conflict, Promise, Mystery, Twist, Reveal, Goal, Expectation, Foreshadowing) with cross-registry foreign keys, transactional cascade updates (BFS), conflict detection, `registry_transaction.py` for atomic commit/rollback.
- **MemoryOS** — `backend/memory_os/`: 5-tier memory.
  - **L0 Runtime** (500 tokens, always in context).
  - **L1 Hot** (last 5 chapters + periodic detail re-extraction every 5 chapters).
  - **L2 Warm** (chapter summaries + timeline + relationship graph, ~8K tokens).
  - **L3 Cold** (Qdrant + BM25 hybrid with RRF fusion, optional — degrades gracefully when off).
  - **L4 Narrative** (synced with StoryOS, ~3K tokens).
  - Retrieval priority: L0 → L1 → L4 → L2 → L3.
- **ReaderOS** — `backend/reader_os/`: 7 reader-state metrics (Curiosity, Tension, Satisfaction, Frustration, Fatigue, Addiction, Discussion Potential), all formula-computed, zero LLM calls. Thresholds genre-specific and YAML-driven.
- **Scene Engine** — `backend/scene_engine/`: Scene Schema 2.0, beat patterns, SF_LOG spec (11 types).
- **Style Engine** — `backend/style_engine/`: L1 genre templates (YAML), L2 writing formulas, L3 taboo regex constraints. Owns the Style Sandbox (Tier 3 preview + saved configs).
- **Growth Curve + Workshop** — `backend/growth_curve/`: auto-generator, binder, context injection. **Growth Workshop** (workshop/) adds interactive stage editor, consistency checking (5 rules), agent discussion loop, Character Designer.
- **Semantic Precheck** — `backend/semantic_precheck/`: pre-Fact-Guard log suggestions; never blocks.
- **Agent Layer** — `backend/agents/`: `base_agent`, `planner`, `writer`, `reviewer` (3 guard layers), `storyos_agent`, `summary_archiver`, `creative_director`, `character_designer`.

### New in v1.8 → v2.x

- **Prompt Plaza** — `backend/api/prompt_plaza.py` + `backend/api/prompt_defaults.py` + `frontend/src/components/home/promptPlaza/`. Browse / edit YAML prompts globally. 3-tier override: YAML → Global → Project. Persisted to `config/global_prompt_overrides.json` via `global_prompt_override_store.py`.
- **Meta-decompose (S2)** — Stage 2 第一性拆解 uses a *per-project* specialized prompt instead of the generic `firstness_decompose.yaml`. On S1→S2 the backend runs a meta-prompt LLM (`meta_decompose.yaml`), writes the result to `projects/{id}/prompt_overrides.json` under `firstness_decompose.system_prompt`, then runs the actual decompose with that override. Footer 「重新生成」 and the S2 top edit icon re-use the existing override (no meta re-run). Prompt Plaza hides `firstness_decompose` and exposes `meta_decompose` under 创意发散 — users customize the meta-prompt (which shapes future specialized prompts) but never edit the per-project dynamic prompt directly. Global `firstness_decompose` overrides in `config/global_prompt_overrides.json` are silently superseded once a project has its own override.
- **AI Console** — `backend/api/llm_config_api.py` + `frontend/src/components/aiConsole/`. CRUD for LLM providers / models / API keys. **Writes API keys directly to `backend/.env`**. Supports custom providers via `STORYFORGE_PROVIDER_API_KEY_<X>` env-var prefix (hot-reloadable).
- **Design System (`ds/`)** — `frontend/src/components/ds/`. Material 3 primitives: `BrandHeader`, `DropdownSelect`, `GhostButton`, `PanelCard`, `PhaseIndicator`, `PrimaryButton`, `ProjectTableRow`, `SearchInput`, `SecondaryButton`, `Sidebar`, `SidebarNavItem`, `StatCard`. Tokens in `tokens.ts` + `stages.ts` (STAGE_COLORS / STAGE_LABELS). Barrel `index.ts` exports all 12 primitives + stage constants.
- **Outline Context** — `backend/outline_context/`: `builder.py`, `volumes.py`. Splits the full outline into per-volume chunks for context assembly.
- **Services** — `backend/services/`: `agent_prompt_stores.py`, `global_prompt_override_store.py`, `llm_config.py`, `llm_usage_log.py`, `prompt_override_store.py`.
- **Cross-cutting LLM infra** — `backend/llm/openai_compatible_provider.py`, `mock_provider.py` added; providers can also be added dynamically via the env-var prefix (above).
- **Map System** — `backend/map_system/`(M1–M6 全部落地):9 类地理实体 (Map/Region/Location/Route/POI/LocationState/Footprint/Assertion/ChangeLog),单文件 `map.json` + `map_snapshots/chapter_NNN.json` 章节快照/回滚。Wizard Step 4 (`MapStep.tsx`) 暴露 5 Tab (地点/区域/路线/POI/快照,注意地点 Tab 排第一) + `MermaidMapModal` 拓扑图。`PlannerAgent.generate_map()` 经 Tier-1 prompt (`map_generation.yaml`) 生成初始 5-15 region + 20-40 location。`POST /map/snapshot/{chapter}` + `POST /map/rollback/{chapter}` 提供不可逆回滚。
  - **M4 地图卡注入 (已落地):** `map_system/map_card.py` 拼 4 行 mini-card → `memory_os/memory_coordinator.assemble_for_scene()` 末尾 append 到 l2_context → Writer 在 `scene_writing.yaml` §9.4 看到当前地点 + 角色足迹 + 自查清单。`StoryOSAgent` 解析 `character_location_change` SF_LOG 写 `Map.footprints`,`map_system/extraction.py` 跑 Tier-1 LLM mention 抽取补全。
  - **M5 地理一致性断言 (已落地):** `map_system/assertions.py` 9 条规则 (3 Blocker: route/access/time-budget · 3 Warning: distance/climate/density · 3 Info: alias/faction/poi)。`backend/agents/reviewer.py` 加 9 个 `check_7_geo_*` 方法,`run_fact_guard()` 末尾聚合。POI 不可见约束由 `storyos.list_discoverable_pois_for_card` 控制。
  - **Coerce / 防御性 schema:** `/generate-map` + `/regenerate-map-section` 走 Pydantic coerce (LLM 输出 `LocationType` 大小写 / 枚举漂移是已知 bug,见 commit `3b2c9ad` + `a5ef921`)。
  - **Config:** `strict_geo: false` 默认 OFF,老项目无 `map.json` 不报错;Wizard Settings Panel 有显式 toggle + 上限 + 模式。

## Key Design: SF_LOG Tags

The Writer agent embeds structured log tags (Markdown comment syntax, invisible to readers) in generated text:

```
<!-- SF_LOG character_relation_change char_a="林峰" char_b="苏晓晓" status="裂痕" trigger="争执" -->
<!-- SF_LOG conflict_escalate id="cf_001" new_intensity="critical" trigger="发现证据" -->
<!-- SF_LOG knowledge_gain char="林峰" content="师父的秘密联络记录" source="实验室终端" -->
<!-- SF_LOG mystery_clue id="mys_003" clue="超脑认识观察者文明的符号" -->
<!-- SF_LOG twist_reveal id="tw_001" trigger="终端日志记录" -->
<!-- SF_LOG goal_milestone id="goal_002" progress="T5→T7" -->
<!-- SF_LOG registry_create type="conflict" data='{"owner":"林峰","target":"师父","type":"betrayal"}' -->
```

11 log types: `character_relation_change`, `character_emotion`, `knowledge_gain`, `conflict_escalate`, `mystery_clue`, `twist_reveal`, `expectation_fulfill`, `goal_milestone`, `registry_create`, `character_location_change`, `character_physical_change`.

The StoryOS Agent parses these deterministically with regex (zero LLM), matches them against Writer's pre-declared `registry_changes` in the Scene Schema, and applies cross-registry cascading updates via `RegistryTransactionManager`.

**Cross-registry cascade rules:** Mystery→revealed → Reveal→revealed; Reveal→revealed → Expectation→fulfilled; Twist→revealed → Expectation→ready_to_fulfill; Reveal→revealed → Conflict→escalated.

## Scene Writing Pipeline (per chapter)

```
1. Chapter Outline (Planner)
2. Scene Planning (Writer plans 3-6 scenes, pre-declares registry_changes + required_logs)
3. Scene Writing (Writer: inject MemoryOS context L0→L1→L4→L2→L3, Character States, TensionCurve warnings, log_instructions)
4. Scene Review (Reviewer 3-layer guard):
   - Fact Guard (hard rules, 6 deterministic checks) → pass/retry(max 3)/force-pass
   - Narrative Guard (suggestions, state drift detection) → suggestions only
   - Style Guard (tags) → log only
5. Scene Refining (if Fact Guard blocked, up to 3 retries with auto-generated hints)
6. Chapter Assembly (stitch scenes, check beat density, final review)
7. StoryOS & MemoryOS Update (StoryOS Agent regex-parses logs → updates registries → cascade → MemoryOS L0/L1/L2/L3)
8. ReaderOS Update (recalculate all metrics, zero LLM)
9. Semantic Precheck (v1.7+) — suggests likely-missing log tags, never blocks
```

## Fact Guard — 6 Deterministic Checks

All done in code, no LLM calls:

1. **Timeline continuity** — character locations must be reachable; same-chapter location changes need a log tag.
2. **Character state consistency** — no forbidden behaviors from voice_signature; no knowledge leaks (`unknown_to_character`).
3. **World rules** — power ceilings respected; if cost_required flexibility, cost must be declared via log tag.
4. **Registry compliance** — pre-declared changes cannot reactivate resolved items; referenced entries must exist.
5. **Required logs** — every item in `required_logs` must have a corresponding SF_LOG tag in the text.
6. **Log format validation** — strict regex check for proper SF_LOG tag formatting.

**Circuit breaker:** 3 retries with auto-generated hints → force-pass with compatibility note → optional human notification.

## Character State Machine — Belief Change Triggers

Belief changes are the most strictly guarded state change. Requirements:

- Must originate from `<log>` tags (never LLM inference).
- Need ≥2 independent trigger events in recent chapters (looking back 3 chapters).
- At least 1 trigger event in the current chapter.
- Trigger type must be in the hardcoded whitelist (8 types): `betrayal_experienced`, `death_of_loved_one`, `world_truth_revealed`, `personal_identity_crisis`, `irreversible_loss`, `moral_awakening`, `accumulated_evidence`, `relationship_transformation`.
- `accumulated_evidence` has special rules: ≥3 chapters with evidence AND ≥4 total independent pieces.
- LLM extraction explicitly excludes `belief_change` type — only location/emotion/relationship/knowledge/physical changes.

## Model Tier Strategy

| Tier | Use | Models |
|---|---|---|
| Tier 1 (creative core) | Scene Writing, Mutation Engine, Contradiction Engine, Creative Planning | Claude Opus 4 / DeepSeek V4 |
| Tier 2 (analysis) | Narrative Guard, Character State Machine, WhatIf Engine | Claude Sonnet 4 |
| Tier 3 (auxiliary) | L1 detail re-extraction, NoveltyEvaluator tag extraction, StyleExtractor classification, semantic precheck, style sandbox render | Claude Haiku |
| Tier 0 (deterministic) | Fact Guard, Style Guard, StoryOS Agent, ReaderOS, TensionCurve, Plot State Machine, Growth Workshop consistency | No LLM |

Routing is config-driven via `config/model_tiers.yaml` and `backend/llm/model_router.py`. As of mid-2026, `tiers.<name>.models` whitelist is **gone** — any model listed in `providers.*.models` is selectable in any tier; `write_yaml_atomic` auto-pops the deprecated field on save.

## Token Budget & Context Caching

Per chapter (~3 scenes): ~120K tokens. Per volume (20 chapters): ~2.4M tokens.
v1.7 single-chapter breakdown: 3× Scene Writing (81K) + 3× Narrative Guard (27K) + 1× Character State Machine (9K) + 3× Semantic Precheck (~1.8K, Tier 3) + amortized L1 re-extraction / inspiration router / user edit assist.

**Caching:** Per-chapter cache (survives across scenes in the same chapter): L1 Hot, L4 Narrative, L2 Warm summaries. Per-scene refresh (no caching): Character State Machine. Chapter switch clears all cache. Saves ~60% context assembly overhead.

**Checkpoint & resume:** Scene-level granularity in `.nebula_checkpoint.json` inside each project. Snapshots: L0 Runtime, all StoryOS Registries, all Character States, ReaderOS state. Written after each scene completes (overwrite mode). Recovery replays from the recorded `pipeline_stage`.

## Development Environment

### Run dev services

```bash
# Backend (port 8000). Reads backend/.env; works without keys but LLM calls fail.
# --reload-dir limits watch to backend/; --reload-exclude skips tests.
source venv/bin/activate
uvicorn backend.main:app --reload --reload-dir backend --reload-exclude 'test_*.py' --port 8000

# Frontend (port 5173). Vite proxies /api → http://localhost:8000.
cd frontend
npm run dev
```

Open http://localhost:5173. **Important:** do NOT edit backend `.py` files while a cockpit SSE stream is open — `--reload` will hang waiting for the connection to close (kill + restart). The `--reload-dir backend --reload-exclude 'test_*.py'` flags above prevent reloads when editing docs / claude memory / tests (which would otherwise also hang on any in-flight LLM call). See `feedback_worktree_v19.md` / related project memories for known pitfalls.

### Run tests

```bash
# Backend (pytest; config in pyproject.toml [tool.pytest.ini_options])
source venv/bin/activate
pytest                                              # all (~140 test files)
pytest tests/test_creative_os/                      # subset
pytest tests/test_autopilot_runner.py -k "queue"    # single test / substring match
pytest tests/ -k "ac6 or ac9"                       # v1.7 acceptance tests still relevant
pytest -x                                           # stop on first failure

# Frontend (vitest; jsdom env)
cd frontend
npm test                          # vitest run (one-shot)
npm run test:watch                # vitest watch mode
```

**Vitest cold-cache quirk:** first `npm test` run on `.tsx` files may report `ReferenceError: document is not defined` because jsdom hasn't initialized. Re-run (or `DEBUG="vitest:*"`) to warm the cache. Do **not** edit `vitest.config.ts` to work around this.

### LLM provider config

`backend/config.py` defaults to `deepseek` + `deepseek-chat`. Override via `backend/.env`:

```
llm_provider=deepseek
llm_model=deepseek-chat
deepseek_api_key=...
```

Supported providers (see `backend/llm/`): `anthropic`, `deepseek`, `minimax`, `mock`, plus any custom OpenAI-compatible provider registered via `STORYFORGE_PROVIDER_API_KEY_<X>` env vars (hot-reloadable — used when adding providers through the AI Console UI).

**Precedence gotcha:** pydantic-settings env vars **override `.env` silently**. If you set `MINIMAX_API_KEY=...` in the shell, the `.env` value is ignored. To debug "why is the wrong key being used?", check both the env and the file. Restart the backend after changing `.env`.

**httpx / proxy gotcha:** `httpx` clients default to `trust_env=True`, which routes through the macOS system proxy (`scutil proxy`). If Clash/whatever is off, LLM clients die with `Connection error` even though `curl` works. The fix is to construct httpx clients without proxy trust (see `make_no_proxy_async_client` helpers in `backend/llm/`).

### Maintenance scripts (`scripts/`)

```bash
python scripts/validate_project_genres.py            # check genres for all projects
python scripts/repair_progress_from_drafts.py       # rebuild progress.json from chapter_drafts/ (recovery after autopilot loss)
python scripts/backfill_behavior_examples.py        # one-off backfill for behavior examples data
python scripts/backfill_creative_divergence.py      # one-off backfill for creative_divergence.json
python scripts/migrate_raw_intent_ids_to_names.py   # migrate raw intent ids → names in legacy projects
```

### Frontend design-system imports

```ts
import { PrimaryButton, PanelCard, Sidebar, StatCard } from "@/components/ds";
import { STAGE_COLORS, STAGE_LABELS } from "@/components/ds/stages";
import { COLOR_ROLES, FONT_SIZE_TOKENS } from "@/components/ds/tokens";
```

Use the `ds/` primitives for new UI rather than hand-rolling buttons/cards/sidebars. The barrel re-exports all 12 primitives + stage constants.