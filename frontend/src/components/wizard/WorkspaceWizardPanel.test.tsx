import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import WorkspaceWizardPanel from "./WorkspaceWizardPanel";
import { ToastProvider } from "../../hooks/useToast";
import api from "../../api/client";

vi.mock("../../api/client", () => ({
  default: {
    // The wizard's prefill effect (Plan Task 25, 2026-08-30) no longer calls
    // any creative-divergence endpoint — CreativeDivergenceStep infers its
    // own SubStage from /state on mount.
    getDivergeState: vi.fn().mockResolvedValue({
      raw_intent: null,
      idea_variants: [],
      core_contradiction: null,
      selected_path: [],
    }),
    getCreativeDivergence: vi.fn().mockResolvedValue({
      variants: [],
      selected_id: null,
      has_selection: false,
      selected_at: null,
    }),
    getCanvasV2State: vi.fn().mockResolvedValue({
      schema_version: 4,
      session_id: "default",
      _etag: "default",
      root_idea: {
        prompt: "",
        genre: "",
        premise: "",
        extracted: { genre: "", core_elements: [], potential_conflict: "" },
      },
      raw_intent: { prompt: "", genre_primary: "" },
      creative_session: { current_step: 1, max_steps: 5, status: "active" },
      creative_path: [],
      current_concept: {},
      final_concept: null,
      committed: false,
      committed_at: null,
      scores: { novelty: 0, conflict: 0, story_potential: 0, uniqueness: 0, computed_at: "" },
      session_metadata: {},
    }),
    getConcept: vi.fn().mockRejectedValue(new Error("404")),
    getWorld: vi.fn().mockRejectedValue(new Error("404")),
    getCharacter: vi.fn().mockRejectedValue(new Error("404")),
    getNovelOutline: vi.fn().mockRejectedValue(new Error("404")),
    getOutline: vi.fn().mockRejectedValue(new Error("404")),
    // Workspace wizard prefill should fetch map.json (step 4 = 地图系统).
    // Default to missing-map back-compat (`{}`) so existing tests aren't
    // affected unless they explicitly mock this.
    getMap: vi.fn().mockResolvedValue({}),
    // 3B divergence (Plan 2026-09-05): useB3Divergence fires
    // getB3State on mount. Mock empty-state so the hook's HYDRATE
    // runs without throwing.
    getB3State: vi.fn().mockResolvedValue({
      schema_version: 1,
      project_id: "proj_test",
      raw_intent: null,
      stage2_candidates: [],
      stage3_deepened: [],
      committed: false,
    }),
    deleteB3State: vi.fn().mockResolvedValue({ ok: true }),
    // Theme migration (2026-09-05): S1InputStep calls useGenres → api.listGenres
    listGenres: vi.fn().mockResolvedValue([]),
    // Task 12 (2026-09-10): S1InputStep now reads creative dimensions via
    // useCreativeDimensions → api.listActiveCreativeDimensions. Empty
    // arrays are safe here because these tests don't exercise the S1 form.
    listActiveCreativeDimensions: vi.fn().mockResolvedValue({ subject: [], tone: [], style: [] }),
    // Genre-inheritance fix (2026-09-11): useB3Divergence fires
    // getProjectStatus on mount to pre-fill S1's subject dropdown.
    // Returned genre is only used when rawIntent is missing; empty string
    // is safe here because the S1 form is not exercised.
    getProjectStatus: vi.fn().mockResolvedValue({ title: "T", genre: "" }),
  },
}));

beforeEach(() => {
  // WizardProvider hydrates from sessionStorage by projectId. Tests
  // share the jsdom sessionStorage, so without this earlier tests in
  // this file would leak currentStep / completedSteps into later
  // tests, masking the fresh-state behaviour we want to verify
  // (e.g., the plot-canvas mount-point test should see step 1 as
  // current, not whatever the previous test left behind).
  sessionStorage.clear();
  vi.clearAllMocks();
});

