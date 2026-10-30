# nebula 产品应用架构分层图

> **定位**：本文只画**分层**与**数据流**。
> 创意流水线的逐阶段细节见 `docs/ARCHITECTURE.md`（Stage 0 → 3b）。
> Wizard 融合后的组件清单见 `docs/wizard-inventory.md`。
>
> 图中所有行数、端点数、阈值均取自当前工作树实测，非估算。
> 标注 ⚠ 的位置对应已验证的架构缺陷，见 §6。

---

## 0. 分层全景

六层。**上层只依赖下层，`api` 是唯一顶层，`models` 是唯一叶子**；中间层互不直接依赖，由 `api` 编排。

```mermaid
flowchart TB
  classDef ui   fill:#E8F0FE,stroke:#4A7EBB,stroke-width:1px,color:#12325C
  classDef api  fill:#E0F2F1,stroke:#3E8E7E,stroke-width:1px,color:#0F3D34
  classDef orch fill:#EFE7F6,stroke:#7B5EA7,stroke-width:1px,color:#33194F
  classDef dom  fill:#E8F5E9,stroke:#4C9A5A,stroke-width:1px,color:#123D1E
  classDef llm  fill:#FEF3E2,stroke:#C98A28,stroke-width:1px,color:#4A3208
  classDef data fill:#F2F2F2,stroke:#808080,stroke-width:1px,color:#2E2E2E

  LYR1["<b>L1 用户交互层</b>　React 18 · Vite · Tailwind　58.9k 行<br/><br/>路由 Pages x18　布局壳 x4　状态 2 Context + 25 hooks<br/>SSE EventSource x2　单一 HTTP 客户端 client.ts"]:::ui

  LYR2["<b>L2 接入层</b>　FastAPI　27 router　170 端点<br/><br/>/api/stage1-6　/creative　/autopilot　/settings<br/>CORS 5174　Deprecation Sunset 头　统一错误信封"]:::api

  LYR3["<b>L3 编排层</b>　Conductor　确定性骨架　4614 行<br/><br/>StageStateMachine 8 阶段　AutopilotLoopService<br/>AsyncAutopilotRunner 优先级队列　SessionManager 写穿<br/>熔断　Checkpoint　手动 / 托管 双分支"]:::orch

  LYR4["<b>L4 领域能力层</b>　三大链 + Agent 层<br/><br/>创意链 CreativeOS 7 引擎 + B3Engine<br/>叙事链 StoryOS 8 注册表 + ReaderOS 7 指标 + Scene/Style Engine<br/>一致链 MemoryOS 5 层 + MapSystem 9 实体 9 断言 + Reviewer 3 守卫"]:::dom

  LYR5["<b>L5 智能模型层</b>　backend/llm　1458 行<br/><br/>ModelRouter: agent_mapping x tier x providers + fallback 链<br/>Provider 适配: Anthropic / DeepSeek / MiniMax / OpenAICompatible / Mock<br/><b>Tier 0 = 零 LLM</b>，一致性判定不烧 token"]:::llm

  LYR6["<b>L6 数据与基础设施层</b><br/><br/>项目目录 JSON（磁盘即真相，tmp+replace 原子写）<br/>Qdrant 本地文件 + BM25 + bge-m3 1024 维<br/>model_tiers.yaml　genres/*.yaml　creative_dimensions.json<br/>llm_usage.jsonl　cascade_log.jsonl"]:::data

  LYR1 -->|"HTTP + SSE"| LYR2
  LYR2 --> LYR3
  LYR3 --> LYR4
  LYR4 -->|"Tier 1-3 调用"| LYR5
  LYR4 -->|"读写"| LYR6
  LYR5 -->|"usage 记录"| LYR6
  LYR6 -.->|"回读组装上下文"| LYR4
```

### 分层职责速查

| 层 | 职责 | 判定标准 | 关键产物 |
|---|---|---|---|
| **L1 交互** | 呈现与输入。无业务规则 | 组件里出现任何"规则判断"就是越界 | 路由、布局、状态、SSE 订阅 |
| **L2 接入** | 协议转换、鉴权、参数校验、错误信封 | 出现编排逻辑就是越界 | HTTP 契约 |
| **L3 编排** | **流程控制**：阶段推进、队列、重试、熔断、暂停恢复 | 出现领域计算就是越界 | 状态机、任务、session.json |
| **L4 领域** | **业务能力**：创作、叙事、一致性 | 出现"下一步做什么"就是越界 | 8 注册表、5 层记忆、9 断言 |
| **L5 模型** | 模型路由与适配 | 出现业务语义就是越界 | RoutingDecision、usage |
| **L6 数据** | 持久化与检索 | — | 项目目录 JSON、索引 |

