# Plot Canvas 剧情画布模块重构 PRD

**版本**：v2.0
**模块**：Plot Canvas / 剧情画布
**状态**：产品重构方案
**上游**：用户 Idea
**下游**：Concept DNA → 世界观 → 角色设计 → 地图系统 → 大纲

---

# Part I — 产品定位与边界

## 1. 产品定位

Creative Canvas 是整个 nebula 创作链路的**创意发散与收敛引擎**。

它不直接写故事，也不完成世界观、角色和大纲。它只做一件事：

> **把用户一个模糊的 Idea，通过连续的创意操作，逐步推演成一个具有独特性、冲突性和故事潜力的「剧情创意路径」。**

用户不是在一棵复杂的树里寻找答案，而是在一个**有限深度的创意决策旅程**中不断做选择。

## 2. 非目标

Creative Canvas **不负责**：

- 完整故事
- 完整世界观
- 完整人物设定
- 地图
- 章节大纲
- 正文写作

这些属于下游模块。

Creative Canvas 输出的是：

> **一个经过创意推演的故事核心 premise + 创意演进轨迹 + 核心冲突 + 故事 DNA 原材料。**

## 3. 用户体验目标

整个模块应该让用户感觉：

> **「我正在和 AI 一起玩一个创意推演游戏。」**

而不是：

> **「我正在操作一个复杂的故事管理工具。」**

---

# Part II — 核心创作模型

## 4. 核心模型

新模型不再以「无限展开的 What-if Tree」为核心，改为：

```text
                     ┌─ Option A
                     │
Idea → 创意操作① ────┼─ Option B ───→ 创意操作② ───┬─ Option A
                     │                             ├─ Option B
                     └─ Option C                   └─ Option C
```

每一个 Step：

1. 系统根据当前创意状态选择一种创意操作
2. 生成 **3 个创意候选**
3. 用户选择其中一个
4. 被选择的创意成为下一步的输入
5. 系统继续进行下一次创意演化

上限：

> **5 步**

下限（v2.1 实现）：

> **1 步（用户随时可以早收束，详见 §36）**

一次创作最多产生：

```text
1 个 Idea
↓
最多 5 次创意操作
↓
每次 3 个候选
↓
最终 1 条创意路径
```

核心不是「生成很多东西」，而是：

> **让用户参与决定一个创意是如何一步一步变成另一个创意的。**

### 4.1 为什么是「5 步」而非「树」

旧 What-if Tree 模型的失败模式：用户面对 4^N 个节点展开（depth=3, breadth=4），不知道下一步该做什么，最终得到一棵「树」却没有清晰的创意演进逻辑。

5 步的设计意图是把创意推演收敛到一个**用户能在 1 屏内理解**的范围：

```text
Premise → Twist → Conflict → World Implication → Story Premise
```

其中 Step 5「收束」承担「把前面 4 步演化结果凝缩成可消费 premise」的职责。如果用户在更早的步骤已经形成清晰 premise，可以主动早收束（v2.1 启用，详见 §36）。

---

## 5. Step 状态机

v1.x 的 Step 状态只有「可选 / 已选」两态，导致已发现 bug：用户 A/B/C 重编辑会清空下游 selected_path，但 UI 仍显示「5. 提交」可点击 → POST /commit 时空路径 422（详见 memory `project_divergence_a_to_e_empty_path.md`）。

v2 引入完整的 5 态状态机 + 显式 transition rule。

### 5.1 状态定义

| 状态 | 含义 | UI 视觉 |
|---|---|---|
| `LOCKED` | 前置步骤未完成，无法进入 | 灰色未来占位 |
| `AVAILABLE` | 前置完成，可被激活 | 描边卡片，未展开 |
| `ACTIVE` | 当前用户正在交互的步骤 | 高亮强调，3 选项卡片展开 |
| `COMPLETED` | 用户已选择一项，固定不变 | ✓ 标记，可点击查看历史 |
| `STALE` | 上游被回溯/重生导致本步骤产物失效 | 警告色标记，禁用提交 |

`SKIPPED`（v2.1 引入，对应早收束场景）在 Part XI §36 描述，v2.0 不启用。

### 5.2 状态转移规则

```text
init           → LOCKED                 (Step 1 初始状态)
Step i 已完成  → Step i+1 AVAILABLE     (用户选择后立刻触发)
Step i AVAILABLE → Step i ACTIVE       (用户点击「继续」)
Step i ACTIVE  → Step i COMPLETED      (用户选了 A/B/C 之一)
Step i ACTIVE  → Step i COMPLETED + Step i+1..5 STALE  (用户从 Step i 重生)

Step 1 完成后 → Step 1 COMPLETED + Step 2 AVAILABLE
Step 5 COMPLETED → 进入 final_concept 收束（§4.1 描述的 Dramaturgy）
```

### 5.3 关键不变量

- **`COMPLETED → ACTIVE` 不可直接发生**。用户想修改已完成的步骤必须走「回溯」操作（v2.1 启用，详见 §37），而不是简单「重新点击」。
- **`STALE` 步骤不允许 commit**。UI 必须禁用「提交」按钮直到所有非 LOCKED 步骤都达到 COMPLETED。
- **`Step 1 LOCKED → AVAILABLE` 的触发条件是 Idea 已 init 且 canvas_state 处于 active 状态**。

### 5.4 错误防护

v1.x 已发现的 "StepIndicator shows '5. 提交' clickable with empty selectedPath" bug 在 v2 通过以下方式根除：

1. Commit 按钮的启用条件 = `Step N (N≤5) COMPLETED + Step N+1..5 ∈ {LOCKED, COMPLETED} + 无 STALE`
2. 后端 commit 端点校验 `selected_path.length >= 2` + 所有 step 都在 `COMPLETED` 状态，否则返 422 `INVALID_PATH`

---

## 6. 创意操作体系

v2 不让用户直接面对大量 engine。内部继续复用现有 Mutation / Contradiction / Fusion 等能力，但产品层统一抽象为 6 类操作：

| 编号 | 用户看到 | 内部能力 | 一句话定义 |
|---|---|---|---|
| O1 | 扭曲 Twist | Mutation | 改变一个关键条件 |
| O2 | 打破 Break | Subversion / Contradiction | 破坏一个既定规则 |
| O3 | 融合 Fuse | GenreFusion / Trope Fusion | 把两个看似无关的元素组合 |
| O4 | 反转 Invert | Inversion | 交换因果、角色或立场 |
| O5 | 升级 Escalate | Escalation | 将冲突推向更大尺度 |
| O6 | 收束 Dramaturgy | Planner / Concept Generation | 把前序演化凝缩成可消费的 premise |

`极端化 Radicalize`（v2.1 引入）作为第 7 类操作，详见 §24 的引擎映射说明。

---

## 7. 三选项的差异轴

PRD v1.0 草案把三个选项固定映射为「A 稳健 / B 意外 / C 激进」。这种映射在「扭曲」「打破」上是合理的，但在「融合」「反转」「收束」上会产生扭曲输出（融合的三个方向应该是融合 X / Y / Z，而不是保守-激进）。

v2 采用**按操作类型自适应的差异轴**：

| 操作 | A（基础） | B（变体） | C（极端） |
|---|---|---|---|
| 扭曲 | 改变单一关键条件 | 改变条件之间的因果 | 改变整个设定基础 |
| 打破 | 规则在边界条件下失效 | 规则被反噬 | 规则不存在 |
| 融合 | 表面元素融合（道具 / 场景） | 类型规则融合（探案机制） | 世界观融合（物理规则） |
| 反转 | 角色立场反转 | 因果反转 | 主题反转 |
| 升级 | 个人级别升级 | 社会级别升级 | 文明/宇宙级别升级 |
| 收束 | 简洁 premise | 复杂 premise | 主题化 premise |

