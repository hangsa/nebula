# Wizard → Workspace 融合 — 现状梳理

> 目标：将 InitWizardModal 的六个步骤（概念讨论 / 世界观 / 角色设计 / 地图系统 / 全书大纲 / 章节大纲）的能力融入 `/project/:id/workspace` 工作台，使工作台成为单一入口。
> 本文档梳理**当前**的页面信息、UI 元素、布局和数据所有权，作为后续融合规划的参考。

---

## 1. 路由与入口

| 路由 | 容器 | 备注 |
|---|---|---|
| `/project/:id/wizard` | `WizardDeepLinkPage` → `InitWizardModal` | `resume=true`：直接跳到 `Math.max(...completedSteps)` |
| `/project/:id/workspace` | `WorkspacePage`（**不嵌在 MainLayout**） | 工作台，自带 chrome |
| `/` 首页 | `InitWizardModal`（`resume=false`） | 新建项目后从首页继续时也开同一个 modal |

新建项目 → 首页"查询"流程中，**用户会在 wizard 与 workspace 之间切换两次**：写完概念回首页，进工作台后又能在右侧栏修改概念。

---

## 2. Wizard 步骤详情

### 2.0 编排器 — `InitWizardModal.tsx`（343 行）

- **形态**：全屏 overlay（`fixed inset-0 z-50 bg-black/70`），内部 `max-w-6xl max-h-[90vh]` flex-column
- **三段式结构**：header（标题 + 关闭） / `WizardSteps`（步骤条） / `<main>` 当前步骤内容（可滚动） / footer（`上一步` + `RegenerateStatusBadge` + 动态按钮）
- **Footer 动态按钮**：由各步骤通过 `setNextHandler / setSaveHandler / setRegenerateHandler` 注册；标签包括「重新生成 / 保存修改 / 确认修改并继续」
- **预填**：挂载时并发拉取 `getConcept / getWorld / getCharacter / getNovelOutline / getOutline`，命中已有内容即跳过该步生成
- **完成**：`finishWizard` → `api.advance(projectId, "STAGE4")` → `wizard.reset()` → 跳 `/workspace`
- **状态机**：`status ∈ {idle, generating, completed, error}` + `regenerateState ∈ {idle, busy, success, failure}`（后两态会在 TTL 后自动清除）

### 2.0 上下文 — `WizardContext.tsx`（504 行）

- `WizardData` 字段：`concept` / `story_dna` / `world` / `characters` / `novel_outline` / `chapter1_outline` / `chapter_outline_progress`（`{done,total,last_user_modifications}`，断点用）
- 持久化到 `sessionStorage`（`nebula.wizard.state.<projectId>`，瞬时字段排除）
- 关键工具：
  - `STEP_DATA_KEY_TO_STEP` — 重新生成早期步骤时**自动清空下游步骤的数据**（防脏数据）
  - `jumpToStep` / `markStepGenerated` / `saveStep` / `skipStep` / `hydrateFromFiles`
  - `setNextHandler / setRegenerateHandler / setSaveHandler` — 各步骤通过这些插槽注册 footer 按钮行为

### 2.0 步骤条 — `WizardSteps.tsx`（49 行）

- 6 个等宽圆形按钮：1·概念讨论 / 2·世界观 / 3·角色设计 / 4·地图系统 / 5·全书大纲 / 6·章节大纲
- 已完成步骤显示 ✓；可达步骤可点击跳转；不可达步骤 disabled

### 2.1 概念讨论 — `ConceptStep.tsx`（290 行）

- **职责**：生成/编辑项目概念 + Story DNA 核心矛盾
- **数据文件**：`concept_and_dna.json`
  - `concept.{title, genre, premise, tone, theme, target_audience, style_template}`
  - `story_dna.core_contradiction.{statement, side_a, side_b}`
  - `story_dna.value_stack[]`
- **UI 元素**：
  - 章节"概念信息"：标题、题材（dropdown）、创作意图、主旨、目标读者、风格模板 — 均为 inline 编辑
  - 章节"核心矛盾"：statement（textarea）+ side_a / side_b（input）
  - 每个章节有一个 `SectionRegenerateButton`（按章节重生）
  - `RegenerateModal`（弹窗确认收集修改意见）
  - loading overlay + error banner
- **API**：`generateConcept` / `updateConcept` / `regenerateConceptSection` / `advance → STAGE2`
- **WizardContext**：注册 regen / save / next handlers；`conceptRef / dnaRef` 双缓冲本地状态