> **分层的真正判据**：L3 与 L4 的边界是本项目最容易糊的地方。
> 「**下一幕该写什么**」属 L3；「**这一幕写得好不好、违不违规**」属 L4。
> `AsyncAutopilotRunner` 决定写第几幕 → L3；`ReviewerAgent.run_fact_guard` 判断这幕违不违规 → L4。

---

## 1. 横向切面：贯穿全层的三个关注点

这三样东西**不属于任何单层**，是横切关注点。

```mermaid
flowchart LR
  classDef ok  fill:#E8F5E9,stroke:#4C9A5A,color:#123D1E
  classDef cfg fill:#E0F2F1,stroke:#3E8E7E,color:#0F3D34
  classDef tel fill:#F2F2F2,stroke:#808080,color:#2E2E2E

  subgraph PR["Prompt 三层覆盖　读路径"]
    direction TB
    P1["YAML 默认值<br/>backend/prompts/*.yaml　34 个　只读"]:::cfg
    P2["全局覆盖<br/>config/global_prompt_overrides.json"]:::cfg
    P3["项目覆盖<br/>projects/id/prompt_overrides.json"]:::cfg
    P1 --> P2 --> P3
  end

  subgraph TL["模型分级　Tier 0-3"]
    direction TB
    T0["tier_0　零 LLM<br/>Fact Guard · SF_LOG 解析 · ReaderOS"]:::ok
    T1["tier_1　创意核心<br/>概念/世界/角色/地图/大纲/创意引擎"]:::ok
    T2["tier_2　分析 + 场景写作<br/>Scene 写作 · Narrative Guard · Novelty"]:::ok
    T3["tier_3　辅助<br/>L1 重提取 · 摘要 · 风格分类 · 语义预检"]:::ok
  end

  subgraph OB["可观测性"]
    direction TB
    O1["llm_usage.jsonl<br/>每次 LLM 调用一条"]:::tel
    O2["storyos/cascade_log.jsonl<br/>每次级联一条"]:::tel
    O3["LLM Console 最近调用统计"]:::tel
  end

  PR --> L4
  TL --> L4
  OB -.-> L4
```

**关键点**：`tier_0` 的存在是整个架构的支点。
Fact Guard、SF_LOG 解析、ReaderOS 指标、地理断言**全部不调 LLM**——这就是「确定性代码管骨架」落地的位置，也是成本可控的根因。

**三级 Prompt 覆盖**的合并函数是 `load_prompt_effective()`（`backend/services/prompt_override_store.py:334`）：
YAML → 全局 → 项目，后者覆盖前者。S2 的 meta-decompose 正是利用项目级覆盖，把通用的 `firstness_decompose` 提示词换成**为本项目定制的版本**。

---

## 2. 能力链 × 分层落点

三条链不是三个目录，是三条**穿过不同层**的路径。