**硬约束**：三选项必须改变故事方向，禁止只是措辞不同：

```text
禁止：
A：未来成为敌人
B：未来成为强大的敌人
C：未来成为可怕的敌人

应该是：
A：未来是敌人
B：未来是受害者
C：未来才是真正的操控者
```

如果 AI 生成的三个选项属于同一种方向，触发自动重生（v2.1 启用 Regenerate 功能，v2.0 阶段如果发生则提示用户回溯或接受当前候选，详见 §39）。

---

## 8. AI 操作选择机制

### 8.1 设计原则

**不应该让用户每一步自己选择「扭曲 / 打破 / 融合」**——否则用户需要承担 AI 的思考工作。

应该：

```text
当前创意状态
↓
backend 计算 candidate_operation_hint（确定性规则）
↓
LLM 单次调用同时返回：
  - operation（通常与 hint 一致；LLM 可微调）
  - reasoning
  - options[3]
↓
用户选择其中一个选项
```

理想实现是 **单次 LLM 调用同时返回 (operation, reasoning, options[3])**，减少 50% 延迟与 token 成本。

### 8.2 何时让用户主动切换操作（v2.1）

v2.0 不实现用户主动 override AI 推荐。MVP 阶段用户只能从 AI 给出的 3 个选项中选一个；如果都不满意只能 commit 当前最佳或重置整个 session。

v2.1 启用 override 功能（详见 §38），UI 在 AI 推荐之外提供 `[ 扭曲 ] [ 打破 ✓ ] [ 融合 ] [ 反转 ] [ 升级 ]` 等切换按钮，并加入「你正在主导创意方向」的软提示。

### 8.3 Prompt 设计草稿（用于 next-step 端点）

```yaml
system: |
  你是创意推演引擎。根据当前创意状态，决定下一步最有价值的创意操作，
  并生成 3 个候选方向。

  你的输出必须是合法 JSON：
  {
    "operation": "twist|break|fuse|invert|escalate|dramaturgy",
    "operation_reason": "为什么选这个操作（30 字内）",
    "options": [
      { "id": "opt_a", "title": "...", "premise": "...", "logic": "..." },
      { "id": "opt_b", "title": "...", "premise": "...", "logic": "..." },
      { "id": "opt_c", "title": "...", "premise": "...", "logic": "..." }
    ]
  }

  三个选项必须：
  1. 保持当前创意的核心
  2. 产生明显不同的剧情可能
  3. 有明确的因果变化
  4. 能够继续向下游发展
  5. 三者之间存在显著差异（按操作类型自适应，见 §7 表）

user: |
  current_concept: {premise, core_conflict, characters, world_rules, tropes, themes}
  selected_path: [...前序 steps 摘要，每条 ≤ 30 字...]
  current_step: N
  max_steps: 5
  candidate_operation_hint: {auto | twist | break | fuse | invert | escalate}
```

### 8.4 backend 计算 candidate_operation_hint 的规则

操作选择判定由 backend 在调用 LLM 之前确定，避免 LLM 完全凭语感选择 op：

```python
def compute_op_hint(concept: dict, path: list, step: int) -> str:
    """确定性规则，不依赖 LLM。"""
    if step == 5 or step == len(path) + 1 >= 5:
        return "dramaturgy"
    if concept.get("novelty", 0) < 0.5:
        return "twist"
    if "冲突" not in (concept.get("core_conflict") or ""):
        return "break"
    if len(concept.get("genres", [])) < 2:
        return "fuse"
    if len(path) >= 3 and not path[-1].get("has_inversion"):
        return "invert"
    if concept.get("conflict_scale") in ("personal", None):
        return "escalate"
    return "twist"  # default fallback
```

LLM 看到 `candidate_operation_hint` 后可微调（例如把 `twist` 改为 `break`），但需要返回的 `operation` 必须与 `reasoning` 自洽。

### 8.5 失败处理（v2.0 MVP 范围）

| 失败模式 | v2.0 处理 | v2.1 处理 |
|---|---|---|
| LLM 返回 JSON 解析失败 | 自动重试 1 次，附 `Please return valid JSON only` | 同 |
| 三个选项相似度过高（premise 余弦相似度 > 0.85） | 在响应中标 `quality_warning: "options_too_similar"`，UI 提示用户回溯或接受 | 触发自动 Regenerate |
| 连续多次相似 | 不适用（v2.0 无 Regenerate） | UI 显示「AI 暂时给不出新方向，是否回溯一步？」|
| 用户主动 Regenerate | **不实现**（v2.0） | v2.1 启用 Regenerate 端点（详见 §39）|

---

## 9. 早收束（v2.1 启用）

v2.0 MVP 不实现早收束——用户必须走满 5 步。

v2.1 启用早收束功能的设计预览：

- **触发方式**：(a) AI 自动判断（基于 novelty/conflict/motivation 充分性）；(b) 用户主动点「提前收束」按钮。
- **状态机影响**：剩余步骤标记为 `SKIPPED`（v2.1 在 §5.1 表中新增）。未走到的步骤记录在 `canvas_meta.skipped_steps: [4, 5]`。
- **关键约束**：「5 是上限，不是必须。早收束不是'放弃'，而是'加速'。」UI 文案必须强调这一点。

详细设计推迟到 v2.1，详见 §36。

---

# Part III — 用户旅程与交互

## 10. 用户旅程概览

### 10.1 Happy Path

```text
1. 用户输入 Idea（一句自然语言 + 类型）
2. 系统解析 Idea 并 init session
3. Step 1 AI 推荐 + 用户选择 → COMPLETED
4. Step 2-5 重复（v2.0 必须走满 5 步；v2.1 支持早收束）
5. 收束：final_concept 生成
6. 用户点 Commit → 写入 concept_and_dna.json → 跳转 ConceptStep
```

### 10.2 异常路径

| 场景 | 用户体验 |
|---|---|
| LLM 调用失败 | 当前 Step 显示错误，可重试 |
| 用户中途关闭浏览器 | canvas_state 自动保存，下次进入续作 |
| 用户在 Step 3 想回溯到 Step 2 | v2.0 不支持，需重置；v2.1 启用回溯（§37）|
| 三选项都不满意 | v2.0 只能 commit 当前最佳或重置；v2.1 启用 Regenerate（§39）|
| Commit 校验失败 | 显示具体失败原因（如某个 Step 未完成）|
| 网络中断 / SSE 断开 | 重连后从 `current_step` 恢复，不丢已完成状态 |

### 10.3 用户离开与恢复

canvas_state.json 是单一真相源。用户在任意 Step 中途离开，下次进入读 `_etag` + `current_step` 直接续作，不需要重做已完成步骤。

---

## 11. 入口与空状态

### 11.1 路由

`/project/:projectId/stage0/canvas`（与 Stage 0 其他 tab 同级）。

### 11.2 空状态

```text
┌─────────────────────────────────────────┐
│  创造一个故事，不需要从完整故事开始。    │
│                                         │
│  只需要告诉我：                          │
│  「你脑子里现在有什么？」                 │
│                                         │
│  ┌───────────────────────────────────┐  │
│  │ 一个关于……                        │  │
│  │                                   │  │
│  └───────────────────────────────────┘  │
│                                         │
│  类型：[ 仙侠 ▼ ]                       │
│                                         │
│              [开始创意推演 →]             │
└─────────────────────────────────────────┘
```

