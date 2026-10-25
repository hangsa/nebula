import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { MemoryRouter, Routes, Route } from "react-router-dom";

// Use vi.hoisted() so the mock factory can reference these mocks (the
// factory is hoisted above regular `const` declarations, so any non-hoisted
// reference evaluates to undefined at mock time).
const {
  mockedGetProjectStatus,
  mockedGetStage4Progress,
  mockedGetOutline,
  mockedGetNovelOutline,
  mockedGenerateOutline,
  mockedWriteScene,
  mockedFactGuard,
  mockedGetSceneDraft,
  mockedStopAutopilotSession,
  mockedGetSceneDrafts,
  mockedRepairProgress,
} = vi.hoisted(() => ({
  mockedGetProjectStatus: vi.fn().mockResolvedValue({ title: "T" }),
  // Mirrors the backend's no-progress response: chapters=[] but total_chapters
  // is set (either from progress.json or, after the Pre-step, from
  // novel_outline.json). Tests that care about a specific total override
  // via mockResolvedValueOnce.
  mockedGetStage4Progress: vi
    .fn()
    .mockResolvedValue({ chapters: [], total_chapters: 0 }),
  mockedGetOutline: vi.fn().mockResolvedValue({ chapters: [] }),
  mockedGetNovelOutline: vi.fn().mockResolvedValue({ volumes: [] }),
  mockedGenerateOutline: vi.fn().mockResolvedValue({ chapters: [] }),
  // writeScene: per-test override via mockResolvedValueOnce. Default returns
  // an empty draft so unrelated tests that click 重新生成 don't break.
  mockedWriteScene: vi.fn().mockResolvedValue({
    scene_number: 0,
    status: "passed",
    retry_count: 0,
    draft_text: "",
    parsed_logs: [],
    fact_guard_results: { all_passed: true, checks: [], coherence_score: 0 },
    registry_updates: { created: [], updated: [], cascade_executed: [] },
    l0_snapshot: {},
    precheck_result: { precheck_passed: true, suggestions: [], tokens_used: 0, skipped_reason: "" },
  }),
  // Default: pass. Tests that care about a specific result override via
  // mockResolvedValueOnce.
  mockedFactGuard: vi.fn().mockResolvedValue({
    all_passed: true,
    checks: [],
    coherence_score: 95,
  }),
  // Default: empty draft (no on-disk content). The draft-load regression
  // test overrides via mockResolvedValueOnce to simulate a saved scene.
  mockedGetSceneDraft: vi.fn().mockResolvedValue({
    draft_text: "",
    chapter_number: 1,
    scene_number: 1,
    parsed_logs: [],
    fact_guard_results: null,
    coherence_score: 0,
  }),
  // Layer 3 fix: managed→manual switch must POST /session/stop so the
  // on-disk session.json doesn't stay in state="running" with a stale
  // current_task. Default-resolves to an empty object — tests assert via
  // mockClear()/toHaveBeenCalledWith().
  mockedStopAutopilotSession: vi.fn().mockResolvedValue({}),
  // Default: no drafts on disk for any chapter. Tests override via
  // mockResolvedValueOnce or mockImplementation to drive the chapter-level
  // scene-status dots and the refetch-on-chapter-switch behavior.
  mockedGetSceneDrafts: vi.fn().mockResolvedValue({ chapter_number: 0, scenes: [] }),
  // v1.10 fix: refresh button calls api.repairProgress to reconcile stuck
  // chapters (status=in_progress with all scenes terminal) without invoking
  // the LLM advance path. Default-resolves to no repairs — tests assert via
  // mockClear()/mockResolvedValueOnce.
  mockedRepairProgress: vi.fn().mockResolvedValue({
    repaired_chapters: [],
    current_chapter: 1,
  }),
}));

vi.mock("../api/client", () => ({
  default: {
    getProjectStatus: mockedGetProjectStatus,
    getStage4Progress: mockedGetStage4Progress,
    getOutline: mockedGetOutline,
    getConcept: vi.fn().mockResolvedValue({ concept: null, story_dna: null }),
    getWorld: vi.fn().mockResolvedValue({}),
    getCharacter: vi.fn().mockResolvedValue({ characters: [] }),
    getNovelOutline: mockedGetNovelOutline,
    updateOutline: vi.fn().mockResolvedValue(undefined),
    updateSceneDraft: vi.fn().mockResolvedValue({ chapter_number: 1, scene_number: 1 }),
    getSceneDraft: mockedGetSceneDraft,
    generateOutline: mockedGenerateOutline,
    writeScene: mockedWriteScene,
    factGuard: mockedFactGuard,
    stopAutopilotSession: mockedStopAutopilotSession,
    getSceneDrafts: mockedGetSceneDrafts,
    repairProgress: mockedRepairProgress,
    // v2.x (Plan Task 12, 2026-08-30): WorkspacePage now runs a 6-endpoint
    // preflight to decide whether the wizard is "done". These mocks make
    // every endpoint resolve with content so the manuscript tab is
    // unlocked — otherwise the page would route to <WorkspaceWizardPanel>
    // instead of the writing panel these tests exercise.
    getCreativeDivergencePrefill: vi
      .fn()
      .mockResolvedValue({ exists: true, has_selection: true }),
    listCreativeDivergenceVariants: vi.fn().mockResolvedValue({ variants: [] }),
    generateCreativeDivergenceVariants: vi
      .fn()
      .mockResolvedValue({ variants: [] }),
    selectCreativeDivergenceVariant: vi.fn().mockResolvedValue({ ok: true }),
    // Plan Task 13 (2026-08-30): ContextPanel renders DiagnosisSummary for
    // the default (?panel=concept → unknown, body falls through to diagnosis)
    // and explicit diagnosis/export panels. The tests don't exercise the
    // diagnosis UI, so we stub both endpoints to safe defaults that match
    // DiagnosisSummary's no-report state.
    getDiagnosis: vi.fn().mockRejectedValue(new Error("no diagnosis yet")),
    runDiagnosis: vi.fn().mockResolvedValue({
      project_id: "p", total_chapters: 0, issues: [],
      summary: { p0_count: 0, p1_count: 0, p2_count: 0 },
    }),
    // 3B divergence (Plan 2026-09-05): useB3Divergence fires
    // getB3State on mount. Mock empty-state so the hook's HYDRATE
    // runs without throwing.
    getB3State: vi.fn().mockResolvedValue({
      schema_version: 1,
      project_id: "p1",
      raw_intent: null,
      stage2_candidates: [],
      stage3_deepened: [],
      committed: false,
    }),
    deleteB3State: vi.fn().mockResolvedValue({ ok: true }),
    // Theme migration (2026-09-05): S1InputStep calls useGenres → api.listGenres
    listGenres: vi.fn().mockResolvedValue([]),
    // Task 12 (2026-09-10): S1InputStep now reads creative dimensions via
    // useCreativeDimensions → api.listActiveCreativeDimensions.
    listActiveCreativeDimensions: vi.fn().mockResolvedValue({ subject: [], tone: [], style: [] }),
  },
}));

vi.mock("../hooks/useAutopilotSession", () => ({
  useAutopilotSession: vi.fn(),
}));

const {
  mockedStartAutopilotSession,
  mockedGetAutopilotSession,
} = vi.hoisted(() => ({
  mockedStartAutopilotSession: vi.fn().mockResolvedValue({
    state: "running", current_task: null, queue: [], history: [], config: null,
  }),
  mockedGetAutopilotSession: vi.fn().mockResolvedValue(null),
}));