### 2.2 世界观 — `WorldStep.tsx`（599 行）

- **职责**：生成/编辑世界观（时代地理 / 力量体系 / 世界规则 / 势力分布）
- **数据文件**：`world.json`
  - 顶层：`era`, `geography`, `era_social_structure`, `era_cultural_history`, `core_rules[]`
  - `power_systems[]`：`{name, description, stages[], core_rules[], ceilings[], cost_system}`
  - `factions[]`：`{name, type, goal, relations}`
- **UI 元素**：4 个有边框的 section card，每 card 含若干 `TagEditor`（用于 stages / rules / ceilings / taboos / sub_tags）
- **关键交互**：
  - 力量体系支持 add/remove 卡片；add 后**立即落盘**（slot 索引必须稳定）
  - `normalizeLegacyWorld()` — 旧版单数 `power_system` 折叠进数组
  - 4 个章节各自独立重生
- **API**：`generateWorld` / `updateWorld` / `regenerateWorldSection`（按章节） / `regeneratePowerSystemItem`

### 2.3 角色设计 — `CharacterStep.tsx`（750 行，体积最大）

- **职责**：生成默认 6 人（1 主角 + 2 反派 + 3 配角），编辑每个角色的全套人设
- **数据文件**：`characters.json` → `{characters[], current}`
- **每角色字段**：
  - `id, name, character_type (protagonist|antagonist|supporting|mentor), is_core_character`
  - `personality.{core_traits, beliefs, desires, fears, values}`
  - `voice_signature.{speech_style, thought_patterns, taboos[], behavior_examples[]}`
  - `current_state.{location, physical_condition, emotional, known_secrets[]}`
  - `unknown_to_character[]`
  - `relations` — `Record<otherId, {status, history, last_update_chapter}>`（`CharacterRelationsEditor`）
- **UI 元素**（每角色卡）：基础信息 / 人格层（5 个 TagEditor）/ 声音签名（含 `BehaviorExamplesSection`）/ 当前状态 / 角色不知道的事 / 角色关系
- **全局按钮**：添加主角/反派/配角/导师、删除（带级联提示 modal）、核心角色 checkbox
- **API**：`generateCharacter`（**串行** — 读-改-写竞态）/ `updateCharacter` / `regenerateCharacterSection`（5 字段）/ `regenerateCharacterExamples` / `deleteCharacter` / `advance → STAGE3`
- **两个 RegenerateModal**：批量 + 单卡行为示例

### 2.4 地图系统 — `MapStep.tsx`（23 行）

- **占位**：仅展示 `map` 图标 + "地图系统功能即将推出，可在工作台内补做此步" + 「跳过」按钮
- **数据**：暂未对接后端
- **WizardContext**：`skipStep(4)`

### 2.5 全书大纲 — `OutlineStep.tsx`（568 行）

- **职责**：生成/编辑全书卷-章结构、核心冲突、主角成长弧线、关键情节点
- **数据文件**：`novel_outline.json`
  - `core_conflict_theme`
  - `volumes[]`：`{name, chapter_range, summary, key_events[]}`
  - `mc_growth_arc[]`：`{label, target_chapter_range, description}`
  - `key_plot_points[]`：`{title, must_appear_in_volume, description, trigger_chapter_hint}`
  - `generated_at, updated_at`
- **UI 元素**：纵向章节；卷行含 name / range / 删除 / `AutoTextarea`（summary）/ `key_events` 增删；loading overlay 显示"第N次"重试计数（`runWithGuardRetry`）
- **API**：`generateNovelOutline` / `updateNovelOutline` / `regenerateNovelOutlineSection`（4 字段）

### 2.6 章节大纲 — `ChapterOutlineStep.tsx`（528 行）

- **职责**：批量生成第一卷每章的细纲（含场景方案），支持暂停/继续 + 单章重生
- **数据文件**：`outline.json`
  - `chapters[]`：`{chapter_number, title, theme?, scene_plan[]}`
  - `scene_plan[]`：`{scene_number, goal, conflict, emotional_arc, narrative_role, beat_type}`
- **断点状态**：保存在 wizard sessionStorage（不落盘），字段 `{done, total, last_user_modifications}`
- **UI 元素**：
  - 加载中：旋转图标 + 第N次重试 + 「暂停」按钮 + "第 X/N" 进度
  - 暂停后：「已生成 done/total 章，剩余 N 章未生成 → 继续生成」
  - 已生成章节：每章一个 card，含 title input + 「重新生成」
  - 完成视图：总章数 + 总场景数 + "完成 → 进入工作台"