不要求用户填写大量字段。Idea 是唯一核心输入，类型后台辅助。

### 11.3 与现有 init 入口的兼容

`/init` 端点当前接收 `RawIntent { prompt, genre_primary, genre_secondary, ... }`。v2 init 端点保持兼容，但新 session 写入 `root_idea.prompt`（从 `RawIntent.prompt` 映射，详见 §22）。

如果用户在已有 `concept_and_dna.json` 的项目上重新进入 canvas，先弹出「是否覆盖」确认对话框（详见 §18 重置）。

---

## 12. Canvas 布局（横向路径画布）

整个页面布局：

```text
┌──────────────────────────────────────────────────────────┐
│ Creative Canvas                          创意深度 3 / 5   │
│ 把一个 Idea 逐步推演成独特的剧情创意                       │
├──────────────────────────────────────────────────────────┤
│                                                          │
│  ← 横向创意路径                                          │
│                                                          │
│  IDEA       STEP 1      STEP 2      STEP 3                │
│  ┌───┐      ┌───┐       ┌───┐       ┌───┐                │
│  │   │──────│ ✓ │───────│ ✓ │───────│ ● │                │
│  └───┘      └───┘       └───┘       └───┘                │
│                │           │           │                  │
│                ├─ A        ├─ A        ├─ A                │
│                │           │           │                  │
│                ├─ B ✓      ├─ B ✓      ├─ B ✓              │
│                │           │           │                  │
│                └─ C        └─ C        └─ C                │
│                                                          │
├──────────────────────────────────────────────────────────┤
│                                                          │
│  STEP 3                                                  │
│  融合                                                    │
│                                                          │
│  将当前创意与一个新的元素结合，寻找意外结果。              │
│                                                          │
│  ┌────────────┐ ┌────────────┐ ┌────────────┐             │
│  │ A          │ │ B ✓        │ │ C          │             │
│  │            │ │            │ │            │             │
│  │ ……         │ │ ……         │ │ ……         │             │
│  │            │ │            │ │            │             │
│  │ [选择]     │ │ [已选择]   │ │ [选择]     │             │
│  └────────────┘ └────────────┘ └────────────┘             │
│                                                          │
│  AI 为什么推荐「融合」？                                  │
│  当前设定已经形成清晰的世界规则，但缺少外部冲突。         │
│  融合能引入新元素来创造张力。                              │
│                                                          │
├──────────────────────────────────────────────────────────┤
│  创意质量   新颖度 87   冲突 91   故事潜力 88              │
│                                                          │
│                      [重新生成] [继续 →]                  │
└──────────────────────────────────────────────────────────┘
```

视觉原则：

- 已选择分支：高亮
- 未选择分支：降低视觉权重（淡化色）
- 历史分支：保留可点击，唤起历史查看
- 当前步骤：强调（最大视觉权重）
- 未生成步骤：未来占位（淡灰色）

这样用户始终能看到：

> **「我是怎么从 Idea 走到这里的。」**

### 12.1 顶部进度

```text
创意深度 ● ● ● ○ ○
            3 / 5
```

而不是显示 `depth = 3` 这种开发者用语。

### 12.2 Toolbar 简化

旧 Toolbar 显示「节点数 17 / 活跃节点 4」属于树编辑器思维，v2 移除。

新版 toolbar 只保留：

```text
Step 3 / 5

[查看完整路径]  [重新开始]
```

必要时展开为 `[展开路径]`（让用户看完整 creative_path 文本）。

---

## 13. 当前步骤必须突出

当前 Step 是整个页面视觉中心。

```text
STEP 3 / 5

      融合

[ A ]    [ B ]    [ C ]
```

而历史路径缩小。未来步骤弱化为淡灰色。

### 13.1 选项卡片

```text
┌─────────────────────────────┐
│ B                           │
│                             │
│ 未来世界正在召唤现在的人    │
│                             │
│ 这个变化会让故事从个人      │
│ 修炼升级为跨时代冲突。      │
│                             │
│ 新颖度  87                  │
│ 冲突    91                  │
│                             │
│          [选择这个方向]      │
└─────────────────────────────┘
```

不塞太多信息——只有 title、premise 摘要、AI 推荐理由、评分、可点击按钮。

---

## 14. 分支回溯（v2.1 启用）

v2.0 MVP 不实现回溯——用户在 5 步路径上无法回头。如果用户对某个 Step 的选择不满意，只能：

1. 重置整个 session（保留 root_idea）
2. 重新 init

v2.1 启用回溯功能的设计预览：

- **触发**：用户在历史 Step 卡片上点「从这里重新探索」
- **行为**：保留 Step 1..to_step-1，删除 to_step 的 selected_option，标记 to_step+1..5 为 STALE，重新生成 to_step 的 3 个选项
- **状态机交互**：回溯不删除下游步骤的数据，只标记为 STALE
- **提交校验**：STALE 步骤必须重生或被显式跳过

详细设计推迟到 v2.1，详见 §37。

---

## 15. AI 推荐解释

每一步顶部显示：

```text
STEP 2 / 5

打破 BREAK

改变当前创意中一个已经成立的规则，
看看故事会发生什么。
```

操作类型具有解释性，让用户知道 AI 在做什么。

### 15.1 为什么是这个操作

```text
为什么是「打破」？

当前创意已经有了明确的世界规则，
但角色与规则之间的冲突还不够强。

所以这一步尝试破坏规则本身。
```

建立用户对 AI 的信任。

### 15.2 选项评分

每个选项卡片显示 3 个评分（新颖度 / 冲突强度 / 故事潜力），评分由 NoveltyEvaluator 在生成时自动给出（不要求用户主动点「评估」按钮）。

---

## 16. 创意质量评分

评分不再成为用户必须主动点击的独立工具。

旧版本中 NoveltyEvaluator 是独立「评估」操作，v2 改为：

### 16.1 自动评分

每次用户选择一个创意后，自动刷新：

```text
新颖度     82
冲突强度   91
故事潜力   87
独特性     84
```

### 16.2 评分定位

> **辅助用户判断，而不是让用户玩评分工具。**

评分只显示，不让用户去操作评分。

### 16.3 NoveltyEvaluator 的角色变化

从「用户主动点击 → 独立评估」改为「next-step / select 端点内置调用 → 自动填充到 option 卡片」。

- 调用时机：每个 option 生成时调用一次
- 调用方式：fire-and-forget（不阻塞主流程）
- 显示位置：option 卡片右下角
- 不再单独暴露 `/evaluate` 端点（v1.x 端点保留为兼容 deprecated 头）

---

## 17. 防止创意漂移

### 17.1 一致性检查

每步生成时 prompt 强制包含 `original_idea + current_concept + selected_path` 三元组，防止漂移：

```text
禁止：
第一步：修仙
第二步：科幻
第三步：校园爱情
第四步：商业战争
```

除非用户明确选 `fuse` 操作并指定副类型。

### 17.2 后台一致性校验

每步生成后自动检查（详见 §8.5）：

```text
Concept Consistency     # 概念连贯性
World Logic             # 世界规则自洽
Character Potential     # 角色潜力
Conflict Potential      # 冲突潜力
Novelty                 # 新颖度
```

不合格自动重生，用户不需要看到失败过程。

---

## 18. 保存、恢复与重置

### 18.1 自动保存