vi.mock("../api/autopilot", async (importActual) => {
  const actual = await importActual<typeof import("../api/autopilot")>();
  return {
    ...actual,
    startAutopilotSession: mockedStartAutopilotSession,
    getAutopilotSession: mockedGetAutopilotSession,
  };
});

import WorkspacePage from "../pages/WorkspacePage";
import { useAutopilotSession } from "../hooks/useAutopilotSession";
import { ToastProvider } from "../hooks/useToast";
import ToastContainer from "../components/shared/ToastContainer";

// Mutable state shared between ManagedDashboard and AutopilotMiddlePanel —
// they each call useAutopilotSession() in their own subtree, so we need a
// single source of truth for `session` / `events` that both render paths
// read from. Tests can mutate `mockSession` / `mockEvents` and re-render to
// observe the change.
let mockSession: {
  state: "stopped" | "running" | "paused";
  current_task: { description: string; chapter?: number } | null;
  queue: Array<{ id: string; description: string }>;
  history: unknown[];
  config: null;
} = {
  state: "stopped",
  current_task: null,
  queue: [],
  history: [],
  config: null,
};
let mockEvents: Array<{ event: string; data: unknown; id?: number }> = [];

const startFn = vi.fn(async () => {
  mockSession = { ...mockSession, state: "running", current_task: { description: "writing ch7" } };
});
const stopFn = vi.fn(async () => {
  mockSession = { ...mockSession, state: "stopped", current_task: null };
});

function setup(initialPath: string) {
  // v2.x (Plan Task 12, 2026-08-30): WorkspacePage now hosts a tab switcher
  // and renders <WorkspaceWizardPanel> by default (when the wizard hasn't
  // been completed). Existing tests for the manuscript writing panel add
  // ?tab=manuscript to the URL to force the panel mount. We transparently
  // inject it here when the caller didn't already specify a tab so that
  // legacy paths like "/project/p1/workspace" keep landing on the
  // manuscript tab in tests.
  const path = initialPath.includes("tab=")
    ? initialPath
    : `${initialPath}${initialPath.includes("?") ? "&" : "?"}tab=manuscript`;
  const view = render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        {/* WorkspacePage reads useParams<{ projectId }> — declare the route
            with a :projectId segment so MemoryRouter populates params. The
            "path *" branch below is a fallback for non-/project paths. */}
        <Route path="/project/:projectId/workspace" element={<ToastProvider><WorkspacePage /><ToastContainer /></ToastProvider>} />
        <Route path="*" element={<ToastProvider><WorkspacePage projectId="p1" /><ToastContainer /></ToastProvider>} />
      </Routes>
    </MemoryRouter>,
  );
  return { ...view, rerender: view.rerender };
}

beforeEach(() => {
  sessionStorage.clear();
  localStorage.clear();
  mockedGetProjectStatus.mockClear();
  mockedGetStage4Progress.mockReset();
  mockedGetStage4Progress.mockResolvedValue({ chapters: [], total_chapters: 0 });
  mockedGetOutline.mockReset();
  mockedGetOutline.mockResolvedValue({ chapters: [] });
  mockedGetNovelOutline.mockReset();
  mockedGetNovelOutline.mockResolvedValue({ volumes: [] });
  mockedGenerateOutline.mockReset();
  mockedGenerateOutline.mockResolvedValue({ chapters: [] });
  mockedWriteScene.mockReset();
  mockedWriteScene.mockResolvedValue({
    scene_number: 0,
    status: "passed",
    retry_count: 0,
    draft_text: "",
    parsed_logs: [],
    fact_guard_results: { all_passed: true, checks: [], coherence_score: 0 },
    registry_updates: { created: [], updated: [], cascade_executed: [] },
    l0_snapshot: {},
    precheck_result: { precheck_passed: true, suggestions: [], tokens_used: 0, skipped_reason: "" },
  });
  mockedFactGuard.mockReset();
  mockedFactGuard.mockResolvedValue({
    all_passed: true,
    checks: [],
    coherence_score: 95,
  });
  // Reset getSceneDraft back to its default empty-draft response — the
  // draft-load regression test calls mockReset/mockResolvedValueOnce and
  // leaves the spy in a no-implementation state; without this re-mock
  // the next test's useEffect call returns undefined and crashes the
  // .then() chain.
  mockedGetSceneDraft.mockReset();
  mockedGetSceneDraft.mockResolvedValue({
    draft_text: "",
    chapter_number: 1,
    scene_number: 1,
    parsed_logs: [],
    fact_guard_results: null,
    coherence_score: 0,
  });
  mockedStartAutopilotSession.mockReset();
  mockedStartAutopilotSession.mockResolvedValue({
    state: "running", current_task: null, queue: [], history: [], config: null,
  });
  mockedGetAutopilotSession.mockReset();
  mockedGetAutopilotSession.mockResolvedValue(null);
  mockedStopAutopilotSession.mockReset();
  mockedStopAutopilotSession.mockResolvedValue({});
  mockedGetSceneDrafts.mockReset();
  mockedGetSceneDrafts.mockResolvedValue({ chapter_number: 0, scenes: [] });
  mockedRepairProgress.mockClear();
  mockedRepairProgress.mockResolvedValue({
    repaired_chapters: [],
    current_chapter: 1,
  });
  startFn.mockClear();
  stopFn.mockClear();
  mockSession = {
    state: "stopped",
    current_task: null,
    queue: [],
    history: [],
    config: null,
  };
  mockEvents = [];
  vi.mocked(useAutopilotSession).mockImplementation(() => ({
    session: mockSession,
    events: mockEvents,
    status: "idle",
    start: startFn,
    stop: stopFn,
    pause: vi.fn().mockResolvedValue(undefined),
    resume: vi.fn().mockResolvedValue(undefined),
    refresh: vi.fn().mockResolvedValue(undefined),
  }));
});

