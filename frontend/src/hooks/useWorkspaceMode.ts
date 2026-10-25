import { useCallback } from "react";
import { useSearchParams } from "react-router-dom";

export type WorkspaceMode = "managed" | "manual";
const STORAGE_KEY = "nebula.workspace.mode";
const VALID: WorkspaceMode[] = ["managed", "manual"];

function isValid(v: string | null): v is WorkspaceMode {
  return v !== null && (VALID as string[]).includes(v);
}

export function useWorkspaceMode(): {
  mode: WorkspaceMode;
  setMode: (m: WorkspaceMode) => void;
} {
  const [params, setParams] = useSearchParams();
  const urlMode = params.get("mode");
  const stored = (() => {
    try {
      return localStorage.getItem(STORAGE_KEY);
    } catch {
      return null;
    }
  })();

  // v1.9: default is "manual" so multiple workspace entry points (bookshelf,
  // wizard completion, init page) all land the user on the chapter tree +
  // writing area. Users opt into managed mode (autopilot) explicitly via the
  // top-bar switcher.
  const mode: WorkspaceMode = isValid(urlMode)
    ? urlMode
    : isValid(stored)
    ? (stored as WorkspaceMode)
    : "manual";

  const setMode = useCallback(
    (m: WorkspaceMode) => {
      try {
        localStorage.setItem(STORAGE_KEY, m);
      } catch {}
      const next = new URLSearchParams(params);
      next.set("mode", m);
      setParams(next, { replace: true });
    },
    [params, setParams],
  );

  return { mode, setMode };
}