canvas_state.json 在每个用户操作后立即持久化（沿用 v1.x 的 `_etag` 乐观锁机制）。用户在任意 Step 中途离开，下次进入读最新状态续作。

### 18.2 重置

重置不是简单「删除 Canvas」，而是：

```text
重新开始创意

这会保留你的原始 Idea，
但删除当前创意路径。

[取消] [重新开始]
```

如果用户想连 Idea 也换，必须：

1. 先重置
2. 然后重新 init

不允许在保留创意路径的情况下修改 Idea——避免「Idea 与当前路径不一致」的语义混乱。

### 18.3 提交前最终确认

```text
你的创意已经形成

创意深度：4 / 5
新颖度：87
核心冲突：91

你将进入下一阶段：
概念 DNA

[返回继续探索]   [形成概念 →]
```

---

# Part IV — 下游契约

## 19. 下游消费清单

Creative Canvas 的 commit 输出（`concept_and_dna.json`）被以下消费者读取。本节明确每个消费者实际使用的字段，避免「字段写了但没人读」的 dead contract。

| 消费者 | 文件 | 实际字段 |
|---|---|---|
| ConceptStep.tsx | `frontend/src/components/wizard/ConceptStep.tsx:216-296` | `concept.{title, premise, tone, theme, target_audience, style_template, source}` + `story_dna.core_contradiction.{statement, side_a, side_b}` |
| ConceptStep prefill check | `ConceptStep.tsx:135` | `story_dna.value_stack.length === 0` 判断是否预填 |
| state_machine.py STAGE1→STAGE2 gate | `backend/conductor/state_machine.py:38-39` | `concept_and_dna.json` 存在性 + `story_dna.core_contradiction.statement` 非空 |
| Stage 2 World/Char prompts | `backend/api/stage2_world_char.py:123,203,449,531,607,712` | `concept` + `story_dna` 整体 |
| Stage 4 Writing prompts | `backend/api/stage4_writing.py:562,590` | `concept` 整体 |
| Project naming | `backend/api/project.py:19` | `concept.title` |

**关键发现**：`canvas_snapshot.selected_path` 与 `story_dna.fusion_meta` 当前没有消费者——它们是 write-only provenance。v2 在 schema 中保留这两个字段，但**不视为契约**。

## 20. concept_and_dna.json v5 Schema

v2 推荐的下游输出 schema（升级版）：

```json
{
  "schema_version": "5",

  "concept": {
    "title": "",
    "premise": "",
    "genre": "",
    "tone": "",
    "theme": "",
    "target_audience": "",
    "style_template": "",
    "source": "canvas"
  },

  "story_dna": {
    "core_contradiction": {
      "statement": "",
      "side_a": "",
      "side_b": ""
    },
    "value_stack": [
      { "level": "personal", "value_a": "", "value_b": "" },
      { "level": "social", "value_a": "", "value_b": "" },
      { "level": "philosophical", "value_a": "", "value_b": "" },
      { "level": "existential", "value_a": "", "value_b": "" }
    ],
    "style_template": "",
    "creative_mechanism": "",          // ← v2 新增
    "creative_path": [...],            // ← v2 新增（详见 §20.1）
    "fusion_meta": { ... } | null      // write-only provenance
  },

  "canvas_meta": {                    // ← v2 新增顶层
    "step_count": 5,
    "skipped_steps": [],
    "operations": ["twist", "break", "fuse"],
    "selected_options": ["option_2_b", "option_3_a", ...],
    "auto_finalize": false
  },

  "canvas_snapshot": {                // 兼容字段（详见 §20.2）
    "selected_path": [...],
    "committed_at": "..."
  }
}
```

### 20.1 creative_path 结构

```json
"creative_path": [
  {
    "step": 1,
    "operation": "twist",
    "operation_reason": "当前创意普通，先建立基础设定",
    "selected_option": {
      "id": "option_1_b",
      "title": "修士必须亲手杀死未来的自己",
      "premise": "...",
      "logic": "..."
    },
    "selected_at": "...",
    "novelty": 0.78
  },
  ...
]
```

### 20.2 契约稳定性原则

| 字段 | 稳定性 | 升级路径 |
|---|---|---|
| `concept.{title, premise, genre, tone, theme, target_audience, style_template, source}` | 稳定 | 已有消费者，禁止改 |
| `story_dna.core_contradiction.{statement, side_a, side_b}` | 稳定 | 已有消费者，禁止改 |
| `story_dna.value_stack`（长度=4 + level ∈ {personal,social,philosophical,existential}）| 稳定 | 已有长度校验 |
| `story_dna.creative_mechanism` / `creative_path` / `canvas_meta` | v2 新增 | 必须配合下游 consumer 升级，详见 §20.3 |
| `canvas_snapshot.selected_path` | 兼容字段 | v2.0 commit 继续写入（供 `scripts/backfill_creative_divergence.py` 等脚本读取）；v2.1 评估移除 |
| `story_dna.fusion_meta` | write-only provenance | 不视为契约 |

### 20.3 下游消费者升级要求

`creative_path` / `creative_mechanism` / `canvas_meta` 三个 v2 新增字段 **不是写完就生效**——必须按顺序升级下游 consumer：

1. **Stage 1 / ConceptStep** 显示「创意路径」摘要（让用户看到自己 5 步的选择链路）
2. **Stage 2 / World** 在世界观生成时注入 `creative_path` 作为「为什么有这个规则」的依据
3. **Stage 3 / Character** 在角色生成时注入 `creative_mechanism`（让角色动机与创意机制对齐）
4. **Stage 3 outline / Map** 在地图生成时注入 `creative_path` 的「场景溯源」
5. **大纲生成**在每章生成时把对应 Step 的 operation_reason 作为「剧情发展方向」的提示

如果跳过上述升级就直接上线 v2，`creative_path` 等字段会变成 write-only dead contract。

**v2.0 MVP 阶段不实施**上述升级——`creative_path` / `creative_mechanism` / `canvas_meta` 在 MVP 阶段**不写入 concept_and_dna.json**，等 v2.1 再随下游 consumer 一起上线。

## 21. Story DNA 概念澄清

PRD v1.0 草案混用「Story DNA」与「Concept DNA」两个术语。本节明确：

- **Concept DNA** 是用户视角的产品概念名——指用户从 Creative Canvas 出来时拿到的「故事基因」
- **Story DNA** 是技术 schema 名——指 `concept_and_dna.json` 中的 `story_dna` 字段
- **两者指代同一份数据**（即 `concept_and_dna.json` 文件），只是命名视角不同

文件本身只有一份 `concept_and_dna.json`，不要试图在 wizard 流程中新增「概念 DNA」独立 stage——它就是 `concept_and_dna.json` + ConceptStep 的组合。

---

# Part V — 数据模型与迁移

## 22. canvas_state.json v4 Schema