describe("WorkspaceWizardPanel", () => {
  it("renders WizardSidebar + step 1 divergence step", async () => {
    render(<MemoryRouter><WorkspaceWizardPanel projectId="proj_test" /></MemoryRouter>);
    expect(screen.getByTestId("wizard-sidebar")).toBeInTheDocument();
    // "创意发散" appears in the sidebar item, header subtitle, and step 1 h2.
    // Asserting ≥1 match confirms step 1's divergence step mounted.
    await waitFor(() => expect(screen.getAllByText("创意发散").length).toBeGreaterThanOrEqual(1));
  });

  it("renders 剧情画布 at sidebar position 6 (between 地图系统 and 全文大纲)", async () => {
    render(<MemoryRouter><WorkspaceWizardPanel projectId="proj_test" /></MemoryRouter>);
    await waitFor(() =>
      expect(screen.getByTestId("wizard-sidebar-item-plot")).toBeInTheDocument()
    );
    const plot = screen.getByTestId("wizard-sidebar-item-plot");
    expect(plot).toHaveTextContent("剧情画布");
  });

  it("calls the remaining prefill endpoints on mount", async () => {
    render(<MemoryRouter><WorkspaceWizardPanel projectId="proj_test" /></MemoryRouter>);
    await waitFor(() => {
      expect(api.getConcept).toHaveBeenCalled();
      expect(api.getWorld).toHaveBeenCalled();
      expect(api.getCharacter).toHaveBeenCalled();
      expect(api.getNovelOutline).toHaveBeenCalled();
      expect(api.getOutline).toHaveBeenCalled();
      expect(api.getCanvasV2State).toHaveBeenCalled();
    });
  });

  it("prefills completedSteps=[...,4] when map.json has content (workspace wizard step 4 indicator)", async () => {
    // WorkspaceWizardPanel's prefill previously fetched world / canvas /
    // novel / outline but skipped api.getMap. After generating a map,
    // navigating away and back lost the sidebar ✅ even though map.json
    // was on disk. InitWizardModal.tsx had this correctly. (proj_47738f64
    // 2026-09-23)
    (api.getMap as ReturnType<typeof vi.fn>).mockResolvedValue({
      schema_version: "1.0",
      project_id: "proj_test",
      regions: [{ id: "region_x", name: "X" }],
      locations: [{ id: "loc_x", name: "X", region_id: "region_x", type: "room" }],
      routes: [],
      pois: [],
      location_states: [],
      snapshots: [],
      footprints: [],
      assertions: [],
      change_log: [],
      display: { positions: {} },
      settings: { mode: "allow_alias_new", scope_enabled: false, allowed_region_ids: [], chapter_new_location_cap: 5, reuse_rate_target: 0.6, strict_geo: false },
    });
    render(<MemoryRouter><WorkspaceWizardPanel projectId="proj_test" /></MemoryRouter>);
    await waitFor(() => {
      // Step 4 (地图系统) must render data-state="completed" so the sidebar
      // shows ✅ even when the user only entered step 4 (no other steps).
      expect(screen.getByTestId("wizard-sidebar-item-map").getAttribute("data-state")).toBe("completed");
      expect(api.getMap).toHaveBeenCalledWith("proj_test");
    });
  });

  it("prefills completedSteps does NOT include 4 when map.json is missing (back-compat for pre-map-system projects)", async () => {
    // 老项目(无 map.json)应当不显示 ✅,避免误报 completed。
    (api.getMap as ReturnType<typeof vi.fn>).mockResolvedValue({});
    render(<MemoryRouter><WorkspaceWizardPanel projectId="proj_legacy_no_map" /></MemoryRouter>);
    await waitFor(() => {
      expect(api.getMap).toHaveBeenCalledWith("proj_legacy_no_map");
    });
    // After prefill, map sidebar item must NOT be in "completed" state.
    // Other items may be in various states; we only assert the negative on map.
    const mapItem = screen.getByTestId("wizard-sidebar-item-map");
    expect(mapItem.getAttribute("data-state")).not.toBe("completed");
  });

  it("prefills completedSteps=[1] when creative_divergence.json has selected_at (Path B variant select)", async () => {
    (api.getCreativeDivergence as unknown as ReturnType<typeof vi.fn>).mockResolvedValue({
      variants: [{ id: "v1", label: "ALPHA", title: "T", description: "D", tags: [], created_at: "2026-08-31T14:00:00Z" }],
      selected_id: "v1",
      has_selection: true,
      selected_at: "2026-08-31T14:00:05.164787",
    });
    (api.getConcept as unknown as ReturnType<typeof vi.fn>).mockResolvedValue({
      concept: { title: "建木之囚", genre: "爽文", premise: "P", tone: "T", theme: "T", target_audience: "A", style_template: "" },
      story_dna: { core_contradiction: { statement: "S", side_a: "A", side_b: "B" }, value_stack: [] },
    });
    render(<MemoryRouter><WorkspaceWizardPanel projectId="proj_test" /></MemoryRouter>);
    await waitFor(() => {
      // World (position 2 in v2.x) should NOT be disabled once step 1 surfaces
      // are completed via prefill (OR-semantic — divergence done here).
      expect(screen.getByTestId("wizard-sidebar-item-world")).not.toHaveAttribute("disabled");
      // Character (position 3) is still disabled because we didn't mock a
      // characters.json here.
      expect(screen.getByTestId("wizard-sidebar-item-character")).toHaveAttribute("disabled");
    });
  });

  it("prefills completedSteps=[1] when selected_id is null but selected_at is set (Path A canvas source — proj_f0721bdc)", async () => {
    // Regression for the proj_f0721bdc 2026-08-31 case where source="canvas"
    // dual-write at /commit_canvas sets selected_at=now but selected_id=None
    // (no Path B variant was chosen). The earlier `has_selection &&
    // selected_at` guard missed step 1 for canvas-source projects. The
    // current guard is `selected_at` alone, which both paths satisfy.
    (api.getCreativeDivergence as unknown as ReturnType<typeof vi.fn>).mockResolvedValue({
      variants: [],
      selected_id: null,
      has_selection: false,
      selected_at: "2026-08-31T14:00:05.164787",
    });
    render(<MemoryRouter><WorkspaceWizardPanel projectId="proj_f0721bdc" /></MemoryRouter>);
    await waitFor(() => {
      expect(screen.getByTestId("wizard-sidebar-item-divergence")).not.toHaveAttribute("disabled");
    });
  });

  it("prefills completedSteps=[1] when selected_at is null but concept_and_dna.json exists (legacy pre-2026-08-31 / interrupted-commit projects)", async () => {
    // 2026-09-21 regression: projects whose creative_divergence.json was
    // written BEFORE the dual-write of selected_at was added (pre
    // 2026-08-31 proj_f0721bdc fix) — OR whose commit partially failed
    // (concept_and_dna.json was written but creative_divergence.json's
    // selected_at was not) — had no selected_at to drive the step 1
    // sidebar gate. Symptom: user on step 2 couldn't click 创意发散
    // to navigate back. Fallback: if concept_and_dna.json has content,
    // divergence was effectively completed — still mark step 1 done.
    //
    // Pre-seed sessionStorage so WizardProvider loads currentStep=2 (the
    // user's actual state when they hit the bug); otherwise the default
    // currentStep=1 makes step 1 trivially reachable via the `current`
    // branch of the sidebar's reachability gate and the test wouldn't
    // exercise the bug.
    const KEY = (await import("./WizardContext")).getSessionKey("proj_legacy");
    sessionStorage.setItem(KEY, JSON.stringify({
      currentStep: 2,
      completedSteps: [2],
      status: "completed",
      data: {
        concept: null, story_dna: null, world: { era: "现代", geography: "城市", factions: [], power_systems: [] },
        characters: null, novel_outline: null, chapter1_outline: null,
      },
      errorMessage: null,
    }));
    (api.getCreativeDivergence as unknown as ReturnType<typeof vi.fn>).mockResolvedValue({
      variants: [{ id: "v1", label: "A", title: "T", description: "D", tags: [], created_at: "2026-08-01T00:00:00Z" }],
      selected_id: null,
      has_selection: false,
      selected_at: null,
    });
    (api.getConcept as unknown as ReturnType<typeof vi.fn>).mockResolvedValue({
      concept: { title: "建木之囚", genre: "爽文", premise: "P", tone: "T", theme: "T", target_audience: "A", style_template: "" },
      story_dna: { core_contradiction: { statement: "S", side_a: "A", side_b: "B" }, value_stack: [] },
    });
    (api.getWorld as unknown as ReturnType<typeof vi.fn>).mockResolvedValue({
      era: "现代", geography: "城市", factions: [], power_systems: [],
    });
    render(<MemoryRouter><WorkspaceWizardPanel projectId="proj_legacy" /></MemoryRouter>);
    await waitFor(() => {
      // After prefill, divergence sidebar item is enabled even though
      // selected_at was null — the concept_and_dna fallback rescued it.
      expect(screen.getByTestId("wizard-sidebar-item-divergence")).not.toHaveAttribute("disabled");
    });
  });
});

