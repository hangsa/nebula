import { describe, it, expect, beforeEach, vi } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { useSidebar } from "../../hooks/useSidebar";

const STORAGE_KEY = "nebula.sidebar";

describe("useSidebar - initialization", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it("returns default state when localStorage is empty", () => {
    const { result } = renderHook(() => useSidebar());
    expect(result.current.collapsed).toBe(false);
    expect(result.current.width).toBe(280);
    expect(result.current.MIN).toBe(200);
    expect(result.current.MAX).toBe(480);
  });

  it("restores state from localStorage", () => {
    localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({ collapsed: true, width: 320 })
    );
    const { result } = renderHook(() => useSidebar());
    expect(result.current.collapsed).toBe(true);
    expect(result.current.width).toBe(320);
  });

  it("clamps width below MIN", () => {
    localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({ collapsed: false, width: 100 })
    );
    const { result } = renderHook(() => useSidebar());
    expect(result.current.width).toBe(200);
  });

  it("clamps width above MAX", () => {
    localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({ collapsed: false, width: 999 })
    );
    const { result } = renderHook(() => useSidebar());
    expect(result.current.width).toBe(480);
  });

  it("falls back to default when JSON is malformed", () => {
    localStorage.setItem(STORAGE_KEY, "not-json{");
    const { result } = renderHook(() => useSidebar());
    expect(result.current.collapsed).toBe(false);
    expect(result.current.width).toBe(280);
  });

  it("falls back to default when value is not an object", () => {
    localStorage.setItem(STORAGE_KEY, JSON.stringify("hello"));
    const { result } = renderHook(() => useSidebar());
    expect(result.current.collapsed).toBe(false);
    expect(result.current.width).toBe(280);
  });

  it("falls back when width field is missing or wrong type", () => {
    localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({ collapsed: true, width: "abc" })
    );
    const { result } = renderHook(() => useSidebar());
    expect(result.current.width).toBe(280);
  });

  it("falls back when getItem throws (e.g. private mode)", () => {
    const original = Storage.prototype.getItem;
    Storage.prototype.getItem = vi.fn(() => {
      throw new Error("QuotaExceeded");
    });
    try {
      const { result } = renderHook(() => useSidebar());
      expect(result.current.collapsed).toBe(false);
      expect(result.current.width).toBe(280);
    } finally {
      Storage.prototype.getItem = original;
    }
  });
});

describe("useSidebar - mutations", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it("toggle flips collapsed and persists", () => {
    const { result } = renderHook(() => useSidebar());
    expect(result.current.collapsed).toBe(false);
    act(() => {
      result.current.toggle();
    });
    expect(result.current.collapsed).toBe(true);
    const stored = JSON.parse(localStorage.getItem(STORAGE_KEY)!);
    expect(stored).toEqual({ collapsed: true, width: 280 });

    act(() => {
      result.current.toggle();
    });
    expect(result.current.collapsed).toBe(false);
    const stored2 = JSON.parse(localStorage.getItem(STORAGE_KEY)!);
    expect(stored2.collapsed).toBe(false);
  });

  it("toggle persists current width, not stale", () => {
    localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({ collapsed: false, width: 280 })
    );
    const { result } = renderHook(() => useSidebar());
    act(() => {
      result.current.commitWidth(350);
    });
    expect(result.current.width).toBe(350);
    act(() => {
      result.current.toggle();
    });
    const stored = JSON.parse(localStorage.getItem(STORAGE_KEY)!);
    expect(stored.width).toBe(350);
  });

  it("setWidthLive updates state but does NOT persist", () => {
    const setItemSpy = vi.spyOn(Storage.prototype, "setItem");
    const { result } = renderHook(() => useSidebar());
    act(() => {
      result.current.setWidthLive(400);
    });
    expect(result.current.width).toBe(400);
    expect(setItemSpy).not.toHaveBeenCalled();
    setItemSpy.mockRestore();
  });

  it("commitWidth clamps to MIN/MAX and persists", () => {
    const setItemSpy = vi.spyOn(Storage.prototype, "setItem");
    const { result } = renderHook(() => useSidebar());

    act(() => {
      result.current.commitWidth(50);
    });
    expect(result.current.width).toBe(200);
    expect(setItemSpy).toHaveBeenLastCalledWith(
      STORAGE_KEY,
      JSON.stringify({ collapsed: false, width: 200 })
    );

    act(() => {
      result.current.commitWidth(999);
    });
    expect(result.current.width).toBe(480);
    expect(setItemSpy).toHaveBeenLastCalledWith(
      STORAGE_KEY,
      JSON.stringify({ collapsed: false, width: 480 })
    );

    setItemSpy.mockRestore();
  });
});