```mermaid
flowchart TB
  classDef c1 fill:#F3E8FF,stroke:#7B5EA7,color:#33194F
  classDef c2 fill:#E0F2F1,stroke:#3E8E7E,color:#0F3D34
  classDef c3 fill:#FFF4E5,stroke:#C98A28,color:#4A3208
  classDef hub fill:#F2F2F2,stroke:#808080,color:#2E2E2E

  subgraph S1["创意链　Creative Chain"]
    direction TB
    c1a["L3 入口<br/>Wizard Step 1 创意发散"]:::c1
    c1b["L4 CreativeOS 7 引擎<br/>B3Engine 5 维第一性拆解"]:::c1
    c1c["L6 产出 7 个骨架 JSON<br/>concept_and_dna · world<br/>characters · outline"]:::c1
    c1a --> c1b --> c1c
  end

  subgraph S2["叙事链　Narrative Chain"]
    direction TB
    c2a["L3 入口<br/>写一幕 / 归档"]:::c2
    c2b["L4 StoryOS 8 注册表 + 级联<br/>ReaderOS 7 指标<br/>Scene Engine · Style Engine"]:::c2
    c2c["L6 产出<br/>storyos/ 8 注册表<br/>progress.reader_os 快照"]:::c2
    c2a --> c2b --> c2c
  end

  subgraph S3["一致链　Consistency Chain"]
    direction TB
    c3a["L3 入口<br/>每幕组装上下文"]:::c3
    c3b["L4 MemoryOS 5 层<br/>MapSystem 9 实体 9 断言<br/>ReviewerAgent 3 守卫"]:::c3
    c3c["L6 产出<br/>memory/l2 l3 l4 · map.json<br/>map_snapshots"]:::c3
    c3a --> c3b --> c3c
  end

  HUB["L4 咽喉点　MemoryCoordinator.assemble_for_scene()<br/>装配顺序 L0 → L1 → L4 → L2 → L3<br/>一致链在此把 L0-L4 与地图卡注入叙事链"]:::hub

  S1 -->|"产出骨架"| HUB
  S2 -->|"读注册表算指标"| HUB
  S3 -->|"注入上下文"| HUB
```

| | 创意链 | 叙事链 | 一致链 |
|---|---|---|---|
| **入口** | Wizard Step 1 创意发散 | 托管/手动写一幕 | 每一次场景组装 |
| **核心引擎** | CreativeOS 7 引擎 + B3Engine | StoryOS Agent + ReaderOS | MemoryCoordinator + Reviewer |
| **LLM 占比** | 高（tier_1 为主） | 中（tier_2 一段） | **极低**（tier_0 为主） |
| **产出** | 7 个 JSON 骨架 | 注册表状态 + 读者指标 | 上下文块 + 守卫结论 |
| **是否阻塞** | 否（可重生成） | 否（只建议） | **是**（Blocker 熔断） |

**一致性链是闭环的**：一致链把记忆与地图喂回叙事链，叙事链的产出再落回数据层被一致链读取。
三条链在 `MemoryCoordinator.assemble_for_scene()` 汇合——这是每幕写作的咽喉点。

---

## 3. 编排层内部：托管模式写一幕

### 3.1 编排层结构

```mermaid
flowchart TB
  classDef orch fill:#EFE7F6,stroke:#7B5EA7,color:#33194F
  classDef api  fill:#E0F2F1,stroke:#3E8E7E,color:#0F3D34
  classDef dom  fill:#E8F5E9,stroke:#4C9A5A,color:#123D1E
  classDef llm  fill:#FEF3E2,stroke:#C98A28,color:#4A3208

  subgraph L3["L3 编排层 Conductor"]
    direction TB
    SM["StageStateMachine<br/>8 阶段 + 文件级前置条件<br/>唯一回退 regress_to_stage3"]:::orch
    LOOP["AutopilotLoopService<br/>每项目一个 asyncio task<br/>启动时 recover_running_sessions"]:::orch
    RUN["AsyncAutopilotRunner.run()<br/>heartbeat → 取最小 priority → _step_one<br/>scene(ch,sc) = ch*1000+sc"]:::orch
    SESS["AutopilotSessionManager<br/>写穿 session.json + SSE 发布"]:::orch
    EXEC["AsyncStage4Executor<br/>execute_stream → 写幕 → upsert progress<br/>本章完成则入队 archive-ch"]:::orch
    BRK["熔断双轨<br/>手动: 逐幕 3 次重试后 force-pass<br/>托管: 会话级 force_pass >= 3 自动暂停"]:::orch
    CKPT["CheckpointManager<br/>.nebula_checkpoint.json<br/>仅按章写入；recover() 无生产调用方"]:::orch

    SM -.->|"阶段准入"| LOOP
    LOOP --> RUN
    RUN --> EXEC
    RUN --> SESS
    BRK -.->|"熔断触发"| EXEC
    EXEC --> CKPT
  end

  L2X["L2 接入层"]:::api
  L4X["L4 领域层<br/>MemoryCoordinator · Writer · Reviewer · StoryOS"]:::dom
  L5X["L5 模型层"]:::llm
  L6X["L6 数据层"]:::orch

  L2X --> L3
  L3 --> L4X
  L4X --> L5X
  L3 --> L6X
```

### 3.2 一幕的端到端时序

