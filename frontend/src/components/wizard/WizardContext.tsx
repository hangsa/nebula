import { createContext, useContext, useReducer, useEffect, useRef, ReactNode } from "react";
import type { World, CharacterSet, NovelOutline, Outline, MapPayload } from "../../api/client";

export function getSessionKey(projectId: string): string {
  return `nebula.wizard.state.${projectId}`;
}

export type WizardStatus = "idle" | "generating" | "completed" | "error";

/**
 * Footer status indicator for the section regenerate flow. Tracked
 * separately from `status` (the in-form overlay state) because:
 *   - `status === "generating"` replaces the form with a full-page spinner,
 *     which is appropriate for the initial generation but jarring for a
 *     single-section refresh (the user expects to see the section update).
 *   - The regenerate status needs transient success/failure states with
 *     auto-clear, which the durable `status` field doesn't support.
 *
 * Lives in context (not local to SectionRegenerateButton) because the
 * indicator renders in the modal footer (rendered by InitWizardModal),
 * which is a sibling of the step that owns the button.
 */
export type WizardRegenerateState =
  | { kind: "idle" }
  | { kind: "busy"; target: string; startedAt: number }
  | { kind: "success"; target: string; at: number }
  | { kind: "failure"; target: string; message: string; at: number };

/**
 * Within step 1 (Creative Divergence), the current sub-step position. Step 1
 * is broken into 5 sequential screens: A (input), B (mutation), C
 * (contradiction), D (expand), E (commit). The CreativeDivergenceStep
 * component reads this to decide which sub-screen renders, and resets to "A"
 * whenever the user enters step 1 fresh.
 */
export type CreativeDivergenceSubStage = "A" | "B" | "C" | "D" | "E";


export interface WizardData {
  creative_divergence: {
    variants: Array<{ id: string; label: string; title: string; description: string; tags: string[]; created_at: string }>;
    selected_id: string | null;
  } | null;
  world: World | null;
  characters: CharacterSet | null;
  map: MapPayload | null;
  novel_outline: NovelOutline | null;
  chapter1_outline: Outline | null;
  /**
   * Mid-batch progress for step 7 (chapter-outline generation). Non-null when
   * the user paused mid-batch — ChapterOutlineStep renders a "继续生成" CTA
   * from this, then clears it on completion or fresh auto-trigger. Shape:
   *   - done: number of chapters already generated and persisted to disk.
   *   - total: planned batch size (parsed from novel_outline.volumes[0] end,
   *     or fallback 10 when Volume 1 is missing).
   *   - last_user_modifications: the prompt mods applied to the last batch
   *     iteration, re-used on resume so the user's instructions survive
   *     a pause/refresh cycle. Empty string means "no mods".
   */
  chapter_outline_progress: {
    done: number;
    total: number;
    last_user_modifications: string;
  } | null;
}

export const TOTAL_STEPS = 7;

const EMPTY_DATA: WizardData = {
  creative_divergence: null,
  world: null,
  characters: null,
  map: null,
  novel_outline: null,
  chapter1_outline: null,
  chapter_outline_progress: null,
};

// Maps each wizard data key to the step that owns it. step 5 (行为示例 /
// behavior examples) owns no data. Used by the STEP_COMPLETED reducer to clear
// downstream keys on resave.
//
// 2026-09-19 砍掉概念DNA 步骤(原 step 2)— concept / story_dna 字段被移除,
// 下游步骤编号统一 -1:
//   - world:    3 → 2
//   - characters: 4 → 3
//   - novel_outline:   7 → 6
//   - chapter1_outline: 8 → 7
//   - chapter_outline_progress: 8 → 7
const STEP_DATA_KEY_TO_STEP: Partial<Record<keyof WizardData, number>> = {
  creative_divergence: 1,
  world: 2,
  characters: 3,
  map: 4,
  novel_outline: 6,
  chapter1_outline: 7,
  // Mid-batch progress for step 7's chapter-outline generation. Lives in
  // wizard.data so it survives navigation; must be cleared on resave of any
  // step ≤ 6 so a stale "3/10 done" from a previous run doesn't leak into
  // the next attempt. Without this entry, the resave branch's
  // `ownerStep === undefined` fallback would treat this key as unmapped and
  // preserve it.
  chapter_outline_progress: 7,
};