- **作用域推导**：`computeFirstVolumeEnd` / `computePlannedTotal`（`utils/outline.ts`），从 `novel_outline.volumes[0].chapter_range` 推出批量
- **API**：`generateOutline` / `updateOutline` / `regenerateChapterOutlineRange` / `advance → STAGE4`

### 2.7 子组件 — `BehaviorExamplesSection.tsx`（144 行）

- 编辑 `voice_signature.behavior_examples[]`：`{situation, action, speech_sample}`
- 「AI 重新生成」回调带 per-card 旋转图标

### 2.8 子组件 — `CharacterRelationsEditor.tsx`（113 行）

- 编辑 `character.relations` —— 排除自己 + 已关联的角色
- `STATUS_OPTIONS = {neutral, ally, enemy, family, rival, mentor}`

---

## 3. 工作台相关面板

### 3.1 总览 — `WorkspacePage.tsx`（727 行）

- **形态**：`h-screen flex flex-col`，顶 `WorkspaceTopBar`，下 `WorkspaceLayout`（三列）
- **状态**：
  - `mode ∈ {manual, managed}`（持久化）
  - `currentChapter` / `currentScene`（`"${ch}-${scene}"`，URL `?chapter=N&scene=M` 同步）
  - `chapterStatus`（`completed | writing | planned | pending`）+ `sceneStatus`（`Record<sceneId, boolean>`）
  - `content` / `lastSavedContent` / `reloadKey`（编辑器刷新触发）
- **写入触发**：所有右侧栏编辑器 save → `setReloadKey(k=>k+1)` → 触发重新拉取
- **Modals**：`ModeSwitchConfirmModal`（模式切换）/ `ManagedStartModal`（托管模式配置）/ `AddChaptersModal`（追加章节）/ `PromptPlazaModal` / `AIConsoleModal` / 初始化项目确认 `ConfirmDialog`
- **API**：起步拉 `getProjectStatus / getOutline / getSceneDraft / getStage4Progress / getNovelOutline`；运行时 `repairProgress / generateOutline / writeScene / updateSceneDraft / factGuard / stopAutopilotSession`

### 3.2 三列布局 — `WorkspaceLayout.tsx`

- 左列 260px（可拖拽改宽）—— `ChapterTreePanel`
- 中列 min 400px（手动模式）/ 全宽（托管模式）—— `WritingArea` 或 `AutopilotMiddlePanel`
- 右列 360px（可拖拽改宽）—— `ContextPanel`
- 列宽持久化到 `localStorage` "nebula.workspace.column-widths"
- 双击 handle 折叠 / 展开

### 3.3 右栏 7 Tab — `ContextPanel.tsx`（154 行）

- **Tab 顺序**：概念 / 世界观 / 角色 / 大纲 / 章节大纲 / 诊断 / 导出
- **前 5 个 Tab**：挂载 `components/workspace/editors/` 下的 5 个编辑器
- **6-7 Tab**：`DiagnosisSummary` / `ExportSummary`
- 顶部 tab 横滑条 + 可选"只读"banner + 滚动内容区
- 每次 save 后 `reloadKey++` 触发整体重拉

> ⚠️ **与 wizard 的数据重叠**：右侧栏 1-5 Tab 编辑的就是 wizard 1-5 步生成的同一批 JSON（`concept_and_dna.json / world.json / characters.json / novel_outline.json / outline.json`）。当前用户需要在两处都能编辑，且能跨页同步。

### 3.4 中栏 手动模式 — `WritingArea.tsx`（239 行）

- 顶部：章节标题 + scene id + 可折叠大纲块（主题/目标/冲突/情绪弧线/叙事角色/拍点）
- 主体：`<textarea flex-1>`
- 底部：字数 + 按钮（重新生成 / Fact Guard / 保存草稿）
- 拥有 `RegenerateModal`（收集用户修改意见）
- 空状态：「前往生成大纲」按钮
- 折叠状态持久化 `localStorage` "nebula.workspace.outline-collapsed"

### 3.5 中栏 托管模式 — `AutopilotMiddlePanel.tsx`（579 行）