```mermaid
sequenceDiagram
  autonumber
  participant B as 浏览器
  participant A as L2 FastAPI
  participant L as L3 LoopService
  participant R as L3 Runner
  participant X as L3 Executor
  participant W as L4 Writer
  participant G as L4 Guard
  participant F as L6 文件系统

  B->>A: POST /autopilot/session/start
  A->>L: ensure(project_id)
  L->>F: seed_queue 读 outline + progress
  L->>L: create_task(_runner_loop_task)
  loop 每 cadence 一轮
    R->>F: heartbeat 写 session.json
    R->>X: execute_stream(queue_item)
    X->>W: write_scene_stream
    W-->>B: scene_chunk 经 SSEBroadcaster
    X->>G: run_fact_guard  tier_0 零 LLM
    G-->>X: 9 基础检查 + 9 地理断言
    X->>F: 写 chapters/chNN_scene_MMM_draft.md
    X->>F: upsert progress.json
  end
  alt 本章全部幕终态
    X->>F: 入队 archive-ch
    X->>F: SummaryArchiver → L2 → ReaderOS → checkpoint
  else 幕写失败
    R->>R: 重试 3 次 退避 30s / 60s
    R->>B: pause(scene_write_failed)
  end
```

### 3.3 手动模式与托管模式的差异

**不是同一套代码的两条路，是两套实现。** 差异集中在 L3 编排层与 L4 守卫层。

| | 手动模式 | 托管模式 |
|---|---|---|
| 入口 | `_write_scene_chapter()` 同步 | `_write_scene_chapter_stream()` 流式 |
| 事实守卫 | 3 次重试 + 熔断 force-pass | **单次**，失败直接 `force_passed` |
| 断点写入 | 逐幕 | **仅按章** |
| `*_meta.json` | 写 | **不写** |
| 章节复核 | 生成 | **不生成** |
| 熔断口径 | 逐幕 `CircuitBreaker` | 会话级 `force_pass_count >= 3` |
| SSE | 无 | 有（scene_chunk 逐 50 字 / 80ms 推送） |

```mermaid
flowchart LR
  classDef man fill:#E0F2F1,stroke:#3E8E7E,color:#0F3D34
  classDef aut fill:#EFE7F6,stroke:#7B5EA7,color:#33194F
  START["L2 接入<br/>POST /stage4/write-scene<br/>POST /autopilot/session/start"]:::man
  START --> MAN["手动分支"]:::man
  START --> AUT["托管分支"]:::aut
  MAN --> M1["同步生成"]:::man
  M1 --> M2["守卫失败 → 3 次重写"]:::man
  M2 --> M3["写 draft.md + meta.json"]:::man
  M3 --> M4["写逐幕 checkpoint"]:::man
  M4 --> M5["章完成 → 章节复核"]:::man
  AUT --> A1["流式生成 逐块推送 SSE"]:::aut
  A1 --> A2["守卫单次"]:::aut
  A2 --> A3["写 draft.md 不写 meta.json"]:::aut
  A3 --> A4["按章 checkpoint"]:::aut
  A4 --> A5["章完成 → 无复核"]:::aut
```

> **架构含义**：托管模式放弃了「重写」与「复核」，换来吞吐。
> 这不是疏漏，是取舍——但代价是托管模式下 `ChapterReviewBuilder` 的输入（`*_meta.json`）根本不存在，
> 所以 Fact Guard 通过率在托管模式下**永远读不到数据**。

---

## 4. 数据与存储分层

磁盘即真相。**没有数据库**。

