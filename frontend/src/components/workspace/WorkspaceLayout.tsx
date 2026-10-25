import { useEffect, useRef, useState, type ReactNode } from "react";
import type { WorkspaceMode } from "../../hooks/useWorkspaceMode";

interface Props {
  mode: WorkspaceMode;
  left: ReactNode;
  center?: ReactNode;
  right: ReactNode;
}

const STORAGE_KEY = "nebula.workspace.column-widths";
const DEFAULT_LEFT = 260;
const DEFAULT_RIGHT = 360;
const MIN_COL = 200;
const MIN_CENTER = 400;
// Pointer-movement threshold above which we treat the gesture as a drag
// (and skip the click → collapse toggle). 2px keeps a clean click usable.
const DRAG_THRESHOLD_PX = 2;

interface CollapsedState {
  left: boolean;
  right: boolean;
}

interface StoredState {
  left: number;
  right: number;
  collapsed: CollapsedState;
}

function loadState(): StoredState {
  const defaults: StoredState = {
    left: DEFAULT_LEFT,
    right: DEFAULT_RIGHT,
    collapsed: { left: false, right: false },
  };
  if (typeof localStorage === "undefined") return defaults;
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return defaults;
    const parsed = JSON.parse(raw);
    const left =
      typeof parsed?.left === "number" && parsed.left >= MIN_COL
        ? parsed.left
        : DEFAULT_LEFT;
    const right =
      typeof parsed?.right === "number" && parsed.right >= MIN_COL
        ? parsed.right
        : DEFAULT_RIGHT;
    const collapsed: CollapsedState = {
      left: Boolean(parsed?.collapsed?.left),
      right: Boolean(parsed?.collapsed?.right),
    };
    return { left, right, collapsed };
  } catch {
    return defaults;
  }
}

function clamp(value: number, min: number, max: number): number {
  return Math.max(min, Math.min(max, value));
}