interface WizardState {
  currentStep: number;
  completedSteps: number[];
  status: WizardStatus;
  data: WizardData;
  errorMessage: string | null;
  /**
   * Transient regenerate status, surfaced in the modal footer beside
   * "重新生成". Distinct from `status` — see WizardRegenerateState doc.
   */
  regenerateState: WizardRegenerateState;
  /**
   * True once the file-based prefill useEffect has finished running (whether
   * or not it found any files). Steps with an auto-trigger (OutlineStep,
   * ChapterOutlineStep, WorldStep, etc.) must wait for this before deciding
   * to call `generate*` — without it, the auto-trigger fires synchronously
   * on mount before the async prefill can hydrate wizard.data from disk,
   * which manifests as "re-entering the wizard regenerates content the user
   * already paid for" (e.g. proj_cc4ca4ae regression, v1.8.2).
   */
  prefillComplete: boolean;
  /**
   * The current step's "next" action — handled by the modal footer. Each step
   * registers its own handler via useEffect and clears it on unmount.
   */
  nextHandler: (() => void) | null;
  nextDisabled: boolean;
  /**
   * Optional label/loadingLabel for the modal footer's "next" button. Set
   * per-sub-stage so the divergence wizard can show "下一步:发散 →" / "拆解中…"
   * etc. on wizard step 1's sub-stages instead of the generic
   * "下一步:拆解 →" / "确认修改并继续". Falls back to those defaults when
   * null. Lives in state so the footer can read it like any other field.
   */
  nextLabel: string | null;
  nextLoadingLabel: string | null;
  /**
   * Optional click handler attached to the footer's loading label (e.g.
   * "发散中…"). When set, the modal/panel next button stays clickable even
   * while loadingLabel is showing, and clicking it invokes this handler
   * instead of nextHandler. Used by CreativeDivergenceStep to wire the
   * "暂停 / 继续" toggle onto the loading label itself. null when the step
   * has no pause/resume affordance (most steps).
   */
  nextLoadingClickHandler: (() => void) | null;
  /**
   * The current step's "previous" action — handled by the modal footer. When
   * registered, the footer's 上一步 button calls this handler instead of the
   * default `jumpToStep(currentStep - 1)`. Used by the divergence wizard's
   * sub-stages (S2/S3/S4) so the user can step back to a previous sub-stage
   * without leaving wizard step 1. null when no sub-stage back-nav applies.
   */
  prevHandler: (() => void) | null;
  /**
   * The current step's "regenerate" action — same lifecycle as nextHandler.
   * null when the step doesn't have a regenerate affordance.
   */
  regenerateHandler: (() => void) | null;
  regenerateDisabled: boolean;
  /**
   * The current step's "save without advancing" action. Distinct from
   * nextHandler: persists current page content to disk but leaves currentStep
   * alone (uses MARK_STEP_GENERATED instead of STEP_COMPLETED). null when
   * the step has no data to save (e.g., MapStep placeholder).
   */
  saveHandler: (() => void) | null;
  saveDisabled: boolean;
  creativeDivergenceSubStage: CreativeDivergenceSubStage;
}