```json
{
  "schema_version": 4,
  "session_id": "uuid",
  "_etag": "16-char-hex",

  "root_idea": {
    "prompt": "...",
    "genre": "仙侠",
    "premise": "...",
    "extracted": {
      "genre": "仙侠",
      "core_elements": ["修仙", "时间", "自我"],
      "potential_conflict": "现在 vs 未来"
    }
  },

  "creative_session": {
    "current_step": 3,
    "max_steps": 5,
    "status": "active|completed|committed|aborted"
  },

  "creative_path": [
    {
      "step": 1,
      "operation": "twist",
      "operation_reason": "...",
      "options": [
        { "id": "option_1_a", "title": "...", "premise": "...", "scores": {...} },
        { "id": "option_1_b", "title": "...", "premise": "...", "scores": {...} },
        { "id": "option_1_c", "title": "...", "premise": "...", "scores": {...} }
      ],
      "selected_option_id": "option_1_b",
      "created_at": "...",
      "selected_at": "...",
      "regenerated_count": 0,
      "state": "completed"
    },
    ...
  ],

  "current_concept": {
    "premise": "...",
    "core_conflict": "...",
    "characters": [],
    "world_rules": [],
    "tropes": [],
    "themes": [],
    "novelty": 0.82
  },

  "final_concept": null,
  "committed": false,
  "committed_at": null,
  "committed_concept_ref": "concept_and_dna.json",

  "scores": {
    "novelty": 0.82,
    "conflict": 0.91,
    "story_potential": 0.87,
    "uniqueness": 0.84,
    "computed_at": "..."
  },

  "session_metadata": {
    "created_at": "...",
    "last_modified_at": "...",
    "elapsed_seconds": 3600,
    "operation_count": 12
  }
}
```

### 22.1 与 v3 的差异

| v3 字段 | v4 处理 |
|---|---|
| `root_node_id` | 删除（v2 不再有 node id 概念） |
| `nodes` | 删除 |
| `edges` | 删除 |
| `selected_path` | 删除（被 `creative_path[].selected_option_id` 替代）|
| `branch_choices` | 删除 |
| `core_contradiction` | 删除（搬到 `final_concept.core_contradiction`） |
| `novelty_scores` | 删除（被 `creative_path[].options[].scores` + 顶层 `scores` 替代） |
| `idea_variants` | 删除（被 `creative_path[].options[]` 替代） |
| `raw_intent` | 保留 + 映射到 `root_idea`（不删除，详见 §23.4） |
| `_etag` | 保留 |

### 22.2 字段类型与命名一致性

- `selected_option_id`（不是 `selected`）——`creative_path[i].selected_option_id` 引用 `creative_path[i].options[j].id`
- `state` 字符串值为 §5.1 列出的 5 态之一
- 时间戳统一为 ISO 8601

## 23. 迁移策略

### 23.1 总体策略

采用 **lazy migration**（不一次性扫库迁移），在读端点（`GET /state`）按需升级 schema。

```python
def _read_canvas(project_id) -> dict:
    canvas = _get_fm().read_json(project_id, "canvas_state.json")
    if canvas is None:
        return _empty_canvas_v4()
    if canvas.get("schema_version") == 4:
        return canvas
    if canvas.get("schema_version") == 3:
        return _migrate_v3_to_v4(canvas)
    raise HTTPException(409, "UNKNOWN_SCHEMA_VERSION")
```

每次写操作完成后用 `_write_canvas` 把 v4 写回（带新 `_etag`）。

### 23.2 v3 → v4 转换规则

| v3 字段 | v4 字段 | 转换逻辑 |
|---|---|---|
| `raw_intent` | `root_idea` + `root_idea.extracted` | `raw_intent.prompt → root_idea.prompt`，`raw_intent.genre_primary → root_idea.genre`，`raw_intent.trope_tags → root_idea.extracted.core_elements`（若 LLM 提取已完成）|
| `root_node_id` + `nodes[id]` | `creative_path[0].selected_option` | 取 `selected_path[1]` 对应节点的 content 作为 Step 1 selected option premise |
| `selected_path[i]` | `creative_path[i-1].selected_option_id` | 需要查 `branch_choices` 或 `edges` 推断 |
| `branch_choices` | （删除）| 隐含在 selected_option_id 中 |
| `nodes[id].novelty_score` | `creative_path[i].options[j].scores.novelty` | 按 node id 匹配 |
| `idea_variants` | （删除，但保留引用）| 用于 `concept_and_dna.json` 的 `fusion_meta` 推断 |
| `core_contradiction` | （搬到 `final_concept.core_contradiction`，或新建）| 若已 commit，搬到 final_concept；否则迁移到 root_idea.extracted.potential_conflict |
| `novelty_scores` | `scores` | 直接映射 |
| `committed_at` / `committed_concept_ref` | 保留 | 不变 |

### 23.3 已 commit 项目的处理

如果 canvas_state 已经标记 `committed_at != null`（即 concept_and_dna.json 已存在）：

- **不再迁移 canvas_state.json 到 v4**——保持 v3，避免误覆盖 commit 后的状态
- 但 `GET /state` 仍然返回 v4 schema 的视图（迁移函数纯只读）
- 写操作被禁止（v4 view 不允许修改已 commit 的 session）

### 23.4 raw_intent 保留决策

`raw_intent` 在 v3 是 init 端点的输入契约。如果 v4 删除 raw_intent，前端 `WizardSidebar` / `InitRequest` 等已绑定该字段名的代码会报 422（参 memory `feedback_frontend_mock_hides_contract_drift.md`）。

**决策**：保留 `raw_intent` 作为 v4 顶层字段，但与 `root_idea` 双写（init 时同步更新两边）。

```python
canvas["raw_intent"] = data.raw_intent.dict()
canvas["root_idea"] = {
    "prompt": data.raw_intent.prompt,
    "genre": data.raw_intent.genre_primary,
    "premise": data.raw_intent.prompt,
    "extracted": {"core_elements": data.raw_intent.trope_tags or []}
}
```

v2.1 再考虑彻底移除 raw_intent（需要前端先做契约升级）。

### 23.5 _etag 保留

`_etag` 乐观锁是 v3 已有机制，避免两个人同时编辑。v4 完全保留。

### 23.6 下游脚本兼容性

`scripts/backfill_creative_divergence.py:61` 等脚本读取 `canvas_snapshot.selected_path`：

- **v2.0 commit 输出**：`canvas_snapshot.selected_path` 仍写入（与 v3 兼容）；同时若 `creative_path` 写入则下游脚本可读取新字段
- **实施时需全量扫描**：用 `grep -rn "canvas_snapshot\|selected_path\|canvas_state" backend/ scripts/` 找出所有 reader，列入迁移清单
- **v2.1**：评估移除 `selected_path` 双写（需先确认没有 reader）

---

# Part VI — AI 引擎层

## 24. 现有引擎的新定位

v2 不删除任何现有 engine，而是改变它们的角色定位：

| 引擎 | 旧角色 | v2 角色 |
|---|---|---|
| **WhatIfEngine** | 核心树生成器（BFS depth=3, breadth=4） | **可选的创意候选生成能力**——不再强制用树展开路径，而是作为「一次生成多个候选」的能力被 next-step 端点按需调用 |
| **MutationEngine** | 用户主动点的 mutation 操作 | **扭曲（O1）+ 升级（O5）的底层实现**——next-step 选择 twist/escalate 时调用；v2.1 启用「极端化（O7）」时也归本引擎 |
| **ContradictionEngine** | 独立 contradiction 工具 | **打破（O2）+ 反转（O4）的底层实现**——next-step 选择 break/invert 时调用；同时为 final_concept 生成 core_contradiction 提供模板 |
| **GenreFusionEngine** | 类型融合 BFS 距离矩阵 | **融合（O3）的候选生成器**——next-step 选择 fuse 时调用，提供 risk_level 标注 |
| **NoveltyEvaluator** | 用户主动点的「评估」按钮 | **自动后台评估**——每个 option 生成时调用一次，结果嵌入 option.scores；不再暴露独立端点 |

## 25. AI 引擎架构