- 3 Tab：驾驶舱 / 仪表盘 / 监控日志
- **驾驶舱**：状态徽章（运行中/已暂停/已停止）+ SSE 状态 + 当前任务 + 进度条 + 队列位置 + start/pause/resume/stop/rollback + `ChapterStreamPanel`
- **失败 banner**：解析 `pause_reason="scene_write_failed:..."` 给出 retry/stop CTA
- **数据源**：`useAutopilotSession(projectId)` — `session / events / sseStatus`
- 事件类型：`snapshot / session_start / session_stop / task_start / task_complete / task_fail / decision / circuit_open / circuit_close / queue_add / queue_remove`

### 3.6 实时流 — `ChapterStreamPanel.tsx`（107 行）

- SSE 驱动的当前场景文本流，含 streaming cursor + 状态徽章
- 自动滚到底（用户上滚则取消）

### 3.7 左栏 — `ChapterTreePanel.tsx`（193 行）

- 卷→章→场景树，状态徽章 ✓/✎/○，场景草稿点 ●/○
- 当前章节自动展开其场景
- 模式相关按钮：手动模式显示 + 新章节；托管模式隐藏

### 3.8 托管启动 — `ManagedStartModal.tsx`（242 行）

- 5 个 radio group：推进范围（全部 / 区间）/ 章节范围 / 节奏 / 策略 / 通知
- 防抖的 `rangePreview` + 已完成章节的 `ManagedStartConfirmDialog`

---

## 4. 数据文件所有权

| 文件 | Wizard 写者 | Workspace 读者/写者 |
|---|---|---|
| `concept_and_dna.json` | ConceptStep (wizard 1) | `ConceptEditor` (ContextPanel Tab 1) |
| `world.json` | WorldStep (wizard 2) | `WorldEditor` (ContextPanel Tab 2) |
| `characters.json` | CharacterStep (wizard 3) | `CharacterEditor` (ContextPanel Tab 3) |
| `novel_outline.json` | OutlineStep (wizard 5) | `NovelOutlineEditor` (Tab 4)；`ChapterOutlineStep` 通过 volumes[0].chapter_range 推批量 |
| `outline.json` | ChapterOutlineStep (wizard 6) | `ChapterOutlineEditor` (Tab 5)；`ChapterTreePanel` 用作左树源；`WritingArea` 读场景大纲展示 |
| 章节草稿（per-scene） | — | `WorkspacePage / WritingArea` 写 |

> 关键事实：**同一个文件被两处编辑**——融合后这层冗余需被消除。

---

## 5. 章节 ↔ 场景体系

- `outline.json.chapters[].scene_plan[]` — 单文件嵌套结构（**没有 per-scene 文件**）
- `scene_id` 约定：`"${chapter_number}-${scene_number}"`（如 `"1-2"`）
- 工作粒度：**场景级**（`currentChapter + currentScene` 一同决定 WritingArea 内容）
- 草稿存储：单独路径（`getSceneDraft / updateSceneDraft`），不在 `outline.json`
- `chapterStatus`：仅在 `assemble_for_chapter_advance` 或 `repair_stuck_chapters` 时翻转
- 卷分组工具：`utils/outline.ts → groupChaptersByVolume`（被 `WorkspacePage / ChapterOutlineEditor` 共用）

---

## 6. 当前重叠与冗余

| 项 | Wizard | Workspace | 冗余成本 |
|---|---|---|---|
| 概念编辑 UI | ConceptStep（290 行） | `ConceptEditor` | 同一文件两套 UI，必须保持一致 |
| 世界观编辑 UI | WorldStep（599 行） | `WorldEditor` | 同上 |
| 角色编辑 UI | CharacterStep（750 行） | `CharacterEditor` | 同上 |
| 全书大纲编辑 UI | OutlineStep（568 行） | `NovelOutlineEditor` | 同上 |
| 章节大纲编辑 UI | ChapterOutlineStep（528 行） | `ChapterOutlineEditor` | 同上 |
| 全局状态 | WizardContext（504 行） | 各 Editor + WorkspacePage | 两套状态 |
| 持久化 | sessionStorage | JSON 文件（直接）/localStorage（UI） | 两套持久化路径 |
| 重生 UI | RegenerateModal + SectionRegenerateButton | 各 Editor 重生按钮 | 重复 |

---

## 7. 关键约束（融合时需保留）