type WizardAction =
  | { type: "START_STEP"; step: number }
  | { type: "STEP_COMPLETED"; step: number; patch: Partial<WizardData> }
  /**
   * Step generated content via an auto-trigger but did NOT advance the
   * user's position (the user still has to click "下一步" to confirm).
   * Mark it generated: add to completedSteps, write `data`, set
   * status="completed", but DO NOT touch currentStep. This is what keeps
   * the step reachable in the indicator after the user navigates to an
   * earlier step — without it, completedSteps stays stale and the step
   * becomes "pending" (gray) — see PROJ_proj_cc4ca4ae_report.
   */
  | { type: "MARK_STEP_GENERATED"; step: number; patch: Partial<WizardData> }
  | { type: "STEP_SKIPPED"; step: number }
  | { type: "JUMP_TO"; step: number }
  | { type: "STATUS"; status: WizardStatus; errorMessage?: string | null }
  | { type: "REGENERATE_BUSY"; target: string }
  | { type: "REGENERATE_SUCCESS"; target: string }
  | { type: "REGENERATE_FAILURE"; target: string; message: string }
  | { type: "REGENERATE_CLEAR" }
  | { type: "RESET" }
  | { type: "HYDRATE"; state: WizardState }
  | { type: "UPDATE_DATA"; patch: Partial<WizardData> }
  | {
      type: "HYDRATE_FROM_FILES";
      completedSteps: number[];
      data: Partial<WizardData>;
    }
  | {
      type: "HYDRATE_FROM_FILES_AND_ADVANCE";
      completedSteps: number[];
      data: Partial<WizardData>;
      nextStep: number;
    }
  | { type: "PREFLILL_COMPLETE" }
  | {
      type: "SET_NEXT_HANDLER";
      handler: (() => void) | null;
      disabled: boolean;
      label?: string | null;
      loadingLabel?: string | null;
    }
  | {
      type: "SET_PREV_HANDLER";
      handler: (() => void) | null;
    }
  | {
      type: "SET_REGENERATE_HANDLER";
      handler: (() => void) | null;
      disabled: boolean;
    }
  | {
      type: "SET_SAVE_HANDLER";
      handler: (() => void) | null;
      disabled: boolean;
    }
  | {
      type: "SET_NEXT_LOADING_CLICK_HANDLER";
      handler: (() => void) | null;
    }
  | { type: "SET_DIVERGENCE_SUBSTAGE"; subStage: CreativeDivergenceSubStage }
  | { type: "JUMP_TO_CREATIVE_DIVERGENCE"; subStage: CreativeDivergenceSubStage };

const initialState: WizardState = {
  currentStep: 1,
  completedSteps: [],
  status: "idle",
  data: EMPTY_DATA,
  errorMessage: null,
  regenerateState: { kind: "idle" },
  prefillComplete: false,
  nextHandler: null,
  nextDisabled: false,
  nextLabel: null,
  nextLoadingLabel: null,
  nextLoadingClickHandler: null,
  prevHandler: null,
  regenerateHandler: null,
  regenerateDisabled: false,
  saveHandler: null,
  saveDisabled: false,
  creativeDivergenceSubStage: "A",
};