```text
                   Creative Canvas v2
                          │
              Creative Director Agent
                          │
        ┌─────────────────┼─────────────────┐
        ↓                 ↓                 ↓
  MutationEngine   ContradictionEngine   FusionEngine
        │                 │                 │
        └─────────────────┼─────────────────┘
                          ↓
                 Candidate Generator
                 (LLM: 1 次调用返回 operation + operation_reason + options[3])
                          ↓
                       3 Options
                          ↓
                       User
```

### 25.1 单次 LLM 调用的契约

```
输入: current_concept + selected_path + current_step + max_steps + candidate_operation_hint
输出: { operation, operation_reason, options[3] }
```

不再分两步（先选 op，再为 op 生成 options），减少 50% 延迟。

### 25.2 engine fallback 链

如果某个 engine 内部失败（LLM timeout / JSON 解析失败），按以下顺序 fallback：

1. 重试同 engine 1 次
2. fallback 到通用 LLM prompt（不依赖具体 engine 的硬约束）
3. 仍失败则返 503 `GENERATION_FAILED`，UI 显示「AI 暂时无法生成，请重试或稍后再试」

---

# Part VII — API

## 26. 端点清单

| Method | Path | 用途 | 替代的 v1.x 端点 | 启用版本 |
|---|---|---|---|---|
| POST | `/creative/session/init` | 初始化 session，接收 raw_intent | `/init` | v2.0 |
| POST | `/creative/session/next-step` | 生成下一步的 operation + 3 options | `/expand` + `/fuse` | v2.0 |
| POST | `/creative/session/select` | 用户选择某选项，触发下一步生成 | `/select` + `/choose-branch` | v2.0 |
| POST | `/creative/session/regenerate` | 重新生成当前 step 的 3 个选项 | （v1.x 无对应） | v2.1 |
| POST | `/creative/session/backtrack` | 回溯到历史 step | （v1.x 无对应） | v2.1 |
| GET | `/creative/session/state` | 读取 canvas_state（lazy migrate） | `/state` | v2.0 |
| DELETE | `/creative/session/state` | 重置 session（保留 root_idea） | `/state` (DELETE) | v2.0 |
| POST | `/creative/session/finalize` | 触发 final_concept 生成（早收束） | （v1.x 无对应） | v2.1 |
| POST | `/creative/session/commit` | 写 concept_and_dna.json + 双写 | `/commit` | v2.0 |
| POST | `/creative/session/evaluate` | （deprecated）| `/evaluate`（保留兼容头）| v2.0（仅 deprecated 头）|

> **Implementation note (v2.0 shipped):** the actual endpoint paths use the project-scoped namespace `/api/creative/canvas/{project_id}/session/*` instead of the flat `/creative/session/*` listed above. See §26.1 for the namespace-decision ADR explaining the deviation.

### 26.1 命名空间决策（v2.0 implementation ADR）

PRD §26 表格里的端点路径是 `/creative/session/*`，但 v2.0 实际实现的 router（`backend/api/v2_canvas.py`）使用的路径是 `/api/creative/canvas/{project_id}/session/*`。这里保留 project-scoped 命名空间（并在前面加 `/api` 前缀），理由如下：

1. **路由层级一致性：** PRD §11.1 页面路由是 `/project/:projectId/stage6/plot`（**路径演进史**：v1.x 阶段位于 Stage 0（`/project/:projectId/stage0/canvas`）；v1.8.1 改造把 canvas 从 Stage 0 提升到 Stage 1，作为与 divergence 并列的 Step 1 入口，路由变为 `/project/:projectId/stage1/canvas`；2026-09-04 重命名 + 步骤重排把它移到 Map 和 Outline 之间（Step 6），同时更名为「剧情画布 / Plot Canvas」，当前路由为 `/project/:projectId/stage6/plot`，实际代码见 `frontend/src/App.tsx` 与 `frontend/src/pages/PlotCanvasPage.tsx`）。前端路由已经是 `/project/{projectId}/...`，后端 API 在同一个 project 维度下加一层 `/api/creative/canvas/{project_id}/` 既匹配前端的资源层级，又让 OpenAPI 文档与前端生成代码（如果将来引入 typed client）能直接按 project 分组端点。**注意：后端 API 路径（`/api/creative/canvas/{project_id}/`）保持不变，仅前端路由随步骤重排而调整。**

2. **与既有 v1.x 端点保持一致：** 旧版 v1 divergence endpoints 都在 `backend/api/creative_diverge.py` 里实现，路径里本身就有 `{project_id}` path-param（参见 `creative_diverge.py:46` 的 `prefix="/api/v1/projects/{project_id}/creative/diverge"`）。v2 router 把这个 project-scoped 写法沿用过来，避免出现「v1 用 `/state/{pid}`、v2 用 `/state`」的认知割裂——尤其在 v1/v2 共存期间（`enable_canvas_v2` flag 控制 router 是否挂载）。

3. **`/api` 前缀对齐：** 2026-09-03 的 bug 修复（无提示的 404 → 用户看到「暂无内容」）发现，**`/api` 前缀不能省**。前端 `request()` helper（`frontend/src/api/client.ts:5,43`）会自动给所有路径加 `/api` 前缀（与 v1 divergence 的 `/api/v1/...` 对齐）；如果后端 v2 router 只用 `/creative/canvas/...`，请求永远到不了后端，前端拿到的永远是 404。修复方式：v2 router prefix 加 `/api`，与 v1 divergence 一致。这点反过来给将来加子资源（如 `/api/creative/canvas/{pid}/sessions/{sid}/...`）留出了空间。

4. **权限/审计的扩展空间：** project-scoped 路径让中间件能直接通过 path-param 取到 `project_id`，做项目级鉴权与审计日志时不需要从 body 或 header 反查。

5. **多 session 隔离：** 同一个 project 在未来 v2.1 可能并存多个并行分支（早收束 + backtrack 引入的 STALE 节点让「同一 project 多个 active session」成为可能），project-scoped 路径为后续在 `{project_id}` 下挂子资源（如 `/creative/canvas/{project_id}/sessions/{session_id}/...`）留出了空间，而 flat `/creative/session/*` 没有这种扩展性。

**结论：** v2.0 实际路径 = `/api/creative/canvas/{project_id}/session/{verb}`，对应 §26 表格里的 verb 列。后续 v2.1 的 `regenerate`、`backtrack`、`finalize` 端点都遵循同一形状。

## 27. 关键端点语义

### 27.1 next-step

```json
// 请求
{ "session_id": "...", "current_step": 2 }

// 返回
{
  "step": 3,
  "operation": {
    "type": "fusion",
    "name": "融合",
    "reason": "..."
  },
  "options": [
    { "id": "opt_a", "title": "...", "premise": "...", "scores": {...} },
    { "id": "opt_b", "title": "...", "premise": "...", "scores": {...} },
    { "id": "opt_c", "title": "...", "premise": "...", "scores": {...} }
  ]
}
```

### 27.2 select

```json
// 请求
{ "session_id": "...", "step": 3, "option_id": "option_3_b" }

// 服务端行为
保存选择 → 更新 current_concept → 自动触发 next-step 生成 Step 4
```

### 27.3 regenerate（v2.1 启用）

```text
[重新生成 3 个方向]
```

要求：

- 新生成的 3 个选项与当前 3 个选项 premise 余弦相似度 < 0.85
- 记录 `regenerated_count`，单 step 默认上限 3 次
- 超过 3 次返 422 `REGENERATE_LIMIT_EXCEEDED`，UI 提示「建议回溯一步」