```mermaid
flowchart TB
  classDef d1 fill:#F2F2F2,stroke:#808080,color:#2E2E2E
  classDef d2 fill:#E0F2F1,stroke:#3E8E7E,color:#0F3D34
  classDef d3 fill:#E8F5E9,stroke:#4C9A5A,color:#123D1E

  subgraph ROOT["projects/project_id/"]
    direction TB
    P1["创作骨架<br/>project.json · concept_and_dna.json<br/>world.json · characters.json<br/>map.json · novel_outline.json · outline.json"]:::d1
    P2["运行时<br/>progress.json · autopilot/session.json<br/>autopilot/chunks/*.jsonl<br/>.nebula_checkpoint.json"]:::d2
    P3["领域状态<br/>storyos/ 8 个注册表 + cascade_log.jsonl<br/>memory/l2 · memory/l3 · memory/l4"]:::d3
    P4["正文与快照<br/>chapters/chNN_scene_MMM_draft.md<br/>map_snapshots/chapter_NNN.json<br/>creative_os/ · chapter_reviews/"]:::d1
  end

  IDX["检索索引<br/>memory/l3/qdrant/ 本地文件<br/>bm25_index.pkl<br/>chunk_manifest.json"]:::d2
  CACHE["模型权重缓存<br/>~/.cache/nebula/models<br/>bge-m3 约 3GB<br/>legacy: ~/.cache/storyforge/models"]:::d1
  CONF["全局配置<br/>config/model_tiers.yaml<br/>config/genres/*.yaml<br/>config/creative_dimensions.json<br/>config/trope_catalog.yaml"]:::d2
  LOGS["遥测<br/>llm_usage.jsonl<br/>backend/.env  API Key"]:::d1

  ROOT --> IDX
  ROOT --> CONF
  ROOT --> LOGS
  IDX --> CACHE
```

**原子写是全仓库硬约定**：所有 JSON 落地一律 `tmp 文件 → Path.replace()`。
崩溃不会留下半截文件。

**内存层级与磁盘层级一一对应**：

| 记忆层 | 磁盘位置 | 生命周期 | 装配时机 |
|---|---|---|---|
| L0 Runtime | 内存 | 单次请求 | 每幕 |
| L1 Hot | 内存（扫 `chapters/*.md` 重建） | 单次请求 | 每幕 |
| L4 Narrative | `memory/l4/narrative_patterns.json` | 持久缓存 | 每幕（**每次都重新同步 8 个注册表**） |
| L2 Warm | `memory/l2/` | 持久 | 每幕 |
| L3 Cold | `memory/l3/` | 持久 | 每幕（可缺席） |

装配顺序 **L0 → L1 → L4 → L2 → L3**，但注入提示词的顺序是
`实时上下文 → 近期章节摘要 → 全书叙事摘要 → 叙事模式分析(L4) → 相关历史文本(L3) → 创作阶段 → 地图卡`。
**L4 在提示词里排在 L3 前面**，与装配顺序不同。

---

## 5. 前端分层

```mermaid
flowchart TB
  classDef f1 fill:#E8F0FE,stroke:#4A7EBB,color:#12325C
  classDef f2 fill:#E0F2F1,stroke:#3E8E7E,color:#0F3D34
  classDef f3 fill:#F2F2F2,stroke:#808080,color:#2E2E2E

  subgraph FE["前端 React 18 + Vite 5 + TS 5.3"]
    direction TB
    FR["路由 App.tsx　单一 Routes 块<br/>懒加载除首页/控制台/Prompt Plaza 外全部页面"]:::f1
    FS["布局壳<br/>HomeLayout · MainLayout · WorkspaceLayout<br/>WorkspaceTopBar · StageErrorBoundary"]:::f1
    FW["工作区双 Tab<br/>项目设定 = Wizard 7 步<br/>正文手稿 = 驾驶舱（正文 Tab 锁至向导全完成）"]:::f1
    FC["状态<br/>ToastContext · WizardContext<br/>25 个 hooks + URL + localStorage<br/>无 Redux / Zustand / React Query"]:::f3
    FSSE["SSE x2<br/>session/events 10 事件<br/>chapter-stream 6 事件<br/>原生 EventSource + 指数退避"]:::f2
    FHTTP["HTTP client.ts 2417 行<br/>~180 接口类型 + ~250 方法<br/>TIMEOUT 900s · ApiError 信封"]:::f2
    FDS["设计系统 ds/ 15 个原语 + 3 个 token 模块<br/>Barrel 统一导出"]:::f3
  end
  FR --> FS --> FW
  FW --> FC
  FW --> FSSE
  FW --> FHTTP
```

**状态优先级是硬约定**：`useWorkspaceMode` = **URL › localStorage › 默认**。
URL 优先保证可分享链接能复现视图，localStorage 兜底个人偏好。

**没有全局 store** 是刻意的：项目是单用户本地应用，跨页面共享的状态很少，
引入 Redux 反而要处理持久化与失效。代价是跨页面的数据一致性靠各自 hook 重新拉取。

---

## 6. 架构风险落点

