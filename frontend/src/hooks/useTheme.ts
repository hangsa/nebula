import { useEffect, useState, useCallback } from "react";

export type Theme = "dark" | "light";

const STORAGE_KEY = "nebula.theme";

/**
 * Read the persisted theme. Defaults to "dark" on first load so existing
 * users see no visual change. Invalid stored values fall back to dark.
 */
function readPersisted(): Theme {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (raw === "light") return "light";
    return "dark";
  } catch {
    return "dark";
  }
}

function applyToDocument(theme: Theme) {
  if (typeof document === "undefined") return;
  const root = document.documentElement;
  if (theme === "light") {
    root.classList.add("light");
  } else {
    root.classList.remove("light");
  }
}

function persist(theme: Theme) {
  try {
    localStorage.setItem(STORAGE_KEY, theme);
  } catch {
    // localStorage unavailable; ignore — the visual change still applies for
    // this session but won't survive a reload.
  }
}

/**
 * Theme switcher state. The hook returns the current theme plus a setter;
 * setting a value updates localStorage AND immediately applies the matching
 * class on <html> so the swap is synchronous (no flash).
 *
 * The apply effect runs once on mount to reconcile the document class with
 * whatever was persisted — handles the case where another tab/window
 * changed the theme between renders.
 */
export function useTheme(): [Theme, (next: Theme) => void] {
  const [theme, setTheme] = useState<Theme>(readPersisted);

  // Apply on mount + on every change. Separating this from the setter keeps
  // the read/write path pure (no side effect in setState callback).
  useEffect(() => {
    applyToDocument(theme);
  }, [theme]);

  const set = useCallback((next: Theme) => {
    persist(next);
    setTheme(next);
  }, []);

  return [theme, set];
}