function reducer(state: WizardState, action: WizardAction): WizardState {
  switch (action.type) {
    case "START_STEP":
      return { ...state, currentStep: action.step, status: "generating", errorMessage: null };
    case "STEP_COMPLETED": {
      const { step, patch } = action;
      const isResave = state.completedSteps.includes(step);
      let nextCompleted: number[];
      let nextData: WizardData;
      if (isResave) {
        // Resave of an already-completed step: drop everything past `step`
        // from completedSteps and clear their data keys. The current step's
        // data is replaced by the new patch.
        nextCompleted = state.completedSteps.filter((s) => s <= step);
        const cleared: WizardData = { ...EMPTY_DATA };
        for (const [key, value] of Object.entries(state.data)) {
          const ownerStep = STEP_DATA_KEY_TO_STEP[key as keyof WizardData];
          if (ownerStep === undefined || ownerStep <= step) {
            (cleared as unknown as Record<string, unknown>)[key] = value;
          }
        }
        nextData = { ...cleared, ...patch };
      } else {
        nextCompleted = [...state.completedSteps, step].sort((a, b) => a - b);
        nextData = { ...state.data, ...patch };
      }
      return {
        ...state,
        status: "completed",
        completedSteps: nextCompleted,
        currentStep: Math.min(step + 1, TOTAL_STEPS),
        data: nextData,
      };
    }
    case "STEP_SKIPPED":
      return {
        ...state,
        status: "completed",
        completedSteps: state.completedSteps.includes(action.step)
          ? state.completedSteps
          : [...state.completedSteps, action.step].sort((a, b) => a - b),
        currentStep: Math.min(action.step + 1, TOTAL_STEPS),
      };
    case "MARK_STEP_GENERATED": {
      const { step, patch } = action;
      const nextCompleted = state.completedSteps.includes(step)
        ? state.completedSteps
        : [...state.completedSteps, step].sort((a, b) => a - b);
      return {
        ...state,
        status: "completed",
        completedSteps: nextCompleted,
        data: { ...state.data, ...patch },
        // currentStep intentionally unchanged — user has not clicked "下一步" yet.
      };
    }
    case "JUMP_TO":
      return {
        ...state,
        currentStep: action.step,
        status: state.completedSteps.includes(action.step) ? "completed" : "idle",
        errorMessage: null,
      };
    case "SET_DIVERGENCE_SUBSTAGE":
      return { ...state, creativeDivergenceSubStage: action.subStage };
    case "JUMP_TO_CREATIVE_DIVERGENCE":
      return {
        ...state,
        currentStep: 1,
        creativeDivergenceSubStage: action.subStage,
      };
    case "STATUS":
      return { ...state, status: action.status, errorMessage: action.errorMessage ?? null };
    case "REGENERATE_BUSY":
      return { ...state, regenerateState: { kind: "busy", target: action.target, startedAt: Date.now() } };
    case "REGENERATE_SUCCESS":
      return { ...state, regenerateState: { kind: "success", target: action.target, at: Date.now() } };
    case "REGENERATE_FAILURE":
      return { ...state, regenerateState: { kind: "failure", target: action.target, message: action.message, at: Date.now() } };
    case "REGENERATE_CLEAR":
      return { ...state, regenerateState: { kind: "idle" } };
    case "RESET":
      return initialState;
    case "HYDRATE":
      return action.state;
    case "UPDATE_DATA":
      // Merge a patch into `data` without touching completedSteps /
      // status / currentStep. Used when a step mutates wizard state in
      // place (e.g., adding an empty power-system card) and wants the
      // mutation visible across step navigation, but does NOT want to
      // mark the step "completed" (which saveStep / markStepGenerated
      // would do).
      return { ...state, data: { ...state.data, ...action.patch } };
    case "HYDRATE_FROM_FILES": {
      const mergedCompleted = Array.from(
        new Set([...state.completedSteps, ...action.completedSteps]),
      ).sort((a, b) => a - b);
      return {
        ...state,
        completedSteps: mergedCompleted,
        data: { ...state.data, ...action.data },
        prefillComplete: true,
      };
    }
    case "HYDRATE_FROM_FILES_AND_ADVANCE": {
      const mergedCompleted = Array.from(
        new Set([...state.completedSteps, ...action.completedSteps]),
      ).sort((a, b) => a - b);
      return {
        ...state,
        completedSteps: mergedCompleted,
        data: { ...state.data, ...action.data },
        currentStep: action.nextStep,
        prefillComplete: true,
      };
    }
    case "PREFLILL_COMPLETE":
      return { ...state, prefillComplete: true };
    case "SET_NEXT_HANDLER":
      return {
        ...state,
        nextHandler: action.handler,
        nextDisabled: action.disabled,
        // Only update label/loadingLabel when explicitly provided — passing
        // undefined would clobber a previously set label on re-registration.
        // Steps that don't customize labels (e.g. S1) leave them at null and
        // the footer falls back to the generic "下一步:拆解 →" / "确认修改并继续".
        nextLabel: action.label !== undefined ? action.label : state.nextLabel,
        nextLoadingLabel:
          action.loadingLabel !== undefined ? action.loadingLabel : state.nextLoadingLabel,
      };
    case "SET_PREV_HANDLER":
      return { ...state, prevHandler: action.handler };
    case "SET_REGENERATE_HANDLER":
      return { ...state, regenerateHandler: action.handler, regenerateDisabled: action.disabled };
    case "SET_SAVE_HANDLER":
      return { ...state, saveHandler: action.handler, saveDisabled: action.disabled };
    case "SET_NEXT_LOADING_CLICK_HANDLER":
      return { ...state, nextLoadingClickHandler: action.handler };
    default:
      return state;
  }
}