以下 9 条**已逐条验证**（非推测），标在图上的位置即修复位置。

```mermaid
flowchart LR
  classDef bad fill:#FDECEC,stroke:#C0392B,stroke-width:2px,color:#7B1E14
  classDef mid fill:#FFF4E5,stroke:#C98A28,stroke-width:1px,color:#4A3208

  subgraph G1["L1 交互层"]
    R9["R9 前端改名无迁移兜底<br/>9 个 storage key 全部改为 nebula.*<br/>无 legacy fallback<br/><b>影响</b>　老用户偏好丢失（项目数据不丢）"]:::mid
  end

  subgraph G2["L2 接入层 · L3 编排层"]
    R1["R1 语义预检静默空转<br/>_run_semantic_precheck 引用未定义的 project_id<br/>NameError 被 except Exception 吞掉<br/><b>影响</b>　三个调用点全部无效"]:::bad
    R2["R2 SSE 断线重连读错文件<br/>current_task.scene_id 恒为 None<br/>解析成 scene=0<br/><b>影响</b>　实时流正常，重放永远为空"]:::bad
    R5["R5 断点恢复是死代码<br/>recover() 无生产调用方、无 HTTP 端点<br/>intervene(rollback_checkpoint) 仅为 pause 桩<br/><b>影响</b>　断点能力未上线"]:::bad
    R4["R4 会话级熔断只触发一次<br/>force_pass_count 永不清零<br/><b>影响</b>　第二次起不再自动暂停"]:::mid
  end

  subgraph G3["L4 领域层"]
    R3["R3 创意导演覆盖失效<br/>CreativeDirector 签名不收 override_store<br/>TypeError 被吞<br/><b>影响</b>　恒返回空串"]:::mid
    R8["R8 StoryOS 级联非跨文件事务<br/>主资产 status 写在事务外<br/><b>影响</b>　级联部分失败仍照样写入"]:::bad
  end

  subgraph G4["L5 模型层"]
    R6["R6 三套 provider 派发表不一致<br/>ModelRouter / 控制台探测 / 遗留路径<br/><b>影响</b>　探测结果与实际调用可能不符"]:::mid
    R7["R7 流式写作绕过 tier 路由<br/>write_scene_stream 用 settings 而非 model_tiers.yaml<br/><b>影响</b>　pin 的 deepseek-v4-pro 不生效"]:::mid
  end
```

### 风险明细

| # | 位置 | 根因 | 影响 | 状态 |
|---|---|---|---|---|
| **R1** | L2/L3 边界 `backend/api/stage4_writing.py:527` | `_run_semantic_precheck` 引用未定义的 `project_id`（既非参数也非模块全局），`NameError` 被 `except Exception` 吞掉 | 语义预检在**手动 / 托管 / fact-guard 三个调用点全部失效**，恒返回 `precheck_passed=True` | 已验证 |
| **R2** | L1/L3 `autopilot_runner_async.py:687` → `api/autopilot.py:452` | `current_task.scene_id` 硬编码 `None`，解析成 `scene=0`，SceneChunkStore 指向 `chNN_scene_000.jsonl` | 实时 SSE 正常，但 **`Last-Event-ID` 重连重放永远返回空** | 已验证 |
| **R3** | L4 `agents/creative_director.py:16` vs `api/creative_diverge.py:938/1051/1556` | 构造函数签名不收 `override_store` / `global_override_store`，`TypeError` 被吞 | `suggest_direction` / `recommend_mutation` **恒返回空串** | 已验证 |
| **R4** | L3 `conductor/autopilot_session.py:332` | `crossed = (count < 3 and new_count >= 3)`，而 `force_pass_count` 永不清零 | 会话级熔断**只会触发一次**；日志文案与代码相反 | 已验证 |
| **R5** | L3/L6 `conductor/checkpoint.py:103/183` | `recover()` 与 `restore_registries_from_snapshot()` 仅被测试调用，无 HTTP 端点 | 断点恢复能力**未上线**；真实恢复靠 `progress.json` + `outline.json` + `session.json` | 已验证 |
| **R6** | L5 `llm/model_router.py:547` / `services/llm_config.py:464` / `llm/__init__.py:47` | 三张 provider→class 派发表互不一致 | AI 控制台探测结果**可能与实际调用不符** | 已验证 |
| **R7** | L3→L5 `agents/base_agent.py:326` | 流式路径显式绕过 ModelRouter，用 `settings.llm_provider/llm_model` | `model_tiers.yaml` 里 pin 的 `deepseek-v4-pro` **对托管写作不生效** | 已验证 |
| **R8** | L4/L6 `story_os/registries.py:173` | 主资产 status 写入在 `propagate()` 事务之外，且级联部分失败时仍写入 | 跨注册表**可能部分应用**，无回滚 | 已验证 |
| **R9** | L1 前端 9 处 storage key | 改名时**未加 legacy fallback**（后端 checkpoint / Qdrant / bge-m3 缓存都加了） | 老用户的主题 / 侧栏宽度 / 折叠状态 / 向导进度**丢失** | 已验证 |

