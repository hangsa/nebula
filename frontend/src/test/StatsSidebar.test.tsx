import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import StatsSidebar from "../components/home/StatsSidebar";

const SAMPLE_STATS = {
  total_books: 12,
  total_chapters: 87,
  total_words: 214000,
  stage_distribution: {
    INIT: 1, STAGE1: 2, STAGE2: 0, STAGE3: 0,
    STAGE4: 5, STAGE5: 0, STAGE6: 0, COMPLETED: 4,
  },
  word_count_series: [20000, 45000, 70000, 120000, 160000, 214000],
};

beforeEach(() => {
  localStorage.removeItem("nebula.home.sidebar.collapsed");
});

function renderSidebar(initialPath = "/") {
  return render(
    <MemoryRouter initialEntries={[initialPath]}>
      <StatsSidebar stats={SAMPLE_STATS} statsLoading={false} />
    </MemoryRouter>
  );
}

describe("StatsSidebar", () => {
  it("renders the stats sections when expanded (brand moved to TopBar)", () => {
    renderSidebar();
    // Brand now lives in the global TopBar, not the sidebar.
    expect(screen.queryByText("Nebula Forge")).not.toBeInTheDocument();
    expect(screen.getByText("全部统计")).toBeInTheDocument();
    expect(screen.getByText("阶段分布")).toBeInTheDocument();
    expect(screen.queryByText("快捷操作")).not.toBeInTheDocument();
    expect(screen.getByText("书籍数")).toBeInTheDocument();
    expect(screen.getByText("总章节")).toBeInTheDocument();
    expect(screen.getByText("总字数")).toBeInTheDocument();
  });

  it("renders only the collapsed icon column when localStorage says collapsed", () => {
    localStorage.setItem("nebula.home.sidebar.collapsed", "true");
    renderSidebar();
    expect(screen.queryByText("全部统计")).not.toBeInTheDocument();
    expect(screen.queryByText("快捷操作")).not.toBeInTheDocument();
  });

  it("shows 加载中… when statsLoading is true", () => {
    render(
      <MemoryRouter initialEntries={["/"]}>
        <StatsSidebar stats={null} statsLoading />
      </MemoryRouter>
    );
    expect(screen.getByText("加载中…")).toBeInTheDocument();
  });

  it("marks 图书墙 active on / and forwards AI 控制台/提示词广场 nav clicks to the routes", () => {
    renderSidebar("/");
    const bookshelf = screen.getByTestId("nav-bookshelf");
    expect(bookshelf.className).toContain("bg-primary-container/15");
    expect(bookshelf.className).toContain("border-primary");

    // AI 控制台 and 提示词广场 navigate via useNavigate — no callback prop.
    expect(screen.getByTestId("nav-ai-console")).toBeInTheDocument();
    expect(screen.getByTestId("nav-prompt-plaza")).toBeInTheDocument();
    expect(screen.getByTestId("nav-stats")).toBeInTheDocument();
  });

  it("marks AI 控制台 active when the URL is /ai-console", () => {
    render(
      <MemoryRouter initialEntries={["/ai-console"]}>
        <StatsSidebar stats={SAMPLE_STATS} statsLoading={false} />
      </MemoryRouter>
    );
    expect(screen.getByTestId("nav-ai-console").className).toContain("bg-primary-container/15");
    expect(screen.getByTestId("nav-bookshelf").className).not.toContain("bg-primary-container/15");
  });

  it("does not render the 设置/支持 footer (moved to TopBar)", () => {
    renderSidebar();
    expect(screen.queryByTestId("footer-settings")).not.toBeInTheDocument();
    expect(screen.queryByTestId("footer-support")).not.toBeInTheDocument();
  });

  it("no longer renders the version chip (moved to TopBar)", () => {
    renderSidebar();
    expect(screen.queryByTestId("version-chip")).not.toBeInTheDocument();
  });

  it("renders the 总字数 sparkline when word_count_series has >= 2 points", () => {
    renderSidebar();
    expect(screen.getByTestId("sparkline-total-words")).toBeInTheDocument();
  });

  it("hides the 总字数 sparkline when word_count_series is empty or short", () => {
    const emptyStats = { ...SAMPLE_STATS, word_count_series: [] };
    const { rerender } = render(
      <MemoryRouter initialEntries={["/"]}>
        <StatsSidebar stats={emptyStats} statsLoading={false} />
      </MemoryRouter>
    );
    expect(screen.queryByTestId("sparkline-total-words")).not.toBeInTheDocument();

    const shortStats = { ...SAMPLE_STATS, word_count_series: [42] };
    rerender(
      <MemoryRouter initialEntries={["/"]}>
        <StatsSidebar stats={shortStats} statsLoading={false} />
      </MemoryRouter>
    );
    expect(screen.queryByTestId("sparkline-total-words")).not.toBeInTheDocument();
  });

  it("does not render the Total sublabel on the 总字数 stat card (removed)", () => {
    renderSidebar();
    expect(screen.queryByText("Total")).not.toBeInTheDocument();
  });

  it("collapses 8 backend stages into 4 business groups with summed counts", () => {
    // SAMPLE_STATS:
    //   INIT:1 STAGE1:2 STAGE2:0 STAGE3:0  → 概念 = 3
    //   STAGE4:5                              → 写作中 = 5
    //   STAGE5:0 STAGE6:0                     → 润色中 = 0
    //   COMPLETED:4                           → 已完成 = 4
    renderSidebar();
    const phaseList = screen.getByRole("list");
    expect(phaseList).toBeInTheDocument();
    expect(within(phaseList).getByText("概念")).toBeInTheDocument();
    expect(within(phaseList).getByText("写作中")).toBeInTheDocument();
    expect(within(phaseList).getByText("润色中")).toBeInTheDocument();
    expect(within(phaseList).getByText("已完成")).toBeInTheDocument();

    // Old stage labels should no longer be shown.
    expect(within(phaseList).queryByText("工作台")).not.toBeInTheDocument();
    expect(within(phaseList).queryByText("初始化")).not.toBeInTheDocument();
  });
});