function loadPersisted(projectId: string): WizardState | null {
  try {
    const raw = sessionStorage.getItem(getSessionKey(projectId));
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    if (
      typeof parsed.currentStep === "number" &&
      Array.isArray(parsed.completedSteps) &&
      parsed.data
    ) {
      return {
        currentStep: parsed.currentStep,
        completedSteps: parsed.completedSteps,
        status: parsed.status || "idle",
        data: { ...EMPTY_DATA, ...parsed.data },
        errorMessage: parsed.errorMessage || null,
        // Transient — never persist a regenerate status across sessions.
        // "已重新生成" from 10 minutes ago would be stale by definition.
        regenerateState: { kind: "idle" },
        // SessionStorage can be stale (e.g., user closed on step 5 without
        // saving, leaving data.novel_outline=null even though the file exists
        // on disk). Force prefill to run again by setting prefillComplete=false
        // regardless of what sessionStorage held.
        prefillComplete: false,
        nextHandler: null,
        nextDisabled: false,
        nextLabel: null,
        nextLoadingLabel: null,
        nextLoadingClickHandler: null,
        prevHandler: null,
        regenerateHandler: null,
        regenerateDisabled: false,
        saveHandler: null,
        saveDisabled: false,
        // Preserve the divergence sub-step the user was on. Default to "A"
        // if a stale sessionStorage payload predates this field.
        creativeDivergenceSubStage:
          parsed.creativeDivergenceSubStage ?? "A",
      };
    }
    return null;
  } catch {
    return null;
  }
}

