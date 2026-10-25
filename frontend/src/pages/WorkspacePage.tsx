import { useEffect, useRef, useState } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import api from "../api/client";
import { useWorkspaceMode } from "../hooks/useWorkspaceMode";
import type { WorkspaceMode } from "../hooks/useWorkspaceMode";
import WorkspaceTopBar from "../components/workspace/WorkspaceTopBar";
import WorkspaceWritingPanel, {
  type WorkspaceWritingPanelHandle,
} from "../components/workspace/WorkspaceWritingPanel";
import WorkspaceWizardPanel from "../components/wizard/WorkspaceWizardPanel";
import PromptPlazaModal from "../components/home/promptPlaza/PromptPlazaModal";
import AIConsoleModal from "../components/aiConsole/AIConsoleModal";

type WorkspaceTab = "settings" | "manuscript";

const TAB_STORAGE_KEY = "nebula.workspace.active-tab";

export default function WorkspacePage({ projectId: projectIdProp }: { projectId?: string } = {}) {
  const params = useParams<{ projectId: string }>();
  const projectId = projectIdProp ?? params.projectId ?? "";
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const { mode, setMode } = useWorkspaceMode();

  // Page-level state — kept here because the TopBar and global modals need
  // it but neither child panel does.
  const [projectName, setProjectName] = useState("加载中…");
  const [reloadKey, setReloadKey] = useState(0);

  // v1.9: AI 工具 dropdown opens the Prompt Plaza modal for THIS project
  // (not the most-recent project like the home page entry).
  const [plazaOpen, setPlazaOpen] = useState(false);
  const [consoleOpen, setConsoleOpen] = useState(false);

  // v2.x: tab switcher state (Plan Task 12, 2026-08-30). Settings = wizard;
  // manuscript = writing panel. Default landing logic lives below.
  // Initialize activeTab from the URL so first paint matches the user's
  // intent — no flash of the wizard for deep links like `?tab=manuscript`
  // (and integration tests that expect the writing panel to be present
  // synchronously after render).
  const [activeTab, setActiveTabState] = useState<WorkspaceTab>(() => {
    const requested = searchParams.get("tab");
    return requested === "manuscript" || requested === "settings"
      ? requested
      : "settings";
  });
  // If the URL explicitly asks for the manuscript tab, treat the wizard as
  // already complete for the initial paint so the writing panel mounts
  // synchronously. The preflight + landing-effect will correct this to
  // `false` (and flip the tab back to "settings") if the wizard data is
  // actually incomplete.
  const [allStepsDone, setAllStepsDone] = useState(
    () => searchParams.get("tab") === "manuscript",
  );
  // Imperative ref into <WorkspaceWritingPanel> — lets the TopBar's
  // mode-switcher click route through the panel's `handleModeChange`
  // (which opens the confirm/start modals) instead of bypassing them by
  // calling the global setMode directly. Null when the wizard tab is
  // active (panel not mounted).
  const writingPanelRef = useRef<WorkspaceWritingPanelHandle>(null);

  // Project name load — best-effort. 404 redirects to "/" per spec
  // § Error Handling. Anything else is silently swallowed (page still
  // renders with the default "加载中…" title).
  useEffect(() => {
    if (!projectId) return;
    let cancelled = false;
    api
      .getProjectStatus(projectId)
      .then((s: { title?: string }) => {
        if (!cancelled && s?.title) setProjectName(s.title);
      })
      .catch((err: { response?: { status?: number }; status?: number }) => {
        if (cancelled) return;
        if (err?.response?.status === 404 || err?.status === 404) {
          navigate("/", { replace: true });
        }
      });
    return () => {
      cancelled = true;
    };
  }, [projectId, navigate]);

  // Detect whether all 7 wizard steps are done. We treat the manuscript tab
  // as "unlocked" only when 6 wizard-data endpoints resolve successfully
  // (steps 1-6 cover the wizard pre-write data; the panel itself checks
  // step 7 / chapter outline on mount) AND step 1 (creative_divergence)
  // actually has a user selection (`has_selection: true`).
  //
  // URL override: while `?tab=manuscript` is in the URL, the user has
  // explicitly opted into the manuscript tab. We still run the preflight
  // (so the landing-effect can flip the tab back to "settings" if the
  // wizard data is incomplete), but the initial paint shows the writing
  // panel regardless. Refetches on `reloadKey` so completing the wizard
  // inside <WorkspaceWizardPanel> (which bumps reloadKey on save) unlocks
  // the manuscript tab without a page reload.
  useEffect(() => {
    if (!projectId) return;
    let cancelled = false;
    (async () => {
      try {
        const [cd, c, w, ch, n, o] = await Promise.allSettled([
          api.getCreativeDivergencePrefill(projectId),
          api.getConcept(projectId),
          api.getWorld(projectId),
          api.getCharacter(projectId),
          api.getNovelOutline(projectId),
          api.getOutline(projectId),
        ]);
        if (cancelled) return;
        const fulfilledCount = [cd, c, w, ch, n, o].filter(
          (r) => r.status === "fulfilled",
        ).length;
        const cdOk = cd.status === "fulfilled" && cd.value.has_selection === true;
        setAllStepsDone(fulfilledCount === 6 && cdOk);
      } catch {
        if (!cancelled) setAllStepsDone(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [projectId, reloadKey, searchParams]);

  // Default landing logic: runs whenever `allStepsDone` flips (the panel
  // bumps reloadKey → this effect re-runs → user auto-lands on manuscript
  // once the wizard is complete, per spec §4.5). Reads `searchParams` but
  // intentionally does NOT add it to the dep array: this is a one-shot
  // landing decision per `allStepsDone` transition, not a URL watcher.
  useEffect(() => {
    const requested = searchParams.get("tab");
    if (requested === "settings" || requested === "manuscript") {
      if (requested === "manuscript" && !allStepsDone) {
        // URL asked for manuscript but it's locked → fall back to settings.
        setActiveTabState("settings");
      } else {
        setActiveTabState(requested);
      }
    } else {
      // No URL preference: complete wizard → manuscript; otherwise settings.
      setActiveTabState(allStepsDone ? "manuscript" : "settings");
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [allStepsDone]);

  // v2.x: when the manuscript tab becomes active (i.e. the wizard is fully
  // done and the user has just entered the writing cockpit for the first
  // time), advance the project stage from INIT to STAGE4 so the writing
  // pipeline picks up the right state. The spec §3.1 moved this trigger
  // out of InitWizardModal and into the workspace shell so it runs on the
  // deep-link landing path too (e.g. an existing project where the wizard
  // was finished in a previous session).
  //
  // One-shot per manuscript-tab activation (reset on leave so a deliberate
  // round-trip through the wizard re-fires). Best-effort: a failed advance
  // (e.g. server precondition) is logged but does not block the page.
  const advanceTriggeredRef = useRef(false);
  useEffect(() => {
    if (activeTab !== "manuscript") {
      advanceTriggeredRef.current = false;
      return;
    }
    if (!allStepsDone || !projectId) return;
    if (advanceTriggeredRef.current) return;
    advanceTriggeredRef.current = true;
    try {
      api
        .advance(projectId, "STAGE4")
        .catch((err: unknown) => {
          // eslint-disable-next-line no-console
          console.warn("advance to STAGE4 failed", err);
        });
    } catch (err) {
      // Some test mocks omit api.advance — don't blow up the page.
      // eslint-disable-next-line no-console
      console.warn("advance to STAGE4 unavailable", err);
    }
  }, [activeTab, allStepsDone, projectId]);

  const handleTabChange = (next: WorkspaceTab) => {
    // Hard guard: never let the user click into a locked manuscript tab.
    // TopBar already disables the button, but belt-and-suspenders.
    if (next === "manuscript" && !allStepsDone) return;
    if (next === activeTab) return;
    setActiveTabState(next);
    try {
      localStorage.setItem(TAB_STORAGE_KEY, next);
    } catch {
      // localStorage may be unavailable (private mode, SSR); not fatal.
    }
    const sp = new URLSearchParams(searchParams);
    sp.set("tab", next);
    setSearchParams(sp, { replace: true });
  };

  // Forward TopBar mode clicks to the writing panel (when mounted) so the
  // panel's confirm/start modal flow runs instead of flipping the mode
  // silently via the global hook. Falls back to the global setter on the
  // wizard tab — there the panel isn't mounted, but no user-facing modal
  // is expected either (mode is dormant while the wizard is unfinished).
  const handleTopBarModeChange = (m: WorkspaceMode) => {
    if (writingPanelRef.current) {
      writingPanelRef.current.requestModeChange(m);
    } else {
      setMode(m);
    }
  };

  return (
    <div data-testid="workspace-page-shell" className="h-screen flex flex-col bg-canvas-bg">
      <WorkspaceTopBar
        projectId={projectId}
        projectName={projectName}
        mode={mode}
        onModeChange={handleTopBarModeChange}
        onOpenPlaza={() => setPlazaOpen(true)}
        onOpenConsole={() => setConsoleOpen(true)}
        activeTab={activeTab}
        onTabChange={handleTabChange}
        manuscriptLocked={!allStepsDone}
      />

      {activeTab === "settings" ? (
        <WorkspaceWizardPanel projectId={projectId} />
      ) : (
        <WorkspaceWritingPanel
          ref={writingPanelRef}
          projectId={projectId}
          projectName={projectName}
          mode={mode}
          setMode={setMode}
          reloadKey={reloadKey}
          setReloadKey={setReloadKey}
        />
      )}

      <PromptPlazaModal
        isOpen={plazaOpen}
        projectId={projectId}
        projectTitle={projectName === "加载中…" ? null : projectName}
        onClose={() => setPlazaOpen(false)}
      />
      <AIConsoleModal
        isOpen={consoleOpen}
        onClose={() => setConsoleOpen(false)}
      />
    </div>
  );
}
