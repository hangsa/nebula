import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import WritingArea from "../components/workspace/WritingArea";

const CURRENT = {
  chapter_number: 1,
  chapter_title: "第一章",
  scene_id: "1-1",
  scene_title: "开场",
  outline_summary: "主角踏上旅途",
};

const STRUCTURED_CURRENT = {
  chapter_number: 1,
  chapter_title: "第一章",
  chapter_theme: "主角踏上旅途",
  scene_id: "1-1",
  scene_title: "开场",
  scene_goal: "主角遇到师父",
  scene_conflict: "内心挣扎是否离开家乡",
  scene_emotional_arc: "期待 → 恐惧",
  scene_narrative_role: "setup",
  scene_beat_type: "inciting",
};

beforeEach(() => {
  localStorage.clear();
});

describe("WritingArea", () => {
  it("renders chapter + scene title and outline summary", () => {
    render(
      <WritingArea
        current={CURRENT}
        content="已写的场景文本"
        onContentChange={() => {}}
        onSaveDraft={async () => {}}
        onRegenerate={async () => {}}
        onFactGuard={async () => {}}
        busy={false}
      />,
    );
    expect(screen.getByTestId("writing-chapter-title").textContent).toContain("第一章");
    expect(screen.getByTestId("writing-scene-title").textContent).toContain("开场");
    expect(screen.getByTestId("writing-outline-summary").textContent).toContain("主角踏上旅途");
  });

  it("typing into the editor calls onContentChange", () => {
    const onContentChange = vi.fn();
    render(
      <WritingArea
        current={CURRENT}
        content=""
        onContentChange={onContentChange}
        onSaveDraft={async () => {}}
        onRegenerate={async () => {}}
        onFactGuard={async () => {}}
        busy={false}
      />,
    );
    fireEvent.change(screen.getByTestId("editor-body"), { target: { value: "新的文本" } });
    expect(onContentChange).toHaveBeenCalledWith("新的文本");
  });

  it("保存草稿 button calls onSaveDraft", () => {
    const onSaveDraft = vi.fn().mockResolvedValue(undefined);
    render(
      <WritingArea
        current={CURRENT}
        content="x"
        onContentChange={() => {}}
        onSaveDraft={onSaveDraft}
        onRegenerate={async () => {}}
        onFactGuard={async () => {}}
        busy={false}
      />,
    );
    fireEvent.click(screen.getByTestId("editor-save"));
    expect(onSaveDraft).toHaveBeenCalled();
  });

  it("当 current=null 时显示 empty-state：'本章节尚未生成大纲' + '前往生成大纲' 按钮", () => {
    const onNavigateToOutline = vi.fn();
    render(
      <WritingArea
        current={null}
        content=""
        onContentChange={() => {}}
        onSaveDraft={async () => {}}
        onRegenerate={async () => {}}
        onFactGuard={async () => {}}
        busy={false}
        onNavigateToOutline={onNavigateToOutline}
      />,
    );
    expect(screen.getByTestId("writing-empty")).toBeInTheDocument();
    expect(screen.getByText(/本章节尚未生成大纲/)).toBeInTheDocument();
    const btn = screen.getByTestId("writing-empty-go-to-outline");
    fireEvent.click(btn);
    expect(onNavigateToOutline).toHaveBeenCalled();
  });

  it("empty-state 在未提供 onNavigateToOutline 时不渲染按钮", () => {
    render(
      <WritingArea
        current={null}
        content=""
        onContentChange={() => {}}
        onSaveDraft={async () => {}}
        onRegenerate={async () => {}}
        onFactGuard={async () => {}}
        busy={false}
      />,
    );
    expect(screen.queryByTestId("writing-empty-go-to-outline")).not.toBeInTheDocument();
  });

  // v1.8 expansion: structured scene outline (theme/goal/conflict/
  // emotional_arc) renders as labeled rows in the writing-area header so the
  // user sees the full context above the editor, not a single line.
  it("renders the structured scene-outline block when all fields are present", () => {
    render(
      <WritingArea
        current={STRUCTURED_CURRENT}
        content=""
        onContentChange={() => {}}
        onSaveDraft={async () => {}}
        onRegenerate={async () => {}}
        onFactGuard={async () => {}}
        busy={false}
      />,
    );
    expect(screen.getByTestId("writing-outline-block")).toBeInTheDocument();
    expect(screen.getByTestId("writing-chapter-theme")).toHaveTextContent("主角踏上旅途");
    expect(screen.getByTestId("writing-scene-goal")).toHaveTextContent("主角遇到师父");
    expect(screen.getByTestId("writing-scene-conflict")).toHaveTextContent("内心挣扎是否离开家乡");
    expect(screen.getByTestId("writing-scene-emotional-arc")).toHaveTextContent("期待 → 恐惧");
    expect(screen.getByTestId("writing-scene-role")).toHaveTextContent("setup");
    expect(screen.getByTestId("writing-scene-role").textContent).toContain("inciting");
  });

  it("does not render the outline-block when no outline data is present", () => {
    render(
      <WritingArea
        current={{
          chapter_number: 1,
          chapter_title: "第一章",
          scene_id: "1-1",
          scene_title: "开场",
        }}
        content=""
        onContentChange={() => {}}
        onSaveDraft={async () => {}}
        onRegenerate={async () => {}}
        onFactGuard={async () => {}}
        busy={false}
      />,
    );
    expect(screen.queryByTestId("writing-outline-block")).not.toBeInTheDocument();
  });

  // 大纲信息块收起/展开：默认展开，点击切换后会写入 localStorage，
  // 下次进入时从 localStorage 恢复。
  it("大纲信息块默认展开，渲染所有 rows", () => {
    render(
      <WritingArea
        current={STRUCTURED_CURRENT}
        content=""
        onContentChange={() => {}}
        onSaveDraft={async () => {}}
        onRegenerate={async () => {}}
        onFactGuard={async () => {}}
        busy={false}
      />,
    );
    expect(screen.getByTestId("writing-chapter-theme")).toBeInTheDocument();
    expect(screen.getByTestId("writing-outline-toggle").getAttribute("aria-expanded")).toBe("true");
  });

  it("点击切换按钮收起大纲信息块，再次点击展开", () => {
    render(
      <WritingArea
        current={STRUCTURED_CURRENT}
        content=""
        onContentChange={() => {}}
        onSaveDraft={async () => {}}
        onRegenerate={async () => {}}
        onFactGuard={async () => {}}
        busy={false}
      />,
    );
    const toggle = screen.getByTestId("writing-outline-toggle");
    expect(toggle.getAttribute("aria-expanded")).toBe("true");

    fireEvent.click(toggle);
    expect(screen.queryByTestId("writing-chapter-theme")).not.toBeInTheDocument();
    expect(toggle.getAttribute("aria-expanded")).toBe("false");

    fireEvent.click(toggle);
    expect(screen.getByTestId("writing-chapter-theme")).toBeInTheDocument();
    expect(toggle.getAttribute("aria-expanded")).toBe("true");
  });

  it("收起状态写入 localStorage（key=nebula.workspace.outline-collapsed）", () => {
    render(
      <WritingArea
        current={STRUCTURED_CURRENT}
        content=""
        onContentChange={() => {}}
        onSaveDraft={async () => {}}
        onRegenerate={async () => {}}
        onFactGuard={async () => {}}
        busy={false}
      />,
    );
    fireEvent.click(screen.getByTestId("writing-outline-toggle"));
    expect(localStorage.getItem("nebula.workspace.outline-collapsed")).toBe("1");
    fireEvent.click(screen.getByTestId("writing-outline-toggle"));
    expect(localStorage.getItem("nebula.workspace.outline-collapsed")).toBe("0");
  });

  it("从 localStorage 读取收起状态（首次渲染即收起）", () => {
    localStorage.setItem("nebula.workspace.outline-collapsed", "1");
    render(
      <WritingArea
        current={STRUCTURED_CURRENT}
        content=""
        onContentChange={() => {}}
        onSaveDraft={async () => {}}
        onRegenerate={async () => {}}
        onFactGuard={async () => {}}
        busy={false}
      />,
    );
    expect(screen.queryByTestId("writing-chapter-theme")).not.toBeInTheDocument();
    expect(screen.getByTestId("writing-outline-toggle").getAttribute("aria-expanded")).toBe("false");
  });
});