interface WizardContextValue extends WizardState {
  startStep: (step: number) => void;
  saveStep: (step: number, patch: Partial<WizardData>) => void;
  /**
   * Mark a step as having generated content (auto-trigger completed). Adds
   * the step to completedSteps, writes the patch to data, and sets
   * status="completed" — but DOES NOT advance currentStep. Use this when
   * the LLM call wrote content directly to disk and the user still has to
   * click "下一步" to confirm (i.e., generated = data on disk, completed =
   * user reviewed & acknowledged).
   */
  markStepGenerated: (step: number, patch: Partial<WizardData>) => void;
  skipStep: (step: number) => void;
  jumpToStep: (step: number) => void;
  setStatus: (status: WizardStatus, errorMessage?: string | null) => void;
  hydrateFromFiles: (completedSteps: number[], data: Partial<WizardData>) => void;
  hydrateFromFilesAndAdvance: (
    completedSteps: number[],
    data: Partial<WizardData>,
    nextStep: number,
  ) => void;
  /**
   * Merge a patch into the wizard's data without touching completedSteps,
   * status, or currentStep. Use when a step has made a local mutation it
   * wants reflected across step navigation (e.g., adding an empty card),
   * but does NOT want to mark the step "completed".
   */
  updateData: (patch: Partial<WizardData>) => void;
  /**
   * Signal that the prefill useEffect has finished running. Use this when
   * prefill found no files to hydrate (the hydrate* actions above already
   * mark prefillComplete=true as a side effect).
   */
  markPrefillComplete: () => void;
  reset: () => void;
  /**
   * Each step registers its "next" and "regenerate" handlers here so the
   * modal footer can render them. Pass null on unmount to clear.
   *
   * `label` / `loadingLabel` are optional; when provided, the modal footer's
   * next button uses them instead of the generic "下一步:拆解 →" / "确认修改并继续"
   * defaults. Used by the divergence wizard's sub-stages (S1/S2/S3/S4) to
   * show stage-specific copy (e.g. "下一步:发散 →" / "拆解中…").
   */
  setNextHandler: (
    handler: (() => void) | null,
    disabled?: boolean,
    label?: string | null,
    loadingLabel?: string | null,
  ) => void;
  /**
   * Register the current step's "previous" action. When set, the modal
   * footer's 上一步 button calls this handler instead of the default
   * `jumpToStep(currentStep - 1)`. Used by divergence sub-stages (S2/S3/S4)
   * to step back to a previous sub-stage without leaving wizard step 1.
   * Pass null on unmount / when the step is the first sub-stage (S1 has no
   * previous sub-stage to return to).
   */
  setPrevHandler: (handler: (() => void) | null) => void;
  setRegenerateHandler: (handler: (() => void) | null, disabled?: boolean) => void;
  /**
   * Register the current step's "save without advancing" action. Like
   * setNextHandler, the modal footer renders the button when this is
   * non-null. Use null when the step has nothing to persist (e.g. MapStep).
   */
  setSaveHandler: (handler: (() => void) | null, disabled?: boolean) => void;
  /**
   * Register a click handler on the footer's loading label (when one is
   * showing). When set, the footer keeps its next button clickable during
   * loading and routes clicks here instead of nextHandler. Used by
   * CreativeDivergenceStep's 暂停 / 继续 toggle. Pass null on unmount /
   * when no loading label is showing.
   */
  setNextLoadingClickHandler: (handler: (() => void) | null) => void;
  /** Update the section-regenerate footer status indicator. */
  setRegenerateBusy: (target: string) => void;
  setRegenerateSuccess: (target: string) => void;
  setRegenerateFailure: (target: string, message: string) => void;
  clearRegenerateState: () => void;
  /**
   * Switch the divergence sub-step (A/B/C/D/E) without leaving step 1.
   * Used by CreativeDivergenceStep's internal "next sub-screen" button.
   */
  setCreativeDivergenceSubStage: (subStage: CreativeDivergenceSubStage) => void;
  /**
   * Navigate to step 1 AND set the divergence sub-step in one atomic action.
   * Used when an outside surface (e.g., a jump-back link in the wizard
   * indicator) wants to land the user at a specific sub-step of step 1.
   */
  jumpToCreativeDivergence: (subStage: CreativeDivergenceSubStage) => void;
}

const WizardContext = createContext<WizardContextValue | null>(null);
// Exported for tests that need to mount a stub provider around isolated
// renders of WizardProvider children (e.g. CreativeDivergenceStep unit tests).
export { WizardContext };

interface WizardProviderProps {
  projectId: string;
  children: ReactNode;
}