export default function WorkspaceLayout({ mode, left, center, right }: Props) {
  const initial = useRef(loadState()).current;
  const [widths, setWidths] = useState<{ left: number; right: number }>({
    left: initial.left,
    right: initial.right,
  });
  const [collapsed, setCollapsed] = useState<CollapsedState>(initial.collapsed);

  const containerRef = useRef<HTMLDivElement>(null);
  const widthsRef = useRef(widths);
  const dragState = useRef<{
    side: "left" | "right";
    startX: number;
    startLeft: number;
    startRight: number;
    containerWidth: number;
  } | null>(null);
  // Tracks whether the current pointer gesture has moved enough to count as
  // a drag. The handle's onClick checks this ref so a drag never accidentally
  // collapses the column at mouseup time.
  const dragOccurredRef = useRef(false);

  // Keep ref in sync so the global mousemove handler always reads the
  // latest widths without re-attaching listeners on every state change.
  useEffect(() => {
    widthsRef.current = widths;
  }, [widths]);

  // Persist widths + collapsed together (single localStorage write so a
  // reload sees a consistent snapshot).
  useEffect(() => {
    try {
      localStorage.setItem(
        STORAGE_KEY,
        JSON.stringify({ left: widths.left, right: widths.right, collapsed }),
      );
    } catch {
      // localStorage unavailable (SSR, quota) — non-fatal.
    }
  }, [widths, collapsed]);

  const toggleCollapse = (side: "left" | "right") => {
    setCollapsed((prev) => ({ ...prev, [side]: !prev[side] }));
  };

  const onHandleClick = (side: "left" | "right") => () => {
    if (dragOccurredRef.current) return;
    toggleCollapse(side);
  };

  const startDrag = (side: "left" | "right") => (e: React.MouseEvent) => {
    e.preventDefault();
    const container = containerRef.current;
    if (!container) return;
    const rect = container.getBoundingClientRect();
    dragState.current = {
      side,
      startX: e.clientX,
      startLeft: widthsRef.current.left,
      startRight: widthsRef.current.right,
      containerWidth: rect.width,
    };
    dragOccurredRef.current = false;
    document.body.style.cursor = "col-resize";
    document.body.style.userSelect = "none";
  };

  useEffect(() => {
    const onMove = (ev: MouseEvent) => {
      const s = dragState.current;
      if (!s) return;
      if (Math.abs(ev.clientX - s.startX) > DRAG_THRESHOLD_PX) {
        dragOccurredRef.current = true;
      }
      const dx = ev.clientX - s.startX;
      const minCenter = mode === "manual" ? MIN_CENTER : 0;
      if (s.side === "left") {
        const maxLeft = s.containerWidth - widthsRef.current.right - minCenter;
        setWidths((w) => ({
          ...w,
          left: clamp(s.startLeft + dx, MIN_COL, Math.max(MIN_COL, maxLeft)),
        }));
      } else {
        const maxRight = s.containerWidth - widthsRef.current.left - minCenter;
        setWidths((w) => ({
          ...w,
          right: clamp(s.startRight - dx, MIN_COL, Math.max(MIN_COL, maxRight)),
        }));
      }
    };
    const onUp = () => {
      if (!dragState.current) return;
      dragState.current = null;
      document.body.style.cursor = "";
      document.body.style.userSelect = "";
    };
    document.addEventListener("mousemove", onMove);
    document.addEventListener("mouseup", onUp);
    return () => {
      document.removeEventListener("mousemove", onMove);
      document.removeEventListener("mouseup", onUp);
      document.body.style.cursor = "";
      document.body.style.userSelect = "";
    };
  }, [mode]);

  const renderHandle = (side: "left" | "right") => {
    if (collapsed[side]) {
      const chevron = side === "left" ? "›" : "‹";
      const label = side === "left" ? "展开左栏" : "展开右栏";
      return (
        <button
          type="button"
          data-testid={`collapse-rail-${side}`}
          aria-label={label}
          title={label}
          onClick={() => toggleCollapse(side)}
          className="w-6 bg-surface-container hover:bg-surface-container-high shrink-0 flex items-center justify-center text-system-log cursor-pointer border-y border-outline-variant"
        >
          <span className="text-xs">{chevron}</span>
        </button>
      );
    }
    const chevron = side === "left" ? "‹" : "›";
    const label = side === "left" ? "收起左栏" : "收起右栏";
    return (
      <div
        role="separator"
        aria-orientation="vertical"
        aria-label={label}
        title={label}
        data-testid={`resize-handle-${side}`}
        onMouseDown={startDrag(side)}
        onClick={onHandleClick(side)}
        className="w-1 bg-outline-variant hover:bg-primary cursor-col-resize shrink-0 transition-colors group flex items-center justify-center"
      >
        <span className="opacity-0 group-hover:opacity-100 text-[10px] text-system-log select-none">
          {chevron}
        </span>
      </div>
    );
  };

  const effectiveLeftWidth = collapsed.left ? 0 : widths.left;
  const effectiveRightWidth = collapsed.right ? 0 : widths.right;

  if (mode === "managed") {
    return (
      <div
        ref={containerRef}
        data-testid="workspace-layout"
        data-mode="managed"
        className="flex-1 flex overflow-hidden"
      >
        <div
          data-testid="left-column"
          style={{ width: effectiveLeftWidth }}
          className="h-full overflow-y-auto shrink-0"
        >
          {left}
        </div>
        {renderHandle("left")}
        {center && (
          <main
            data-testid="center-column"
            className="flex-1 overflow-hidden min-w-0"
          >
            {center}
          </main>
        )}
        {renderHandle("right")}
        <aside
          data-testid="right-column"
          style={{ width: effectiveRightWidth }}
          className="h-full overflow-y-auto shrink-0"
        >
          {right}
        </aside>
      </div>
    );
  }

  return (
    <div
      ref={containerRef}
      data-testid="workspace-layout"
      data-mode="manual"
      className="flex-1 flex overflow-hidden"
    >
      <aside
        data-testid="left-column"
        style={{ width: effectiveLeftWidth }}
        className="h-full overflow-y-auto shrink-0"
      >
        {left}
      </aside>
      {renderHandle("left")}
      <main
        data-testid="center-column"
        className="flex-1 overflow-y-auto min-w-0"
      >
        {center}
      </main>
      {renderHandle("right")}
      <aside
        data-testid="right-column"
        style={{ width: effectiveRightWidth }}
        className="h-full overflow-y-auto shrink-0"
      >
        {right}
      </aside>
    </div>
  );
}