describe("WorkspaceWizardPanel (post-integration)", () => {
  it("prefill with canvas.committed=true populates completedSteps=[1,6]", async () => {
    (api.getCanvasV2State as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      schema_version: 4,
      session_id: "s",
      _etag: "e",
      root_idea: {
        prompt: "x",
        genre: "x",
        premise: "x",
        extracted: { genre: "x", core_elements: [], potential_conflict: "" },
      },
      raw_intent: { prompt: "x", genre_primary: "x" },
      creative_session: { current_step: 5, max_steps: 5, status: "committed" },
      creative_path: [],
      current_concept: {},
      final_concept: null,
      committed: true,
      committed_at: "2026-09-03T00:00:00Z",
      scores: { novelty: 0, conflict: 0, story_potential: 0, uniqueness: 0, computed_at: "" },
      session_metadata: {},
    });
    // selected_at set → step 1 (divergence) is also marked completed via
    // the prefill branch in WorkspaceWizardPanel (proj_f0721bdc 2026-08-31
    // regression: canvas.committed alone used to leave divergence incomplete).
    (api.getCreativeDivergence as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      has_selection: true,
      selected_at: "2026-09-03T00:00:05.000000",
      selected_id: "v1",
      variants: [{ id: "v1", label: "A", title: "T", description: "D", tags: [], created_at: "2026-09-03T00:00:00Z" }],
    });
    render(<MemoryRouter><WorkspaceWizardPanel projectId="proj_test" /></MemoryRouter>);
    await waitFor(() => {
      // Step 5 (剧情画布) is now completed in completedSteps, so its
      // sidebar item renders data-state="completed".
      expect(screen.getByTestId("wizard-sidebar-item-plot").getAttribute("data-state")).toBe("completed");
      // And step 1 (创意发散) is also completed — divergence and canvas
      // are independent steps in the post-abstraction model.
      expect(screen.getByTestId("wizard-sidebar-item-divergence").getAttribute("data-state")).toBe("completed");
    });
  });

  it("renders PlotCanvasMountPoint when currentStep=5", async () => {
    // 2026-09-19: 砍「概念 DNA」后剧情画布从 step 6 移到 step 5。
    // Hydrate the wizard at currentStep=5 (剧情画布) via sessionStorage so
    // the panel renders PlotCanvasMountPoint on mount. This bypasses the
    // sidebar's "next-step reachable" gating, which would otherwise require
    // either completing step 4 first or stepping through every prior step
    // — neither of which is the unit under test.
    sessionStorage.setItem(
      "nebula.wizard.state.proj_test",
      JSON.stringify({
        currentStep: 5,
        completedSteps: [1, 2, 3, 4],
        status: "idle",
        data: {},
        errorMessage: null,
        creativeDivergenceSubStage: "A",
      })
    );
    (api.getCanvasV2State as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      schema_version: 4,
      session_id: "s",
      _etag: "e",
      root_idea: {
        prompt: "x",
        genre: "x",
        premise: "x",
        extracted: { genre: "x", core_elements: [], potential_conflict: "" },
      },
      raw_intent: { prompt: "x", genre_primary: "x" },
      creative_session: { current_step: 1, max_steps: 5, status: "active" },
      creative_path: [],
      current_concept: {},
      final_concept: null,
      committed: false,
      committed_at: null,
      scores: { novelty: 0, conflict: 0, story_potential: 0, uniqueness: 0, computed_at: "" },
      session_metadata: {},
    });
    (api.getCreativeDivergence as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      has_selection: false,
      selected_at: null,
      selected_id: null,
      variants: [],
    });
    render(
      <ToastProvider>
        <MemoryRouter><WorkspaceWizardPanel projectId="proj_test" /></MemoryRouter>
      </ToastProvider>
    );
    await waitFor(() => {
      expect(screen.getByTestId("wizard-sidebar-item-plot").getAttribute("data-state")).toBe("current");
      expect(screen.getByTestId("plot-canvas-mount-point")).toBeInTheDocument();
    });
  });
});
