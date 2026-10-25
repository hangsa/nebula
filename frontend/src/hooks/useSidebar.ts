import { useState, useCallback } from "react";

const STORAGE_KEY = "nebula.sidebar";
const DEFAULT_WIDTH = 280;
const MIN = 200;
const MAX = 480;

interface PersistedState {
  collapsed: boolean;
  width: number;
}

function loadFromStorage(): PersistedState {
  const fallback: PersistedState = { collapsed: false, width: DEFAULT_WIDTH };
  let raw: string | null = null;
  try {
    raw = localStorage.getItem(STORAGE_KEY);
  } catch {
    return fallback;
  }
  if (!raw) return fallback;
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return fallback;
  }
  if (typeof parsed !== "object" || parsed === null) return fallback;
  const obj = parsed as Record<string, unknown>;
  const widthNum = typeof obj.width === "number" ? obj.width : DEFAULT_WIDTH;
  const collapsedBool =
    typeof obj.collapsed === "boolean" ? obj.collapsed : false;
  const clampedWidth = Math.max(MIN, Math.min(MAX, widthNum));
  return { collapsed: collapsedBool, width: clampedWidth };
}

function saveToStorage(state: PersistedState): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
  } catch {
    // ignore
  }
}

export interface UseSidebarReturn {
  collapsed: boolean;
  width: number;
  MIN: number;
  MAX: number;
  setWidthLive: (w: number) => void;
  commitWidth: (w: number) => void;
  toggle: () => void;
}

function clamp(w: number): number {
  return Math.max(MIN, Math.min(MAX, w));
}

export function useSidebar(): UseSidebarReturn {
  const initial = loadFromStorage();
  const [collapsed, setCollapsed] = useState<boolean>(initial.collapsed);
  const [width, setWidth] = useState<number>(initial.width);

  const setWidthLive = useCallback((w: number) => {
    setWidth(clamp(w));
  }, []);

  const commitWidth = useCallback(
    (w: number) => {
      const next = clamp(w);
      setWidth(next);
      saveToStorage({ collapsed, width: next });
    },
    [collapsed]
  );

  const toggle = useCallback(() => {
    setCollapsed((prev) => {
      const next = !prev;
      saveToStorage({ collapsed: next, width });
      return next;
    });
  }, [width]);

  return {
    collapsed,
    width,
    MIN,
    MAX,
    setWidthLive,
    commitWidth,
    toggle,
  };
}