### 附：文档漂移（非缺陷，但会误导后续开发）

| 文档说法 | 代码实际 |
|---|---|
| 前端端口 5173 | **5174**（`vite.config.ts`，后端 CORS 也只放行 5174） |
| Tier 1 = Scene Writing | `writer.scene_writing` 在 **tier_2** |
| What-If depth=3 breadth=4 | `BRANCHES_PER_NODE = 3` |
| L1 = 最近 5 章 | `MAX_SCENES = 6` **幕** |
| `growth_curve/auto_generator.py` | 该文件不存在，已被 `aligner.py` 取代 |
| L2 约 8K token | `get_context_string(max_tokens=8000)` **参数收了没用** |

### 附：结构性不一致

- **两套测试目录**：根 `tests/`（~140 文件）与 `backend/tests/`（46 文件），风格不同。
- **三套创意发散并存**：`creative_divergence.py`（已废弃，`_generate_variants` 是确定性 stub 不调 LLM）/ `creative_diverge.py`（v1.x，3357 行）/ `v2_canvas.py`（v2，836 行）。
- **两套突变词汇**：`MutationOp`（inversion/fusion/escalation/subversion）vs canvas 6 ops（twist/break/fuse/invert/escalate/dramaturgy）。
- **`REGISTRY_FILES` 字面量复制三份**（`registries.py` / `registry_transaction.py` / `l4_narrative.py`）。
- **Stage 前置条件文件号错位一位**：STAGE2 检查 `concept_and_dna.json`，STAGE3 检查 `characters.json` + `world.json`。
- **STAGE5 / STAGE6 前置条件空集合恒真**（`all()` 作用于空列表）。

---

## 7. 一张图收尾：为什么这套架构能撑住长篇

```mermaid
flowchart LR
  classDef good fill:#E8F5E9,stroke:#4C9A5A,color:#123D1E
  classDef key fill:#FEF3E2,stroke:#C98A28,color:#4A3208

  A["L3 确定性骨架<br/>阶段推进 · 重试 · 熔断 · 断点"]:::good
  B["L4 领域判定<br/>守卫 · 断言 · 指标"]:::good
  C["L5 LLM 只填血肉<br/>按 tier 分级 · fallback 链"]:::key
  D["L6 磁盘即真相<br/>原子写 · 可回滚 · 可审计"]:::good

  A --> B --> C
  B -.->|"读注册表与记忆"| D
  C -.->|"只产出文本"| D
  D -.->|"下一幕的上下文"| A
```

**核心：LLM 出现在链尾且被夹在中间。**
它既不决定「写什么」（L3），也不决定「对不对」（L4），只负责把已经定好的骨架变成文字。
所以：

- LLM 挂了 → tier_2/3 降级为空串，**流程不崩**（`base_agent.py:411` 静默降级）
- 写崩了 → 断点 + 队列 + 熔断，**可恢复**
- 判定永远一致 → 因为判定根本不经 LLM

代价是**托管模式为了吞吐放弃了重写与复核**（§3.3），这是当前架构最锋利的一处取舍。

---

*所有图块已通过 `node scripts/check_mermaid.mjs docs/ARCHITECTURE-LAYERS.md` 校验（用 frontend 已安装的 mermaid v12 离线解析）。*
*布局经验（写新图时省得踩坑）：mermaid 在子图**内部节点对外连线**时会忽略子图内的 `direction`；*
*改成**子图 ID 对外连线**即可保留方向；多个并列子图指向同一节点时会自动排成一行。*