export function WizardProvider({ projectId, children }: WizardProviderProps) {
  const [state, dispatch] = useReducer(
    reducer,
    initialState,
    (init) => loadPersisted(projectId) || init
  );
  // Track whether the provider has ever written a non-empty state. Used to
  // tell apart "first mount, nothing happened" (we DO save the empty state
  // so the close button preserves it) from "post-reset" (we DON'T save —
  // reset already cleared sessionStorage, and writing it back undoes the
  // reset). Empty state = initialState shape; non-empty = anything else.
  const hasProgressedRef = useRef(false);

  useEffect(() => {
    const isEmpty =
      state.currentStep === 1 &&
      state.completedSteps.length === 0 &&
      state.status === "idle" &&
      state.errorMessage === null;

    if (isEmpty && hasProgressedRef.current) {
      // Post-reset: the reset() handler cleared sessionStorage synchronously.
      // Don't write the empty state back — that's the bug.
      return;
    }

    if (!isEmpty) {
      hasProgressedRef.current = true;
    }

    try {
      sessionStorage.setItem(
        getSessionKey(projectId),
        JSON.stringify({
          currentStep: state.currentStep,
          completedSteps: state.completedSteps,
          status: state.status,
          data: state.data,
          errorMessage: state.errorMessage,
          creativeDivergenceSubStage: state.creativeDivergenceSubStage,
        })
      );
    } catch {
      // sessionStorage unavailable; ignore.
    }
  }, [projectId, state]);

  const value: WizardContextValue = {
    ...state,
    startStep: (step) => dispatch({ type: "START_STEP", step }),
    saveStep: (step, patch) => dispatch({ type: "STEP_COMPLETED", step, patch }),
    markStepGenerated: (step, patch) => dispatch({ type: "MARK_STEP_GENERATED", step, patch }),
    skipStep: (step) => dispatch({ type: "STEP_SKIPPED", step }),
    jumpToStep: (step) => dispatch({ type: "JUMP_TO", step }),
    setStatus: (status, errorMessage) => dispatch({ type: "STATUS", status, errorMessage }),
    hydrateFromFiles: (completedSteps, data) =>
      dispatch({ type: "HYDRATE_FROM_FILES", completedSteps, data }),
    hydrateFromFilesAndAdvance: (completedSteps, data, nextStep) =>
      dispatch({ type: "HYDRATE_FROM_FILES_AND_ADVANCE", completedSteps, data, nextStep }),
    updateData: (patch) => dispatch({ type: "UPDATE_DATA", patch }),
    markPrefillComplete: () => dispatch({ type: "PREFLILL_COMPLETE" }),
    setNextHandler: (handler, disabled = false, label, loadingLabel) =>
      dispatch({ type: "SET_NEXT_HANDLER", handler, disabled, label, loadingLabel }),
    setPrevHandler: (handler) =>
      dispatch({ type: "SET_PREV_HANDLER", handler }),
    setRegenerateHandler: (handler, disabled = false) =>
      dispatch({ type: "SET_REGENERATE_HANDLER", handler, disabled }),
    setSaveHandler: (handler, disabled = false) =>
      dispatch({ type: "SET_SAVE_HANDLER", handler, disabled }),
    setNextLoadingClickHandler: (handler) =>
      dispatch({ type: "SET_NEXT_LOADING_CLICK_HANDLER", handler }),
    setRegenerateBusy: (target) =>
      dispatch({ type: "REGENERATE_BUSY", target }),
    setRegenerateSuccess: (target) =>
      dispatch({ type: "REGENERATE_SUCCESS", target }),
    setRegenerateFailure: (target, message) =>
      dispatch({ type: "REGENERATE_FAILURE", target, message }),
    clearRegenerateState: () => dispatch({ type: "REGENERATE_CLEAR" }),
    setCreativeDivergenceSubStage: (subStage) =>
      dispatch({ type: "SET_DIVERGENCE_SUBSTAGE", subStage }),
    jumpToCreativeDivergence: (subStage) =>
      dispatch({ type: "JUMP_TO_CREATIVE_DIVERGENCE", subStage }),
    reset: () => {
      try {
        sessionStorage.removeItem(getSessionKey(projectId));
      } catch {
        // ignore
      }
      dispatch({ type: "RESET" });
    },
  };

  return <WizardContext.Provider value={value}>{children}</WizardContext.Provider>;
}

export function useWizard(): WizardContextValue {
  const ctx = useContext(WizardContext);
  if (!ctx) throw new Error("useWizard must be used within WizardProvider");
  return ctx;
}

/**
 * Like useWizard but returns null when no provider is present.
 * Use for components that *optionally* integrate with the wizard footer
 * (e.g. CreativeDivergenceStep registers its "进入拆解" button when it
 * detects a wizard context, and silently no-ops in tests / isolated renders).
 */
export function useOptionalWizard(): WizardContextValue | null {
  return useContext(WizardContext);
}