describe("Workspace integration", () => {
  // v1.9: workspace now defaults to manual mode on entry (was: managed).
  // Users explicitly opt into managed (autopilot) via the top-bar switcher.
  it("default mode renders ChapterTreePanel + WritingArea + ContextPanel (manual)", async () => {
    // Plan Task 13 (2026-08-30): ContextPanel no longer calls api.getConcept
    // (etc.) on mount, so WorkspaceWritingPanel's getOutline call now runs
    // *first* — both it and WorkspacePage.preflight need populated chapters,
    // so use mockResolvedValue (persistent) instead of once.
    mockedGetOutline.mockResolvedValue({
      chapters: [{ chapter_number: 1, title: "第一章", scene_plan: [{ scene_number: 1 }] }],
    });
    setup("/project/p1/workspace?chapter=1&scene=1-1");
    expect(screen.getByTestId("workspace-layout").getAttribute("data-mode")).toBe("manual");
    expect(await screen.findByTestId("chapter-tree")).toBeInTheDocument();
    expect(await screen.findByTestId("writing-area")).toBeInTheDocument();
    expect(screen.getByTestId("context-panel")).toBeInTheDocument();
  });

  // Bug fix: bookshelf navigation goes to /workspace without ?chapter=/
  // ?scene= params. WorkspacePage should default to chapter 1 + scene 1-1
  // after getOutline resolves, instead of rendering the empty state and
  // forcing the user to click into the chapter tree.
  it("no URL params: defaults to chapter 1 + scene 1-1 after outline loads", async () => {
    mockedGetOutline.mockResolvedValue({
      chapters: [{ chapter_number: 1, title: "第一章", scene_plan: [{ scene_number: 1 }] }],
    });
    setup("/project/p1/workspace");
    expect(await screen.findByTestId("writing-area")).toBeInTheDocument();
    expect(screen.queryByTestId("writing-empty")).not.toBeInTheDocument();
    expect(screen.getByTestId("writing-chapter-title")).toHaveTextContent("第一章");
  });

  // v1.9.1: managed-mode left column (ManagedDashboard) no longer renders
  // its own autopilot start/stop toggle — the cockpit tab in the middle
  // AutopilotMiddlePanel owns all session controls. Session still defaults
  // to "stopped" so the user must opt in via the cockpit's 启动托管 button.
  it("managed mode defaults to autopilot stopped (cockpit has the 启动托管 button)", () => {
    setup("/project/p1/workspace?mode=managed");
    expect(screen.getByTestId("autopilot-cockpit-state")).toBeInTheDocument();
    expect(screen.queryByTestId("status-strip")).not.toBeInTheDocument();
    expect(screen.queryByTestId("autopilot-toggle")).not.toBeInTheDocument();
  });

  // v1.9 alignment with plotPilot: managed mode centers on an
  // AutopilotWorkspace (cockpit / dashboard / log) rather than just a chapter
  // grid + side panels. The center slot had been left empty, so the user
  // saw only the left chapter grid + right AI-control panel — no progress
  // / queue / event stream surface in the middle.
  it("managed mode renders the autopilot middle panel with cockpit/dashboard/log tabs", async () => {
    setup("/project/p1/workspace?mode=managed");
    expect(await screen.findByTestId("autopilot-middle-panel")).toBeInTheDocument();
    expect(screen.getByTestId("autopilot-tab-cockpit")).toBeInTheDocument();
    expect(screen.getByTestId("autopilot-tab-dashboard")).toBeInTheDocument();
    expect(screen.getByTestId("autopilot-tab-log")).toBeInTheDocument();
    // Cockpit is the default landing tab and shows the session state card.
    expect(screen.getByTestId("autopilot-cockpit-state")).toBeInTheDocument();
  });

  it("clicking cockpit 启动托管 button starts the autopilot session", async () => {
    const { rerender } = setup("/project/p1/workspace?mode=managed");
    // v1.9.1: left-column toggle was removed; the cockpit's start button is
    // the only entry point to start the session in managed mode.
    fireEvent.click(screen.getByTestId("autopilot-cockpit-start"));
    // start() is async; the mock updates mockSession synchronously but React
    // needs a render cycle to pick it up. Re-render the tree to mirror the
    // post-promise-resolve snapshot the real hook would have produced.
    await new Promise((r) => setTimeout(r, 50));
    rerender(
      <MemoryRouter initialEntries={["/project/p1/workspace?tab=manuscript"]}>
        <Routes>
          <Route path="/project/:projectId/workspace" element={<ToastProvider><WorkspacePage /></ToastProvider>} />
        </Routes>
      </MemoryRouter>,
    );
    expect(startFn).toHaveBeenCalled();
    // Once running, the cockpit surfaces pause/stop (start is hidden).
    expect(screen.queryByTestId("autopilot-cockpit-start")).not.toBeInTheDocument();
    expect(screen.getByTestId("autopilot-cockpit-pause")).toBeInTheDocument();
    expect(screen.getByTestId("autopilot-cockpit-stop")).toBeInTheDocument();
  });

  it("?mode=manual renders ChapterTreePanel + WritingArea + ContextPanel", async () => {
    mockedGetOutline.mockResolvedValue({
      chapters: [
        {
          chapter_number: 1,
          title: "第一章",
          scene_plan: [{ scene_number: 1 }],
        },
      ],
    });
    setup("/project/p1/workspace?mode=manual&chapter=1&scene=1-1");
    expect(screen.getByTestId("workspace-layout").getAttribute("data-mode")).toBe("manual");
    expect(screen.getByTestId("chapter-tree")).toBeInTheDocument();
    // Wait for getOutline to resolve + URL parse useEffect to pick chapter 1
    expect(await screen.findByTestId("writing-area")).toBeInTheDocument();
    expect(screen.getByTestId("context-panel")).toBeInTheDocument();
  });

  // Regression: the manual-mode "重新生成" button used to be a no-op
  // (just toggled busy). It must call /stage4/write-scene and update the
  // editor body with the returned draft_text.
  it("manual-mode 重新生成 button calls writeScene and updates editor content", async () => {
    mockedWriteScene.mockResolvedValueOnce({
      scene_number: 1,
      status: "passed",
      retry_count: 0,
      draft_text: "新生成的场景正文。",
      parsed_logs: [],
      fact_guard_results: { all_passed: true, checks: [], coherence_score: 95 },
      registry_updates: { created: [], updated: [], cascade_executed: [] },
      l0_snapshot: {},
      precheck_result: { precheck_passed: true, suggestions: [], tokens_used: 0, skipped_reason: "" },
    });

    mockedGetOutline.mockResolvedValue({
      chapters: [
        { chapter_number: 1, title: "第一章", scene_plan: [{ scene_number: 1 }] },
      ],
    });
    setup("/project/p1/workspace?mode=manual&chapter=1&scene=1-1");
    const regen = await screen.findByTestId("editor-regenerate");
    fireEvent.click(regen);

    // v1.9: 重新生成 now opens a RegenerateModal; writeScene only fires
    // after the user confirms. Confirm with empty user_modifications
    // (the "留空 = 仅重新生成" path).
    await screen.findByTestId("regenerate-modal");
    fireEvent.click(screen.getByTestId("regenerate-modal-confirm"));

    await waitFor(() => {
      expect(mockedWriteScene).toHaveBeenCalledWith({
        project_id: "p1",
        chapter_number: 1,
        scene_number: 1,
        // v1.9: empty string = "regenerate without guidance" — backend
        // appends no 【用户修改意见】 block, behavior identical to v1.8.
        user_modifications: "",
      });
    });
    const body = (await screen.findByTestId("editor-body")) as HTMLTextAreaElement;
    expect(body.value).toBe("新生成的场景正文。");
  });

  // Bug fix — "保存草稿" must call updateSceneDraft (writes draft.md), NOT
  // updateOutline (which corrupts outline.json by setting title: "" and
  // injecting schema-invalid `content` field). See client.ts:870 for the
  // correct endpoint.
  it("'保存草稿' button calls updateSceneDraft with the current editor content", async () => {
    const { default: api } = await import("../api/client");
    const updateSceneDraftSpy = api.updateSceneDraft as ReturnType<typeof vi.fn>;
    updateSceneDraftSpy.mockReset();
    updateSceneDraftSpy.mockResolvedValue({ chapter_number: 1, scene_number: 1 });

    mockedGetOutline.mockResolvedValue({
      chapters: [
        { chapter_number: 1, title: "第一章", scene_plan: [{ scene_number: 1 }] },
      ],
    });
    setup("/project/p1/workspace?mode=manual&chapter=1&scene=1-1");
    // Wait for editor to mount, then type some content.
    const body = (await screen.findByTestId("editor-body")) as HTMLTextAreaElement;
    fireEvent.change(body, { target: { value: "用户写的正文" } });
    fireEvent.click(screen.getByTestId("editor-save"));
    await waitFor(() => expect(updateSceneDraftSpy).toHaveBeenCalled());
    // Must use the scene-draft endpoint, not updateOutline (which would
    // corrupt outline.json).
    expect(updateSceneDraftSpy).toHaveBeenCalledWith({
      project_id: "p1",
      chapter_number: 1,
      scene_number: 1,
      draft_text: "用户写的正文",
    });
  });

  // Bug fix — "Fact Guard" must run a read-only check on the current draft,
  // not regenerate the scene via /write-scene (which would overwrite the
  // user's hand-written text). See api.factGuard in client.ts.
  it("'Fact Guard' button calls factGuard (not writeScene) and does NOT overwrite the editor", async () => {
    const { default: api } = await import("../api/client");
    const writeSceneSpy = api.writeScene as ReturnType<typeof vi.fn>;
    writeSceneSpy.mockClear();

    mockedFactGuard.mockResolvedValueOnce({
      all_passed: false,
      checks: [{ name: "timeline", passed: false, detail: "时间线冲突" }],
      coherence_score: 60,
    });

    mockedGetOutline.mockResolvedValue({
      chapters: [
        { chapter_number: 1, title: "第一章", scene_plan: [{ scene_number: 1 }] },
      ],
    });
    setup("/project/p1/workspace?mode=manual&chapter=1&scene=1-1");
    const body = (await screen.findByTestId("editor-body")) as HTMLTextAreaElement;
    // Type user content; clicking Fact Guard must NOT replace it.
    fireEvent.change(body, { target: { value: "用户手写的稿子" } });
    fireEvent.click(screen.getByTestId("editor-fact-guard"));

    await waitFor(() => expect(mockedFactGuard).toHaveBeenCalled());
    expect(mockedFactGuard).toHaveBeenCalledWith({
      project_id: "p1",
      chapter_number: 1,
      scene_number: 1,
      draft_text: "用户手写的稿子",
    });
    // Critical: writeScene is NOT called for Fact Guard (would overwrite).
    expect(writeSceneSpy).not.toHaveBeenCalled();
    // Editor body must be unchanged after Fact Guard.
    expect((screen.getByTestId("editor-body") as HTMLTextAreaElement).value).toBe("用户手写的稿子");
  });

  // Bug fix — selecting a chapter/scene must load the saved draft from
  // disk (ch{NN}_scene_{NNN}_draft.md) so the user sees their previously
  // written prose instead of an empty editor.
  it("selecting a chapter loads its saved draft into the editor", async () => {
    const { default: api } = await import("../api/client");
    const getSceneDraftSpy = api.getSceneDraft as ReturnType<typeof vi.fn>;
    getSceneDraftSpy.mockReset();
    getSceneDraftSpy.mockResolvedValueOnce({
      draft_text: "之前保存的草稿",
      chapter_number: 1,
      scene_number: 1,
      parsed_logs: [],
      fact_guard_results: null,
      coherence_score: 0,
    });

    mockedGetOutline.mockResolvedValue({
      chapters: [
        {
          chapter_number: 1,
          title: "第一章",
          scene_plan: [
            { scene_number: 1 },
            { scene_number: 2 },
          ],
        },
      ],
    });
    setup("/project/p1/workspace?mode=manual&chapter=1&scene=1-1");
    // Wait for the draft load useEffect to run.
    await waitFor(() => expect(getSceneDraftSpy).toHaveBeenCalledWith("p1", 1, 1));
    const body = await screen.findByTestId("editor-body");
    expect((body as HTMLTextAreaElement).value).toBe("之前保存的草稿");
  });

  it("clicking mode-manual in the top-bar opens the confirm modal", () => {
    setup("/project/p1/workspace?mode=managed");
    fireEvent.click(screen.getByTestId("mode-manual"));
    expect(screen.getByTestId("mode-switch-confirm")).toBeInTheDocument();
  });

  it("clicking mode-managed in the top-bar opens the start modal", async () => {
    setup("/project/p1/workspace?mode=manual");
    fireEvent.click(screen.getByTestId("mode-managed"));
    // The start modal fetches its config via api.getAutopilotSession; wait
    // for the async load to flip `loaded` to true so the modal renders.
    await waitFor(() => expect(screen.getByTestId("managed-start-modal")).toBeInTheDocument());
  });

  // Regression: ManagedStartModal requires `projectId` and calls
  // `onStarted` after submit. WorkspacePage had been rendering it without
  // `projectId` (passed undefined → backend "项目 undefined 不存在") and with
  // an `onStart` prop the modal never reads, so the start flow silently
  // toast-failed and never transitioned out of the modal / into managed mode.
  it("'启动托管' submits with the actual projectId and switches to managed mode", async () => {
    setup("/project/p1/workspace?mode=manual");
    fireEvent.click(screen.getByTestId("mode-managed"));
    await waitFor(() => expect(screen.getByTestId("managed-start-modal")).toBeInTheDocument());
    fireEvent.click(screen.getByTestId("start-submit"));
    await waitFor(() => expect(mockedStartAutopilotSession).toHaveBeenCalled());
    // projectId must be "p1" (the URL param), not undefined / empty.
    expect(mockedStartAutopilotSession).toHaveBeenCalledWith("p1", expect.anything());
    // After submit succeeds, modal closes and mode flips to managed.
    await waitFor(() => expect(screen.queryByTestId("managed-start-modal")).not.toBeInTheDocument());
    expect(screen.getByTestId("workspace-layout").getAttribute("data-mode")).toBe("managed");
  });

  // Layer 3 (v1.9) — when the user confirms a managed→manual switch, the
  // backend's POST /session/stop must be called so session.json leaves
  // state="running" / current_task set. Without this call the manual-mode
  // topbar reads stale session.current_task and displays it. Fire-and-forget
  // is acceptable — we assert the call was made, not its result.
  it("managed→manual switch calls api.stopAutopilotSession(projectId)", async () => {
    setup("/project/p1/workspace?mode=managed");
    fireEvent.click(screen.getByTestId("mode-manual"));
    expect(screen.getByTestId("mode-switch-confirm")).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("confirm-confirm"));
    // Stop is fire-and-forget; give the microtask queue a tick.
    await waitFor(() => expect(mockedStopAutopilotSession).toHaveBeenCalledWith("p1"));
  });

  // Counterpart to the test above — manual→managed switch opens the start
  // modal, not the confirm modal, and must NOT touch the running session.
  // Otherwise the user couldn't re-enter managed mode with the existing
  // session still alive.
  it("manual→managed switch does NOT call api.stopAutopilotSession", async () => {
    setup("/project/p1/workspace?mode=manual");
    fireEvent.click(screen.getByTestId("mode-managed"));
    // The start modal opens; confirm it (no stop should have fired yet).
    await waitFor(() => expect(screen.getByTestId("managed-start-modal")).toBeInTheDocument());
    expect(mockedStopAutopilotSession).not.toHaveBeenCalled();
  });

  it("clicking a 'writing' chapter row opens the take-over confirm modal (not direct switch)", async () => {
    // v1.9 T4: managed mode now renders the same <ChapterTreePanel /> as
    // manual mode (chapter rows use testid `chapter-N`, not `chapter-cell-N`).
    // Both WorkspacePage and WorkspaceTopBar call getStage4Progress on
    // mount, so we need TWO once-only overrides — one per caller.
    const chapter4 = [{ chapter_number: 4, status: "in_progress" }];
    mockedGetStage4Progress.mockResolvedValueOnce({ chapters: chapter4, total_chapters: 7 });
    mockedGetStage4Progress.mockResolvedValueOnce({ chapters: chapter4, total_chapters: 7 });
    // The unified ChapterTreePanel renders chapters from manualChapters
    // (outline.json), not from progress.json. So chapter 4 must be in the
    // outline too, otherwise the row won't render.
    mockedGetOutline.mockResolvedValue({
      chapters: [{ chapter_number: 4, title: "第四章", scene_plan: [{ scene_number: 1 }] }],
    });
    setup("/project/p1/workspace?mode=managed");
    // The useEffect fetch is async; wait for chapter-4 to render.
    expect(await screen.findByTestId("chapter-4")).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("chapter-4"));
    expect(screen.getByTestId("mode-switch-confirm")).toBeInTheDocument();
    expect(localStorage.getItem("nebula.workspace.mode")).toBeNull(); // still on managed
  });

  it("'立即接管' on take-over modal switches to manual + loads that chapter's first scene", async () => {
    const chapter4 = [{ chapter_number: 4, status: "in_progress" }];
    mockedGetStage4Progress.mockResolvedValueOnce({ chapters: chapter4, total_chapters: 7 });
    mockedGetStage4Progress.mockResolvedValueOnce({ chapters: chapter4, total_chapters: 7 });
    // Take-over drills down into the chapter tree (manual mode) — chapter 4
    // must be in the outline so the unified tree shows it.
    mockedGetOutline.mockResolvedValue({
      chapters: [
        { chapter_number: 4, title: "第四章", scene_plan: [{ scene_number: 1 }] },
      ],
    });
    setup("/project/p1/workspace?mode=managed");
    expect(await screen.findByTestId("chapter-4")).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("chapter-4"));
    // uncheck "等待完成" so we take over immediately
    const waitCheckbox = screen.getByTestId("confirm-wait-finish") as HTMLInputElement;
    fireEvent.change(waitCheckbox, { target: { checked: false } });
    fireEvent.click(screen.getByTestId("confirm-confirm"));
    await waitFor(() =>
      expect(screen.getByTestId("workspace-layout").getAttribute("data-mode")).toBe("manual"),
    );
    expect(screen.getByTestId("writing-area")).toBeInTheDocument();
  });

  // Stage 2 Task 2.11 — full SSE event sequence through the autopilot
  // center panel. We mock useAutopilotSession directly (not the underlying
  // EventSource) and re-mock its return value before each sub-step, then
  // re-render. Using rerender() (not multiple setup() calls) avoids
  // duplicate-element errors and mirrors a real "state-update" feel.
  it("EventSource sequence updates autopilot center panel", () => {
    // Step 1: managed mode renders cleanly with no events yet.
    const { rerender } = setup("/project/p1/workspace?mode=managed");
    expect(screen.getByTestId("autopilot-middle-panel")).toBeInTheDocument();
    expect(screen.queryByTestId("status-strip")).not.toBeInTheDocument();

    // Step 2: switch session to running with a current task — the cockpit
    // surfaces the current task + live state. v1.9.1: no left-column status
    // strip anymore (that surface was removed when the cockpit absorbed it).
    mockSession = { ...mockSession, state: "running", current_task: { description: "writing ch7" } };
    rerender(
      <MemoryRouter initialEntries={["/project/p1/workspace?tab=manuscript"]}>
        <Routes>
          <Route path="/project/:projectId/workspace" element={<ToastProvider><WorkspacePage /></ToastProvider>} />
        </Routes>
      </MemoryRouter>,
    );
    expect(screen.getByTestId("autopilot-cockpit-state")).toBeInTheDocument();
    expect(screen.getByTestId("autopilot-cockpit-current-task")).toBeInTheDocument();
    expect(screen.getByTestId("autopilot-cockpit-current-task").textContent).toContain("writing ch7");

    // Step 3: feed a partial event sequence. v1.9.1 moved the event feed
    // out of the cockpit (which now focuses on the live writing stream)
    // and into the 监控日志 tab; verify it's no longer in the cockpit, then
    // switch tabs to see them in the log.
    mockEvents = [
      { event: "task_start", data: { description: "writing ch7" }, id: 1 },
      { event: "circuit_open", data: { reason: "guard" }, id: 2 },
      { event: "queue_add", data: { id: "q1", description: "review" }, id: 3 },
      { event: "task_complete", data: { chapter: 7 }, id: 4 },
      { event: "circuit_close", data: {}, id: 5 },
    ];
    rerender(
      <MemoryRouter initialEntries={["/project/p1/workspace?tab=manuscript"]}>
        <Routes>
          <Route path="/project/:projectId/workspace" element={<ToastProvider><WorkspacePage /></ToastProvider>} />
        </Routes>
      </MemoryRouter>,
    );
    expect(screen.queryByTestId("autopilot-cockpit-events")).not.toBeInTheDocument();
    fireEvent.click(screen.getByTestId("autopilot-tab-log"));
    const logRows = screen.getAllByTestId(/^autopilot-log-row-/);
    expect(logRows.length).toBeGreaterThanOrEqual(3);

    // Step 4: switch to dashboard tab — queue and event stats appear.
    mockSession = { ...mockSession, queue: [{ id: "q1", description: "review" }] };
    rerender(
      <MemoryRouter initialEntries={["/project/p1/workspace?tab=manuscript"]}>
        <Routes>
          <Route path="/project/:projectId/workspace" element={<ToastProvider><WorkspacePage /></ToastProvider>} />
        </Routes>
      </MemoryRouter>,
    );
    fireEvent.click(screen.getByTestId("autopilot-tab-dashboard"));
    expect(screen.getByTestId("autopilot-dashboard-queue")).toBeInTheDocument();
    expect(screen.getByTestId("autopilot-queue-row-q1")).toBeInTheDocument();

    // Step 5: task_fail event → log tab filter surfaces the failure.
    mockEvents = [
      ...mockEvents,
      { event: "task_fail", data: { reason: "x" }, id: 6 },
    ];
    rerender(
      <MemoryRouter initialEntries={["/project/p1/workspace?tab=manuscript"]}>
        <Routes>
          <Route path="/project/:projectId/workspace" element={<ToastProvider><WorkspacePage /></ToastProvider>} />
        </Routes>
      </MemoryRouter>,
    );
    fireEvent.click(screen.getByTestId("autopilot-tab-log"));
    fireEvent.click(screen.getByTestId("autopilot-log-filter-task_fail"));
    const failRows = screen.getAllByTestId(/^autopilot-log-row-/);
    expect(failRows.length).toBeGreaterThanOrEqual(1);
  });

  it("cockpit renders a rollback button (disabled, v1.9.1 placeholder)", () => {
    // Rollback only renders mid-flight, so start the session in running state.
    const { rerender } = setup("/project/p1/workspace?mode=managed");
    mockSession = { ...mockSession, state: "running", current_task: { description: "writing ch7" } };
    rerender(
      <MemoryRouter initialEntries={["/project/p1/workspace?mode=managed"]}>
        <Routes>
          <Route path="/project/:projectId/workspace" element={<ToastProvider><WorkspacePage /><ToastContainer /></ToastProvider>} />
        </Routes>
      </MemoryRouter>,
    );
    expect(screen.getByTestId("autopilot-cockpit-rollback")).toBeInTheDocument();
    const rollback = screen.getByTestId("autopilot-cockpit-rollback");
    expect(rollback).toBeDisabled();
    expect(rollback.title).toContain("v1.9.1");
  });

  it("new project with empty progress.json renders no chapter rows in managed mode", async () => {
    // Default mock returns { chapters: [] } and default mock returns empty
    // outline — no chapter-* rows should render. v1.9 T4: managed mode now
    // uses the unified ChapterTreePanel (testid `chapter-N`), not the
    // legacy `chapter-cell-N` grid.
    setup("/project/p1/workspace?mode=managed");
    // useEffect runs after mount; wait for it to settle.
    await waitFor(() => expect(mockedGetStage4Progress).toHaveBeenCalledWith("p1"));
    expect(screen.queryByTestId("chapter-1")).not.toBeInTheDocument();
    expect(screen.queryByTestId("chapter-2")).not.toBeInTheDocument();
  });

  it("TopBar progress ring shows the novel_outline-derived total (0 / 150) for new project", async () => {
    // Pre-step: backend now returns total_chapters=150 from novel_outline.json
    // even when progress.json is missing. The TopBar reads that field and
    // renders "0 / 150" — verifying the planned total reaches the user
    // instead of "0 / 1" (outline.json) or "0 / 0" (no fallback).
    // Both WorkspacePage and WorkspaceTopBar call getStage4Progress on mount.
    mockedGetStage4Progress.mockResolvedValue({ chapters: [], total_chapters: 150 });
    setup("/project/p1/workspace");
    // WorkspaceTopBar renders a progress element with the "X / Y" text.
    expect(await screen.findByTestId("topbar-progress")).toHaveTextContent("0 / 150");
  });

  it("manual mode renders chapter tree from getOutline (1 chapter, 4 scenes)", async () => {
    mockedGetOutline.mockResolvedValue({
      chapters: [
        {
          chapter_number: 1,
          title: "第一章 瞳开幽闭",
          scene_plan: [
            { scene_number: 1 },
            { scene_number: 2 },
            { scene_number: 3 },
            { scene_number: 4 },
          ],
        },
      ],
    });
    setup("/project/p1/workspace?mode=manual");
    // ChapterTreePanel.tsx:81 already exposes data-testid={`chapter-${ch.chapter_number}`}
    // and line 96 exposes data-testid={`scene-${s.scene_id}`}. Our `scene_id`
    // derivation is `${chapter_number}-${scene_number}`, so the 4 scenes
    // for chapter 1 get test ids "scene-1-1" .. "scene-1-4".
    expect(await screen.findByTestId("chapter-1")).toBeInTheDocument();
    expect(screen.getByTestId("scene-1-1")).toBeInTheDocument();
    expect(screen.getByTestId("scene-1-2")).toBeInTheDocument();
    expect(screen.getByTestId("scene-1-3")).toBeInTheDocument();
    expect(screen.getByTestId("scene-1-4")).toBeInTheDocument();
  });

  it("refresh that fails leaves an empty chapter list (no stale data)", async () => {
    // First two calls (WorkspacePage mount + WorkspaceTopBar mount) succeed
    // with 2 chapters. Third call (WorkspacePage after refresh click) rejects.
    // The UI must show no chapter rows after the refresh, not the pre-
    // refresh data — that's the "we don't know what's on disk" truthful state.
    // v1.9 T4: managed mode now uses the unified ChapterTreePanel, so the
    // outline must also contain the chapters for the rows to render.
    mockedGetStage4Progress
      .mockResolvedValueOnce({
        chapters: [
          { chapter_number: 1, status: "completed" },
          { chapter_number: 2, status: "in_progress" },
        ],
        total_chapters: 7,
      })
      .mockResolvedValueOnce({
        chapters: [
          { chapter_number: 1, status: "completed" },
          { chapter_number: 2, status: "in_progress" },
        ],
        total_chapters: 7,
      })
      .mockRejectedValueOnce(new Error("network down"));
    mockedGetOutline.mockResolvedValue({
      chapters: [
        { chapter_number: 1, title: "第一章", scene_plan: [{ scene_number: 1 }] },
        { chapter_number: 2, title: "第二章", scene_plan: [{ scene_number: 1 }] },
      ],
    });
    setup("/project/p1/workspace?mode=managed");
    // First load renders the 2 rows.
    expect(await screen.findByTestId("chapter-1")).toBeInTheDocument();
    expect(screen.getByTestId("chapter-2")).toBeInTheDocument();
    // Click the workspace "刷新" button.
    fireEvent.click(screen.getByTestId("refresh"));
    // After the failed refresh the rows are gone — stale data cleared.
    await waitFor(() => {
      expect(screen.queryByTestId("chapter-1")).not.toBeInTheDocument();
    });
    expect(screen.queryByTestId("chapter-2")).not.toBeInTheDocument();
  });

  // Bug 1 fix regression — clicking "+ 新章节" on the manual-mode tree
  // toolbar must open the AddChaptersModal (previously a no-op).
  it("'manual mode' + 新章节 opens AddChaptersModal", async () => {
    mockedGetOutline.mockResolvedValue({
      chapters: [
        {
          chapter_number: 1,
          title: "现有第一章",
          scene_plan: [{ scene_number: 1 }],
        },
      ],
    });
    mockedGetNovelOutline.mockResolvedValue({
      volumes: [{ name: "v1", chapter_range: "1-30", summary: "", key_events: [] }],
      mc_growth_arc: [],
      key_plot_points: [],
      generated_at: "",
      updated_at: "",
    });
    setup("/project/p1/workspace?mode=manual");
    expect(await screen.findByTestId("chapter-1")).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("add-chapter"));
    expect(await screen.findByTestId("add-chapters-modal")).toBeInTheDocument();
    // currentMax=1, plannedTotal=30 → start=2, maxEnd=30, default end=11
    expect(screen.getByTestId("add-chapters-start-display")).toHaveTextContent("2");
    const input = screen.getByTestId("add-chapters-end-input") as HTMLInputElement;
    expect(input.max).toBe("30");
    expect(input.min).toBe("2");
  });

  // Bug 1 fix regression — confirming AddChaptersModal calls
  // generateOutline sequentially for new chapter_numbers and reloads.
  it("AddChaptersModal confirm triggers api.generateOutline for chapters in [start..end]", async () => {
    const { default: api } = await import("../api/client");
    const generateSpy = api.generateOutline as ReturnType<typeof vi.fn>;
    generateSpy.mockReset();
    generateSpy.mockResolvedValue({ chapters: [] });
    mockedGetOutline.mockResolvedValue({
      chapters: [
        {
          chapter_number: 1,
          title: "现有第一章",
          scene_plan: [{ scene_number: 1 }],
        },
      ],
    });
    mockedGetNovelOutline.mockResolvedValue({
      volumes: [{ name: "v1", chapter_range: "1-10", summary: "", key_events: [] }],
      mc_growth_arc: [],
      key_plot_points: [],
      generated_at: "",
      updated_at: "",
    });
    setup("/project/p1/workspace?mode=manual");
    expect(await screen.findByTestId("chapter-1")).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("add-chapter"));
    // currentMax=1 → start=2. Set end=3 → adds chapters 2, 3.
    const input = await screen.findByTestId("add-chapters-end-input");
    fireEvent.change(input, { target: { value: "3" } });
    fireEvent.click(screen.getByTestId("add-chapters-confirm"));
    await waitFor(() => expect(generateSpy).toHaveBeenCalledTimes(2));
    expect(generateSpy).toHaveBeenNthCalledWith(1, "p1", 2);
    expect(generateSpy).toHaveBeenNthCalledWith(2, "p1", 3);
  });

  // Bug 2 fix — clicking a manual-mode chapter with a real outline.json now
  // surfaces the chapter's `theme` in the writing-area header (replacing
  // the old "(占位)" placeholder). The theme renders as a labeled row
  // `writing-chapter-theme` inside `writing-outline-block` for v1.8.
  it("manual-mode writing area renders chapter theme above the editor (not 占位)", async () => {
    mockedGetOutline.mockResolvedValue({
      chapters: [
        {
          chapter_number: 1,
          title: "第一章",
          theme: "主角踏上寻找身世的旅途",
          scene_plan: [{ scene_number: 1, goal: "主角启程遇到师父" }],
        },
      ],
    });
    setup("/project/p1/workspace?mode=manual&chapter=1&scene=1-1");
    const theme = await screen.findByTestId("writing-chapter-theme");
    expect(theme.textContent).toContain("主角踏上寻找身世的旅途");
    expect(screen.getByTestId("writing-outline-block").textContent).not.toContain("占位");
  });

  // Bug 2 fallback — older outline.json files written before the `theme`
  // field existed should still surface the selected scene's goal so the
  // header isn't silently empty.
  it("manual-mode writing area falls back to scene goal when chapter theme is missing", async () => {
    mockedGetOutline.mockResolvedValue({
      chapters: [
        {
          chapter_number: 1,
          title: "第一章",
          // no theme on purpose — simulates pre-v1.8 outline.json
          scene_plan: [{ scene_number: 1, goal: "主角启程遇到师父" }],
        },
      ],
    });
    setup("/project/p1/workspace?mode=manual&chapter=1&scene=1-1");
    const goal = await screen.findByTestId("writing-scene-goal");
    expect(goal.textContent).toContain("主角启程遇到师父");
  });

  // Issue 3 (v1.9) — left chapter list groups chapters by volume using
  // novel_outline.volumes[].chapter_range strings. Each volume renders as a
  // header with its name + range.
  it("manual-mode chapter tree groups chapters by novel_outline volumes", async () => {
    mockedGetOutline.mockResolvedValue({
      chapters: [
        { chapter_number: 1, title: "卷一第一章", scene_plan: [{ scene_number: 1 }] },
        { chapter_number: 5, title: "卷一第五章", scene_plan: [{ scene_number: 1 }] },
        { chapter_number: 35, title: "卷二第一章", scene_plan: [{ scene_number: 1 }] },
      ],
    });
    mockedGetNovelOutline.mockResolvedValue({
      volumes: [
        { name: "第一卷", chapter_range: "1-30", summary: "初入江湖", key_events: [] },
        { name: "第二卷", chapter_range: "31-60", summary: "卷入纷争", key_events: [] },
      ],
      mc_growth_arc: [],
      key_plot_points: [],
      generated_at: "",
      updated_at: "",
    });
    setup("/project/p1/workspace?mode=manual&chapter=1&scene=1-1");
    // Wait for the tree + the volume-aware grouping to render.
    expect(await screen.findByTestId("volume-第一卷-header")).toBeInTheDocument();
    expect(screen.getByTestId("volume-第二卷-header")).toBeInTheDocument();
    // Volume headers carry the chapter range.
    expect(screen.getByTestId("volume-第一卷-header").textContent).toContain("1-30");
    expect(screen.getByTestId("volume-第二卷-header").textContent).toContain("31-60");
    // Volume's detailed outline (summary) is intentionally hidden — only the
    // name + range are surfaced; the writer consults the prose outline elsewhere.
    expect(screen.queryByTestId("volume-第一卷-summary")).not.toBeInTheDocument();
    expect(screen.queryByTestId("volume-第二卷-summary")).not.toBeInTheDocument();
  });

  it("manual-mode chapter tree falls back to '未分组' when no novel_outline exists", async () => {
    // Default mockedGetNovelOutline already returns { volumes: [] }.
    mockedGetOutline.mockResolvedValue({
      chapters: [
        { chapter_number: 1, title: "第一章", scene_plan: [{ scene_number: 1 }] },
      ],
    });
    setup("/project/p1/workspace?mode=manual&chapter=1&scene=1-1");
    expect(await screen.findByTestId("volume-未分组-header")).toBeInTheDocument();
    // chapter-1 still renders inside the ungrouped bucket.
    expect(screen.getByTestId("chapter-1")).toBeInTheDocument();
  });

  // v1.9 — "重新生成" with unsaved edits still gates the API call on
  // user confirmation, but the gate is now the RegenerateModal (not a
  // bare confirm-dialog). Without the modal gate, a power user clicking
  // 重新生成 after typing for 10 minutes would silently lose their work.
  it("重新生成 with unsaved changes opens the RegenerateModal (writeScene is NOT yet called)", async () => {
    mockedGetOutline.mockResolvedValue({
      chapters: [
        { chapter_number: 1, title: "第一章", scene_plan: [{ scene_number: 1 }] },
      ],
    });
    setup("/project/p1/workspace?mode=manual&chapter=1&scene=1-1");
    const { default: api } = await import("../api/client");
    const writeSceneSpy = api.writeScene as ReturnType<typeof vi.fn>;
    writeSceneSpy.mockClear();
    const body = (await screen.findByTestId("editor-body")) as HTMLTextAreaElement;
    fireEvent.change(body, { target: { value: "未保存的改动" } });
    fireEvent.click(screen.getByTestId("editor-regenerate"));
    // RegenerateModal appears; writeScene is NOT called yet.
    expect(await screen.findByTestId("regenerate-modal")).toBeInTheDocument();
    expect(writeSceneSpy).not.toHaveBeenCalled();
  });

  // v1.9 — even when content matches what's on disk, the RegenerateModal
  // still opens (it doubles as the input collector). writeScene only fires
  // after the user clicks the modal's confirm button.
  it("重新生成 with no edits still opens RegenerateModal before calling writeScene", async () => {
    const { default: api } = await import("../api/client");
    const getSceneDraftSpy = api.getSceneDraft as ReturnType<typeof vi.fn>;
    getSceneDraftSpy.mockReset();
    getSceneDraftSpy.mockResolvedValueOnce({
      draft_text: "磁盘上已有的内容",
      chapter_number: 1,
      scene_number: 1,
      parsed_logs: [],
      fact_guard_results: null,
      coherence_score: 0,
    });
    const writeSceneSpy = api.writeScene as ReturnType<typeof vi.fn>;
    writeSceneSpy.mockClear();

    mockedGetOutline.mockResolvedValue({
      chapters: [
        { chapter_number: 1, title: "第一章", scene_plan: [{ scene_number: 1 }] },
      ],
    });
    setup("/project/p1/workspace?mode=manual&chapter=1&scene=1-1");
    // Wait for the draft load to populate the editor.
    const body = (await screen.findByTestId("editor-body")) as HTMLTextAreaElement;
    await waitFor(() => expect(body.value).toBe("磁盘上已有的内容"));
    // v1.9: clicking regenerate opens the modal — writeScene is NOT yet
    // called (even when content matches lastSavedContent, the modal still
    // gates the API call).
    fireEvent.click(screen.getByTestId("editor-regenerate"));
    expect(screen.getByTestId("regenerate-modal")).toBeInTheDocument();
    expect(writeSceneSpy).not.toHaveBeenCalled();
    fireEvent.click(screen.getByTestId("regenerate-modal-confirm"));
    await waitFor(() => expect(writeSceneSpy).toHaveBeenCalled());
  });

  // v1.9 — cancelling the RegenerateModal must NOT call writeScene, and
  // the editor content must be preserved exactly as the user left it.
  it("cancelling the regenerate modal does NOT call writeScene", async () => {
    mockedGetOutline.mockResolvedValue({
      chapters: [
        { chapter_number: 1, title: "第一章", scene_plan: [{ scene_number: 1 }] },
      ],
    });
    setup("/project/p1/workspace?mode=manual&chapter=1&scene=1-1");
    const { default: api } = await import("../api/client");
    const writeSceneSpy = api.writeScene as ReturnType<typeof vi.fn>;
    writeSceneSpy.mockClear();
    const body = (await screen.findByTestId("editor-body")) as HTMLTextAreaElement;
    fireEvent.change(body, { target: { value: "未保存的改动" } });
    fireEvent.click(screen.getByTestId("editor-regenerate"));
    // Click the modal's cancel button.
    await screen.findByTestId("regenerate-modal");
    fireEvent.click(screen.getByTestId("regenerate-modal-cancel"));
    expect(screen.queryByTestId("regenerate-modal")).not.toBeInTheDocument();
    expect(writeSceneSpy).not.toHaveBeenCalled();
    // Editor content is preserved.
    expect((screen.getByTestId("editor-body") as HTMLTextAreaElement).value).toBe("未保存的改动");
  });

  it("'保存草稿' shows a success toast on save", async () => {
    const { default: api } = await import("../api/client");
    const updateSceneDraftSpy = api.updateSceneDraft as ReturnType<typeof vi.fn>;
    updateSceneDraftSpy.mockReset();
    updateSceneDraftSpy.mockResolvedValue({ chapter_number: 1, scene_number: 1 });

    mockedGetOutline.mockResolvedValue({
      chapters: [
        { chapter_number: 1, title: "第一章", scene_plan: [{ scene_number: 1 }] },
      ],
    });
    setup("/project/p1/workspace?mode=manual&chapter=1&scene=1-1");
    const body = (await screen.findByTestId("editor-body")) as HTMLTextAreaElement;
    fireEvent.change(body, { target: { value: "新内容" } });
    fireEvent.click(screen.getByTestId("editor-save"));
    // findByText polls — the toast renders asynchronously after the save
    // promise resolves. Use waitFor to avoid races with the auto-dismiss
    // timer in case the test environment is slow.
    await waitFor(() => expect(screen.getByText(/草稿已保存/)).toBeInTheDocument());
  });

  // v1.10: chapter-level status from getStage4Progress surfaces as badges.
  it("manual mode renders chapter-status badges derived from getStage4Progress", async () => {
    // Both WorkspacePage and WorkspaceTopBar call getStage4Progress on mount.
    mockedGetStage4Progress.mockResolvedValue({
      chapters: [
        { chapter_number: 1, status: "completed" },
        { chapter_number: 2, status: "in_progress" },
      ],
      total_chapters: 2,
    });
    mockedGetOutline.mockResolvedValue({
      chapters: [
        { chapter_number: 1, title: "第一章", scene_plan: [{ scene_number: 1 }] },
        { chapter_number: 2, title: "第二章", scene_plan: [{ scene_number: 1 }] },
      ],
    });
    setup("/project/p1/workspace?mode=manual&chapter=1&scene=1-1");
    expect(await screen.findByTestId("chapter-status-1")).toBeInTheDocument();
    expect(screen.getByTestId("chapter-status-1").textContent).toBe("✓");
    expect(screen.getByTestId("chapter-status-2")).toBeInTheDocument();
    expect(screen.getByTestId("chapter-status-2").textContent).toBe("✎");
  });

  // v1.10: switching chapters refetches scene-drafts and updates the
  // scene-status dots accordingly.
  it("manual mode refetches scene drafts when currentChapter changes", async () => {
    mockedGetOutline.mockResolvedValue({
      chapters: [
        { chapter_number: 1, title: "第一章", scene_plan: [
          { scene_number: 1 }, { scene_number: 2 },
        ]},
        { chapter_number: 2, title: "第二章", scene_plan: [
          { scene_number: 1 },
        ]},
      ],
    });
    // Chapter 1: scene 1 has draft, scene 2 doesn't.
    // Chapter 2: scene 1 has draft.
    mockedGetSceneDrafts.mockImplementation(async (_pid: string, ch: number) => {
      if (ch === 1) return { chapter_number: 1, scenes: [
        { scene_number: 1, has_draft: true },
        { scene_number: 2, has_draft: false },
      ]};
      return { chapter_number: 2, scenes: [
        { scene_number: 1, has_draft: true },
      ]};
    });
    setup("/project/p1/workspace?mode=manual&chapter=1&scene=1-1");
    await waitFor(() => expect(mockedGetSceneDrafts).toHaveBeenCalledWith("p1", 1));
    expect(await screen.findByTestId("scene-status-1-1")).toBeInTheDocument();
    expect(screen.getByTestId("scene-status-1-1").textContent).toBe("●");
    expect(screen.getByTestId("scene-status-1-2").textContent).toBe("○");

    // Click chapter 2 to switch — refetch should fire.
    fireEvent.click(screen.getByTestId("chapter-2"));
    await waitFor(() => expect(mockedGetSceneDrafts).toHaveBeenCalledWith("p1", 2));
    expect(await screen.findByTestId("scene-status-2-1")).toBeInTheDocument();
    expect(screen.getByTestId("scene-status-2-1").textContent).toBe("●");
    // Chapter 1's cached entries must remain in the map (we don't refetch
    // to remove them — the user may switch back).
    expect(screen.queryByTestId("scene-status-1-1")).not.toBeInTheDocument();
  });

  // v1.10 fix: refresh button invokes api.repairProgress before reloadKey++,
  // so stuck chapters (status=in_progress but all scenes terminal) can be
  // reconciled without the LLM advance path.
  describe("refresh button triggers repairProgress", () => {
    it("clicking 刷新 calls repairProgress, reloads, and shows toast when chapters were repaired", async () => {
      mockedRepairProgress.mockResolvedValueOnce({
        repaired_chapters: [13],
        current_chapter: 14,
      });
      mockedGetOutline.mockResolvedValue({
        chapters: [
          { chapter_number: 1, title: "第一章", scene_plan: [{ scene_number: 1 }] },
        ],
      });
      setup("/project/p1/workspace?chapter=1&scene=1-1");
      // Wait for manual-mode UI to render so the refresh button is in the DOM.
      await screen.findByTestId("chapter-tree");
      const refreshBtn = await screen.findByTestId("refresh");
      fireEvent.click(refreshBtn);
      // repairProgress was called exactly once with the project id
      await waitFor(() =>
        expect(mockedRepairProgress).toHaveBeenCalledTimes(1)
      );
      expect(mockedRepairProgress).toHaveBeenCalledWith("p1");
      // Toast shows the count
      expect(
        await screen.findByText("已推进 1 个章节")
      ).toBeInTheDocument();
    });

    it("does not show toast when repairProgress returns no repaired chapters", async () => {
      mockedRepairProgress.mockResolvedValueOnce({
        repaired_chapters: [],
        current_chapter: 1,
      });
      mockedGetOutline.mockResolvedValue({
        chapters: [
          { chapter_number: 1, title: "第一章", scene_plan: [{ scene_number: 1 }] },
        ],
      });
      setup("/project/p1/workspace?chapter=1&scene=1-1");
      await screen.findByTestId("chapter-tree");
      const refreshBtn = await screen.findByTestId("refresh");
      fireEvent.click(refreshBtn);
      await waitFor(() =>
        expect(mockedRepairProgress).toHaveBeenCalledTimes(1)
      );
      // No "已推进" toast should appear
      expect(
        screen.queryByText(/已推进 \d+ 个章节/)
      ).not.toBeInTheDocument();
    });

    it("shows error toast when repairProgress rejects, but still triggers reload", async () => {
      mockedRepairProgress.mockRejectedValueOnce(new Error("disk full"));
      mockedGetOutline.mockResolvedValue({
        chapters: [
          { chapter_number: 1, title: "第一章", scene_plan: [{ scene_number: 1 }] },
        ],
      });
      // Snapshot the call count before the click so we can assert it grows.
      const callsBeforeClick = mockedGetStage4Progress.mock.calls.length;
      setup("/project/p1/workspace?chapter=1&scene=1-1");
      await screen.findByTestId("chapter-tree");
      const refreshBtn = await screen.findByTestId("refresh");
      fireEvent.click(refreshBtn);
      // Error toast surfaces
      expect(
        await screen.findByText(/章节收尾失败.*disk full/)
      ).toBeInTheDocument();
      // setReloadKey++ still fired → getStage4Progress called at least once more
      await waitFor(() =>
        expect(mockedGetStage4Progress.mock.calls.length).toBeGreaterThan(
          callsBeforeClick
        )
      );
    });
  });
});