### 27.4 backtrack（v2.1 启用）

```json
// 请求
{ "session_id": "...", "to_step": 2 }
```

行为：保留 Step 1..to_step-1，删除 to_step 的 selected_option，标记 to_step+1..5 为 STALE，重新生成 to_step 的 3 个选项。

**约束（v2.1 实施时必须明确）**：

- 允许回溯到任何 Step 1..current_step-1（包括 Step 1，即清空全部）
- 允许在已 `COMPLETED` 的步骤上调用；不允许在 `AVAILABLE` 步骤上调用（必须先 `ACTIVE` 才可回溯）
- 不限制单 session 的回溯次数（state 复杂度通过 STALE 状态自然控制）
- 回溯后 `current_step` 重置为 `to_step`，UI 跳到该 Step

### 27.5 finalize（v2.1 启用）

```json
// 请求
{ "session_id": "...", "auto": false }
```

行为：生成 final_concept（调用 PlannerAgent + canvas_to_concept.yaml），写入 canvas_state.final_concept。

v2.0 不需要此端点——final_concept 在 commit 时由 commit 端点直接生成。

### 27.6 commit

行为：

1. 校验：`creative_path` 中所有 step ∈ {COMPLETED, LOCKED}，无 STALE 步骤
2. 校验：`final_concept` 必须非空（v2.0 阶段直接生成；v2.1 通过 finalize 端点预生成）
3. 从 canvas_state 派生 v2 新增字段（v2.1 启用：creative_path 摘要、canvas_meta；若 MVP 阶段未启用，跳过本步）
4. 写 `concept_and_dna.json`（v5 schema，含 creative_path + creative_mechanism + canvas_meta）
5. 双写 `creative_divergence.json`（保留 v1.x 兼容）
6. canvas_state 标 committed_at
7. 失败回滚：若 concept_and_dna 写入失败但 canvas_state 已标 committed，留待下次 commit 覆盖（v1.x 行为保留）

---

# Part VIII — MVP 与指标

## 28. MVP 范围（v2.0）

v2.0 MVP 只做核心路径，复杂功能推迟到 v2.1。

### 28.1 必须有

- Idea 输入 + 类型选择
- 1～5 Step（固定 5 步，**MVP 不实现早收束**）
- AI 自动选择创意操作（默认 AI 推荐，**MVP 不实现用户主动 override**）
- 每步 3 个选项
- 用户选择
- 横向路径 UI
- 自动保存
- 最终创意总结（final_concept）
- Commit 到下游（写 v5 concept_and_dna.json，**MVP 不写 creative_path / creative_mechanism / canvas_meta 三个 v2 新增字段**——详见 §20.3 与 §27.6 第 3 步）
- canvas_state v4 schema + lazy migration
- Step 状态机 5 态 + transition rule

### 28.2 v2.1 再做

- 早收束（§36）——MVP 阶段用户必须走满 5 步
- 用户主动 override AI 推荐的操作类型（§38）
- 分支回溯（§37）——MVP 阶段用户不允许回溯
- Regenerate（§39）——MVP 阶段如果选项不满意只能 commit 或重置
- 下游消费者升级：ConceptStep / World / Character / Map / Outline 读取 creative_path（§20.3）
- concept_and_dna.json 加 v2 新增字段（§20 / §27.6 第 3 步）

### 28.3 不做（永远不做）

- 无限树 / 任意拖拽节点 / 手工连接节点 / 复杂节点编辑
- 用户自定义 mutation pipeline
- 多人协作
- 跨项目 IdeaPool
- 24h 草稿自动归档

### 28.4 MVP 阶段下游契约

v2.0 commit 输出**保留 v3 兼容 schema**（即不写 creative_path / creative_mechanism / canvas_meta 三个 v2 新增字段）。下游 ConceptStep 等消费者**不修改**。

v2.1 才升级下游消费者（按 §20.3 顺序），同时 commit 输出加 v2 新字段。

## 29. 核心指标

### 29.1 弃用指标

- 节点数量（属于树编辑器思维）
- 节点展开深度（属于树编辑器思维）

### 29.2 v2 新指标

| 指标 | 定义 | 期望值 | 启用版本 |
|---|---|---|---|
| **Activation** | 输入 Idea 并 init session 的用户比例 | ≥ 70% | v2.0 |
| **Completion** | 完成至少 3 Step 的用户比例 | ≥ 80% | v2.0 |
| **Path Selection Rate** | 每一步成功选择（未走早收束 / 重生）的比例 | ≥ 90% / 步 | v2.0 |
| **Regeneration Rate** | 每一步 regenerate 的比例 | ≤ 30% / 步 | v2.1 |
| **Early Exit Rate** | 哪一步用户主动放弃 / 早收束 | 分布应均匀，无明显断崖 | v2.1 |
| **Backtrack Rate** | 多少 session 触发过回溯 | ≤ 20% | v2.1 |
| **Finalization** | 完成 5 步并生成 final_concept 的比例 | ≥ 60% | v2.0 |
| **Downstream Conversion** | commit 后进入 ConceptStep / World / Character 的比例 | ≥ 85% | v2.0 |

### 29.3 最重要的产品指标

**Idea → Story Concept Conversion Rate**

```text
输入 Idea
       ↓
完成创意路径
       ↓
形成可消费的剧情创意
```

Creative Canvas 的成功标准不是「AI 生成了多少创意」，而是：

> **有多少模糊 Idea 最终变成了用户愿意继续创作的故事。**

## 30. V2 一句话定义

> **Creative Canvas 是一个最多 5 步的 AI 创意推演器：用户从一个 Idea 出发，AI 每一步通过「扭曲、打破、融合、反转、升级」等创意操作生成 3 个方向，用户选择其中一个继续推进，最终形成一条可视化的剧情创意路径，并将这条路径转化为下游 Concept DNA、世界观、角色、地图和大纲的创作基础。**

## 31. 信息架构（最终图）

> 实施顺序详见 §34——v1.3 类型融合接线落地后再做 v2 重构可降低风险。

```text
Creative Canvas
│
├── Root Idea
│
├── Creative Path (≤ 5 Step)
│   │
│   ├── Step 1
│   │   ├── State (LOCKED|AVAILABLE|ACTIVE|COMPLETED|STALE)
│   │   ├── Operation (twist|break|fuse|invert|escalate)
│   │   ├── Options[3]
│   │   │   └── Selected Option
│   │   └── Scores
│   │
│   ├── Step 2 ...
│   ├── Step 3 ...
│   ├── Step 4 ...
│   └── Step 5 (Dramaturgy)
│
├── Current Concept
│   ├── Premise
│   ├── Core Conflict
│   ├── Characters
│   ├── World Rules
│   ├── Tropes
│   └── Themes
│
├── Final Concept (Step 5 或早收束生成；v2.0 仅 Step 5)
│
└── Commit
       ↓
   concept_and_dna.json (v5 schema；v2.0 MVP 阶段不写 v2 新增字段)
       ↓
       ↓
   ConceptStep (wizard Step 2)
       ↓
   Stage 2 World
       ↓
   Stage 3 Character
       ↓
   Stage 3 Map
       ↓
   剧情画布 (Step 6)        // 2026-09-04：v2.0 重构后将 canvas 重命名 + 步骤重排至 Map 与 Outline 之间
       ↓
   Stage 3 Outline
```

---

# Part IX — 关键的产品重构决策

## 32. 模型转换总结

