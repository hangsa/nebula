import { describe, it, expect, beforeEach } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { useTheme } from "../hooks/useTheme";

const STORAGE_KEY = "nebula.theme";

beforeEach(() => {
  localStorage.removeItem(STORAGE_KEY);
  document.documentElement.classList.remove("light");
});

describe("useTheme", () => {
  it("defaults to 'dark' when no value is persisted", () => {
    const { result } = renderHook(() => useTheme());
    expect(result.current[0]).toBe("dark");
  });

  it("returns 'light' when localStorage holds 'light'", () => {
    localStorage.setItem(STORAGE_KEY, "light");
    const { result } = renderHook(() => useTheme());
    expect(result.current[0]).toBe("light");
  });

  it("falls back to 'dark' on an invalid persisted value", () => {
    localStorage.setItem(STORAGE_KEY, "neon-pink");
    const { result } = renderHook(() => useTheme());
    expect(result.current[0]).toBe("dark");
  });

  it("setter persists to localStorage and updates state", () => {
    const { result } = renderHook(() => useTheme());
    act(() => result.current[1]("light"));
    expect(result.current[0]).toBe("light");
    expect(localStorage.getItem(STORAGE_KEY)).toBe("light");
  });

  it("applies .light class to <html> when theme is 'light'", () => {
    const { result } = renderHook(() => useTheme());
    act(() => result.current[1]("light"));
    expect(document.documentElement.classList.contains("light")).toBe(true);
  });

  it("removes .light class from <html> when theme is 'dark'", () => {
    document.documentElement.classList.add("light");
    const { result } = renderHook(() => useTheme());
    act(() => result.current[1]("dark"));
    expect(document.documentElement.classList.contains("light")).toBe(false);
  });

  it("reconciles the .light class on mount to match the persisted value", () => {
    // Simulate a stale state: localStorage says light, but the document class
    // wasn't applied (e.g. previous render crashed before the effect ran).
    localStorage.setItem(STORAGE_KEY, "light");
    document.documentElement.classList.remove("light");
    renderHook(() => useTheme());
    expect(document.documentElement.classList.contains("light")).toBe(true);
  });
});