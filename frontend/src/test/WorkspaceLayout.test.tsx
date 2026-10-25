import { describe, it, expect, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import WorkspaceLayout from "../components/workspace/WorkspaceLayout";

const STORAGE_KEY = "nebula.workspace.column-widths";

// JSDOM doesn't compute layout — stub getBoundingClientRect so the resize
// handler sees a known container width (2000px — wide enough for tests to
// drag left column past 400px without bumping into minCenter constraints).
beforeEach(() => {
  localStorage.clear();
  HTMLElement.prototype.getBoundingClientRect = function (this: HTMLElement) {
    return {
      left: 0, top: 0, right: 2000, bottom: 500,
      x: 0, y: 0, width: 2000, height: 500,
      toJSON: () => ({}),
    };
  };
});

describe("WorkspaceLayout", () => {
  it("renders managed slots (left, center, right) when mode=managed", () => {
    // v1.9 alignment with plotPilot: managed mode now centers on the
    // autopilot workspace (cockpit/dashboard/log). WorkspaceLayout should
    // render the center column whenever it's provided, regardless of mode.
    render(
      <WorkspaceLayout
        mode="managed"
        left={<div data-testid="l">L</div>}
        center={<div data-testid="c">C</div>}
        right={<div data-testid="r">R</div>}
      />,
    );
    expect(screen.getByTestId("l")).toBeInTheDocument();
    expect(screen.getByTestId("c")).toBeInTheDocument();
    expect(screen.getByTestId("r")).toBeInTheDocument();
  });

  it("managed mode without a center slot still renders left + right", () => {
    render(
      <WorkspaceLayout
        mode="managed"
        left={<div data-testid="l">L</div>}
        right={<div data-testid="r">R</div>}
      />,
    );
    expect(screen.getByTestId("l")).toBeInTheDocument();
    expect(screen.queryByTestId("c")).not.toBeInTheDocument();
    expect(screen.getByTestId("r")).toBeInTheDocument();
  });

  it("renders manual slots (left, center, right) when mode=manual", () => {
    render(
      <WorkspaceLayout
        mode="manual"
        left={<div data-testid="l">L</div>}
        center={<div data-testid="c">C</div>}
        right={<div data-testid="r">R</div>}
      />,
    );
    expect(screen.getByTestId("l")).toBeInTheDocument();
    expect(screen.getByTestId("c")).toBeInTheDocument();
    expect(screen.getByTestId("r")).toBeInTheDocument();
  });

  describe("draggable column borders", () => {
    it("manual mode renders two resize handles (left-center and center-right)", () => {
      render(
        <WorkspaceLayout
          mode="manual"
          left={<div>L</div>}
          center={<div>C</div>}
          right={<div>R</div>}
        />,
      );
      expect(screen.getByTestId("resize-handle-left")).toBeInTheDocument();
      expect(screen.getByTestId("resize-handle-right")).toBeInTheDocument();
    });

    it("managed mode renders resize handles for both side columns", () => {
      render(
        <WorkspaceLayout
          mode="managed"
          left={<div>L</div>}
          right={<div>R</div>}
        />,
      );
      expect(screen.getByTestId("resize-handle-left")).toBeInTheDocument();
      expect(screen.getByTestId("resize-handle-right")).toBeInTheDocument();
    });

    it("default widths: left=260, right=360 (no localStorage)", () => {
      render(
        <WorkspaceLayout
          mode="manual"
          left={<div>L</div>}
          center={<div>C</div>}
          right={<div>R</div>}
        />,
      );
      expect((screen.getByTestId("left-column") as HTMLElement).style.width).toBe("260px");
      expect((screen.getByTestId("right-column") as HTMLElement).style.width).toBe("360px");
    });

    it("dragging the left handle right increases the left column width", () => {
      render(
        <WorkspaceLayout
          mode="manual"
          left={<div>L</div>}
          center={<div>C</div>}
          right={<div>R</div>}
        />,
      );
      const handle = screen.getByTestId("resize-handle-left");
      const leftCol = screen.getByTestId("left-column") as HTMLElement;
      expect(leftCol.style.width).toBe("260px");
      fireEvent.mouseDown(handle, { clientX: 260 });
      fireEvent.mouseMove(document, { clientX: 400 });
      fireEvent.mouseUp(document);
      expect(parseInt(leftCol.style.width)).toBe(400);
    });

    it("width persists to localStorage after a drag", () => {
      render(
        <WorkspaceLayout
          mode="manual"
          left={<div>L</div>}
          center={<div>C</div>}
          right={<div>R</div>}
        />,
      );
      fireEvent.mouseDown(screen.getByTestId("resize-handle-left"), { clientX: 260 });
      fireEvent.mouseMove(document, { clientX: 400 });
      fireEvent.mouseUp(document);
      const stored = JSON.parse(localStorage.getItem(STORAGE_KEY)!);
      expect(stored.left).toBe(400);
    });

    it("widths are restored from localStorage on mount", () => {
      localStorage.setItem(STORAGE_KEY, JSON.stringify({ left: 500, right: 400 }));
      render(
        <WorkspaceLayout
          mode="manual"
          left={<div>L</div>}
          center={<div>C</div>}
          right={<div>R</div>}
        />,
      );
      expect((screen.getByTestId("left-column") as HTMLElement).style.width).toBe("500px");
      expect((screen.getByTestId("right-column") as HTMLElement).style.width).toBe("400px");
    });

    it("left column width does not shrink below min (200px)", () => {
      render(
        <WorkspaceLayout
          mode="manual"
          left={<div>L</div>}
          center={<div>C</div>}
          right={<div>R</div>}
        />,
      );
      fireEvent.mouseDown(screen.getByTestId("resize-handle-left"), { clientX: 260 });
      fireEvent.mouseMove(document, { clientX: 0 });
      fireEvent.mouseUp(document);
      expect((screen.getByTestId("left-column") as HTMLElement).style.width).toBe("200px");
    });
  });

  describe("one-click column hide", () => {
    it("clicking the left handle collapses the left column and renders a rail", () => {
      render(
        <WorkspaceLayout
          mode="manual"
          left={<div>L</div>}
          center={<div>C</div>}
          right={<div>R</div>}
        />,
      );
      fireEvent.click(screen.getByTestId("resize-handle-left"));
      expect(screen.getByTestId("collapse-rail-left")).toBeInTheDocument();
      expect(screen.queryByTestId("resize-handle-left")).not.toBeInTheDocument();
      expect((screen.getByTestId("left-column") as HTMLElement).style.width).toBe("0px");
    });

    it("clicking the right handle collapses the right column", () => {
      render(
        <WorkspaceLayout
          mode="manual"
          left={<div>L</div>}
          center={<div>C</div>}
          right={<div>R</div>}
        />,
      );
      fireEvent.click(screen.getByTestId("resize-handle-right"));
      expect(screen.getByTestId("collapse-rail-right")).toBeInTheDocument();
      expect(screen.queryByTestId("resize-handle-right")).not.toBeInTheDocument();
      expect((screen.getByTestId("right-column") as HTMLElement).style.width).toBe("0px");
    });

    it("clicking the left rail expands the left column back to its width", () => {
      render(
        <WorkspaceLayout
          mode="manual"
          left={<div>L</div>}
          center={<div>C</div>}
          right={<div>R</div>}
        />,
      );
      fireEvent.click(screen.getByTestId("resize-handle-left"));
      expect(screen.getByTestId("collapse-rail-left")).toBeInTheDocument();
      fireEvent.click(screen.getByTestId("collapse-rail-left"));
      expect(screen.getByTestId("resize-handle-left")).toBeInTheDocument();
      expect(screen.queryByTestId("collapse-rail-left")).not.toBeInTheDocument();
      expect((screen.getByTestId("left-column") as HTMLElement).style.width).toBe("260px");
    });

    it("dragging does not trigger collapse (drag guard)", () => {
      render(
        <WorkspaceLayout
          mode="manual"
          left={<div>L</div>}
          center={<div>C</div>}
          right={<div>R</div>}
        />,
      );
      const handle = screen.getByTestId("resize-handle-left");
      fireEvent.mouseDown(handle, { clientX: 260 });
      fireEvent.mouseMove(document, { clientX: 400 });
      fireEvent.mouseUp(document);
      // Handle still rendered (not collapsed), no rail.
      expect(screen.getByTestId("resize-handle-left")).toBeInTheDocument();
      expect(screen.queryByTestId("collapse-rail-left")).not.toBeInTheDocument();
    });

    it("collapsed state persists to localStorage", () => {
      render(
        <WorkspaceLayout
          mode="manual"
          left={<div>L</div>}
          center={<div>C</div>}
          right={<div>R</div>}
        />,
      );
      fireEvent.click(screen.getByTestId("resize-handle-left"));
      const stored = JSON.parse(localStorage.getItem(STORAGE_KEY)!);
      expect(stored.collapsed.left).toBe(true);
      expect(stored.collapsed.right).toBe(false);
    });

    it("collapsed state restores from localStorage on mount", () => {
      localStorage.setItem(
        STORAGE_KEY,
        JSON.stringify({ left: 260, right: 360, collapsed: { left: true, right: false } }),
      );
      render(
        <WorkspaceLayout
          mode="manual"
          left={<div>L</div>}
          center={<div>C</div>}
          right={<div>R</div>}
        />,
      );
      expect(screen.getByTestId("collapse-rail-left")).toBeInTheDocument();
      expect(screen.getByTestId("resize-handle-right")).toBeInTheDocument();
    });
  });
});