| v1.x | v2 |
|---|---|
| What-if Tree | Creative Path |
| 用户探索节点 | 用户做创意决策 |
| 无限/较深树状展开 | 最多 5 Step |
| 一次产生多个节点 | 每一步严格 3 个候选 |
| 用户主动找工具 | AI 推荐创意操作 |
| Mutation 是功能 | 扭曲是创作行为 |
| Contradiction 是引擎 | 打破是创作行为 |
| Fusion 是功能 | 融合是创作行为 |
| Novelty 是独立评估 | 自动辅助判断 |
| Canvas 是节点编辑器 | Canvas 是创意旅程 |
| selected_path | creative_path |
| 最终提交节点 | 最终剧情创意 |
| Canvas → Concept | Canvas → **Concept DNA → 下游创作链** |

---

# Part X — 实施注意事项

## 33. 与 v1.3 类型融合的协同

`docs/superpowers/specs/2026-09-01-genre-fusion-wiring-design.md` 与 `plans/2026-09-01-genre-fusion-wiring.md`（2026-09-01 编写）的 v1.3 fusion 接线是面向 v1.x WhatIf 树模型的。

**v2 实施时需要决策**：

- 选项 A：先完成 v1.3 fusion 接线（在 v1.x 上落地），然后做 v2 重构（v2 直接复用已落地的 fusion 能力）
- 选项 B：跳过 v1.3 落地，直接在 v2 中实现 fusion 操作（v1.3 spec 失效）

推荐选项 A（降低 v2 实施风险），但需要在 v2 项目 kickoff 时确认。

## 34. 与 v1.x 已知问题的修复

PRD §5.4 在实施时必须解决的 v1.x bug（参 memory `project_divergence_a_to_e_empty_path.md`）：

- StepIndicator 在 selectedPath=[] 时仍显示「5. 提交」可点击
- commit 端点不校验 selectedPath

v2 通过 §5.3 关键不变量 + §27.6 commit 校验根除。

## 35. 与现有 wizard 流程的集成

剧情画布（Plot Canvas）是 wizard 流程的 Step 6，位于 Map（Step 5）和 Outline（Step 7）之间。提交成功后跳转路径：

```text
plot canvas commit 成功
       ↓
写入 concept_and_dna.json + creative_divergence.json
       ↓
navigate(/project/:id/stage3)  // 落在 Stage 3 Tab
       ↓
用户点 Step 6 (剧情画布) 入口回到画布，或继续向 Step 7 (Outline) 推进
       ↓
GET /stage1/concept → ConceptStep.tsx 渲染（如需修改 Concept，回到更早的 wizard 步骤）
```

> **历史说明：** 早期版本（v1.x 阶段）剧情画布位于 Stage 0，作为子页面 `/project/:id/stage0/canvas`；v1.8.1 提升至 Stage 1（与 divergence 并列的 Step 1 入口）；2026-09-04 重命名 + 步骤重排把它移到 Map 与 Outline 之间（Step 6），后端 API 路径（`/api/creative/canvas/{project_id}/`）保持不变。

---

# Part XI — Future Work（v2.1+）

本 Part 集中描述 v2.0 MVP 不实现、推迟到 v2.1 及之后的功能。v2.0 实施时**不需要**实现这些，但需要在代码注释中标注 `// v2.1+` 以便后续跟进。

## 36. 早收束（v2.1）

### 36.1 设计意图

不是强制要求用户走满 5 步。AI 可以判断当前创意已经足够成熟，用户也可以主动触发。

### 36.2 触发方式

#### 自动触发

每步完成后（用户选择一项）AI 评估当前 concept：

```text
检查清单（任一满足即可）：
✓ 已有明确的核心冲突
✓ 已有独特的世界规则
✓ 已有推动剧情的角色动机
✓ novelty ≥ 70 AND path 选择多样性（3 步中至少 2 次选了非默认 A）
```

如果满足，UI 显示：

```text
当前创意已经形成了清晰的：
✓ 独特设定
✓ 核心冲突
✓ 故事动力

建议现在收束。

[继续探索] [形成剧情创意 →]
```

#### 用户主动触发

用户在 toolbar 随时可以点「提前收束」按钮，跳过剩余步骤。

### 36.3 状态机影响

早收束不影响 5 步上限规则——只是把剩余的 `LOCKED / AVAILABLE` 步骤标记为 `SKIPPED`。

§5.1 状态表 v2.1 补充：

| 状态 | 含义 | UI 视觉 |
|---|---|---|
| `SKIPPED` | 早收束时被跳过的步骤 | 灰色虚线，仅显示「已跳过」标签 |

状态转移增加：

```text
用户触发早收束
       ↓
当前 Step 保持 COMPLETED
       ↓
current_step+1..5 标 SKIPPED（不再 AVAILABLE / LOCKED）
       ↓
final_concept 生成（调用 PlannerAgent）
       ↓
commit 可触发
```

### 36.4 关键约束

> **5 是上限，不是必须。早收束不是「放弃」，而是「加速」。**

UI 文案必须强调这一点，避免用户感觉「我是不是没做完」。

## 37. 分支回溯（v2.1）

### 37.1 触发

用户在历史 Step 卡片上点「从这里重新探索」。

### 37.2 行为

```text
Step 1 → B  (保留)
Step 2 → A  (回溯到这里)
Step 3 → A  (标 STALE)
Step 4 → C  (标 STALE)
Step 5 → -  (标 LOCKED，但保留元数据以便重生)
```

回溯后：

- 已保存的 Step 1 选项保留
- Step 2 重新进入 ACTIVE 状态，AI 重新生成 3 个选项
- Step 3-5 标 STALE，直到用户再次走到那里时重生

### 37.3 与回溯状态机的交互

回溯不删除下游步骤的数据，只标记为 `STALE`。这样：

- 用户在回溯后重新走完路径，可以无副作用提交
- 如果用户后悔再次回溯，回溯链可被追溯
- 提交校验时 STALE 步骤必须重生或被显式跳过

### 37.4 与早收束的协同

如果回溯路径只走到 Step 3 早收束，Step 4-5 的 STALE/LOCKED 状态不影响 commit（因为不参与 final_concept 生成）。

## 38. 用户主动 override AI 推荐（v2.1）

v2.1 启用用户在 AI 推荐之外主动切换操作类型。

UI 显示：

```text
AI 推荐：打破

[ 扭曲 ] [ 打破 ✓ ] [ 融合 ] [ 反转 ] [ 升级 ]
```

用户点其他操作时，重新调用「选 op + 生成 options」流程。

**软 guard**：如果用户在最近 3 步中累计 ≥ 2 次主动 override AI 推荐，在 UI 顶部显示一条轻提示：「你正在主导创意方向」——这是 informational 而非阻塞。

实现：next-step 端点接受 `override_operation: string | null` 参数。当 `override_operation` 非空时，§8.4 的 `compute_op_hint` 不调用，直接用 `override_operation`。

## 39. Regenerate 功能（v2.1）

如果三选项都不满意，用户可主动触发 regenerate。

### 39.1 端点

```text
POST /creative/session/regenerate
{ session_id, step }
```

### 39.2 要求

- 新生成的 3 个选项与当前 3 个选项 premise 余弦相似度 < 0.85
- 记录 `regenerated_count`，单 step 默认上限 3 次
- 超过 3 次返 422 `REGENERATE_LIMIT_EXCEEDED`，UI 提示「建议回溯一步」

### 39.3 UI

选项卡片上方显示「[重新生成 3 个方向]」按钮（仅在 regenerate 未达上限时显示）。