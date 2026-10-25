import { describe, it, expect, beforeEach } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { useWorkspaceMode } from "../hooks/useWorkspaceMode";

function wrap(initialPath = "/workspace") {
  return ({ children }: { children: React.ReactNode }) => (
    <MemoryRouter initialEntries={[initialPath]}>{children}</MemoryRouter>
  );
}

beforeEach(() => {
  sessionStorage.clear();
  localStorage.clear();
});

describe("useWorkspaceMode", () => {
  // v1.9: workspace defaults to manual mode so the chapter tree + writing
  // area is what the user lands on — they explicitly opt into managed
  // (autopilot) via the top-bar switcher when they're ready.
  it("defaults to 'manual' when URL has no mode and no localStorage", () => {
    const { result } = renderHook(() => useWorkspaceMode(), { wrapper: wrap() });
    expect(result.current.mode).toBe("manual");
  });

  it("reads mode from URL when ?mode=manual", () => {
    const { result } = renderHook(() => useWorkspaceMode(), {
      wrapper: wrap("/workspace?mode=manual"),
    });
    expect(result.current.mode).toBe("manual");
  });

  it("falls back to localStorage when URL omits mode", () => {
    localStorage.setItem("nebula.workspace.mode", "manual");
    const { result } = renderHook(() => useWorkspaceMode(), { wrapper: wrap() });
    expect(result.current.mode).toBe("manual");
  });

  it("URL takes precedence over localStorage", () => {
    localStorage.setItem("nebula.workspace.mode", "managed");
    const { result } = renderHook(() => useWorkspaceMode(), {
      wrapper: wrap("/workspace?mode=manual"),
    });
    expect(result.current.mode).toBe("manual");
  });

  it("setMode('managed') updates URL and localStorage", () => {
    const { result } = renderHook(() => useWorkspaceMode(), {
      wrapper: wrap("/workspace?mode=manual"),
    });
    act(() => result.current.setMode("managed"));
    expect(result.current.mode).toBe("managed");
    expect(localStorage.getItem("nebula.workspace.mode")).toBe("managed");
  });

  it("falls back to 'manual' if ?mode=garbage", () => {
    const { result } = renderHook(() => useWorkspaceMode(), {
      wrapper: wrap("/workspace?mode=robot"),
    });
    expect(result.current.mode).toBe("manual");
  });
});