1. **断点续生成**：章节大纲的"暂停/继续"体验不能丢 —— 用户在大批量章节生成中需要可暂停
2. **章节级重生**：每个章节必须能独立重生，融合 UI 需要保留按章节粒度的入口
3. **状态机/重试计数器**：用户能感知"第 N 次重试中"
4. **角色级联删除确认**：删除角色时给出 cascade 计数 modal
5. **行为示例的 per-card 旋转**：重生时要知道是哪个卡片在重生
6. **章节大纲的两 Modal**：批量重生 + 单章重生，需区分入口
7. **跨章节数据依赖清空**：STEP_DATA_KEY_TO_STEP 的逻辑（重做步骤 1 时清空步骤 2-6 的草稿）
8. **场景 ID 双向解析**：`"${ch}-${scene}"` 必须稳定
9. **可写性**：托管模式时右栏应"只读"（当前已实现 `readOnly` 旗标）
10. **左侧章节树**与右栏章节大纲 Tab 必须展示同一棵 outline 树

---

## 8. 关键文件清单

```
frontend/src/
├── pages/
│   ├── WizardDeepLinkPage.tsx           (24 行, /wizard 入口)
│   └── WorkspacePage.tsx                (727 行)
├── components/
│   ├── wizard/                          (~3,910 行)
│   │   ├── InitWizardModal.tsx
│   │   ├── WizardContext.tsx
│   │   ├── WizardSteps.tsx
│   │   ├── ConceptStep.tsx
│   │   ├── WorldStep.tsx
│   │   ├── CharacterStep.tsx
│   │   ├── MapStep.tsx                  (23 行占位)
│   │   ├── OutlineStep.tsx
│   │   ├── ChapterOutlineStep.tsx
│   │   ├── BehaviorExamplesSection.tsx
│   │   └── CharacterRelationsEditor.tsx
│   ├── workspace/                       (~2,782 行)
│   │   ├── WorkspaceTopBar.tsx
│   │   ├── WorkspaceLayout.tsx
│   │   ├── WorkspaceModeSwitcher.tsx
│   │   ├── WritingArea.tsx
│   │   ├── ChapterTreePanel.tsx
│   │   ├── ContextPanel.tsx
│   │   ├── AutopilotMiddlePanel.tsx
│   │   ├── ChapterStreamPanel.tsx
│   │   ├── ManagedStartModal.tsx
│   │   ├── ManagedStartConfirmDialog.tsx
│   │   ├── ManagedDashboard.tsx
│   │   ├── ModeSwitchConfirmModal.tsx
│   │   ├── AddChaptersModal.tsx
│   │   ├── DiagnosisSummary.tsx
│   │   ├── ExportSummary.tsx
│   │   └── editors/                     (5 个 Editor，与右栏 Tab 一一对应)
│   └── home/
│       └── InitPage / HomePage          (调用 InitWizardModal)
```

```
backend/api/creative_canvas.py           (1,675 行, 11 路由)
backend/api/characters.py                (CharacterStep 后端)
backend/api/world.py                     (WorldStep 后端)
backend/api/concept.py                   (ConceptStep 后端 — 新增 source/source_variant_id 字段)
backend/api/creative_divergence.py       (NEW — 4 路由：list/generate/select/prefill)
backend/api/outline.py                   (Outline + ChapterOutline 后端)
backend/api/stage4_writing.py            (Workspace 写作后端)
```

---

## 9. Workspace 融合完成（2026-08-30）

InitWizardModal 的六个步骤已合并到 `/project/:id/workspace?tab=settings` 的 `<WorkspaceWizardPanel>` 内 —— WorkspacePage 顶层 Tab 拆为「项目设定」与「正文手稿」：前者承载 7 步向导（创意发散 / 概念 DNA / 世界观 / 角色设计 / 地图系统 / 全文大纲 / 章节大纲），后者保持原写作流程。顶部「正文手稿」Tab 在所有 7 步完成后自动启用。`<WizardDeepLinkPage>` 已退化为 `/workspace?tab=settings` 的瞬时跳转；旧的 `InitWizardModal` 与 `WizardSteps` 标 DEPRECATED 但保留以便后续清理。新增的 `concept.source` 字段让 ConceptStep 可识别来自创意发散候选的预填，并在用户手动编辑时自动翻转为 `manual`。Workspace 右栏的 5 个编辑器 Tab 已合并移除（概念/世界观/角色/大纲/章节大纲），右栏仅保留诊断/导出。详见 `docs/superpowers/specs/2026-08-30-workspace-wizard-design.md` 与 `docs/superpowers/plans/2026-08-30-workspace-wizard.md`。