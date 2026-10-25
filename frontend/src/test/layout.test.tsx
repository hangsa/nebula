import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import TopHeader from "../components/layout/TopHeader";
import SideNavBar from "../components/layout/SideNavBar";
import CircuitBreaker from "../components/shared/CircuitBreaker";
import SFLogFeed from "../components/shared/SFLogFeed";
import StageErrorBoundary from "../components/shared/StageErrorBoundary";
import type { ParsedLog } from "../api/client";

describe("TopHeader", () => {
  it("renders nebula branding", () => {
    render(
      <TopHeader
        projectName="测试项目"
        currentStage="STAGE1"
        collaborationMode="live"
        autoSaveStatus="saved"
        collapsed={false}
        onToggleSidebar={() => {}}
      />
    );
    expect(screen.getByText("nebula")).toBeInTheDocument();
    expect(screen.getByText("测试项目")).toBeInTheDocument();
  });

  it("shows current stage badge", () => {
    render(
      <TopHeader
        projectName=""
        currentStage="STAGE3"
        collaborationMode="live"
        autoSaveStatus="saved"
        collapsed={false}
        onToggleSidebar={() => {}}
      />
    );
    expect(screen.getByText("STAGE3")).toBeInTheDocument();
  });

  it("shows collaboration mode in Chinese", () => {
    render(
      <TopHeader
        projectName=""
        currentStage="INIT"
        collaborationMode="live"
        autoSaveStatus="saved"
        collapsed={false}
        onToggleSidebar={() => {}}
      />
    );
    expect(screen.getByText("实时写作")).toBeInTheDocument();
  });

  it("shows discussion mode", () => {
    render(
      <TopHeader
        projectName=""
        currentStage="INIT"
        collaborationMode="discuss"
        autoSaveStatus="saved"
        collapsed={false}
        onToggleSidebar={() => {}}
      />
    );
    expect(screen.getByText("讨论模式")).toBeInTheDocument();
  });

  it("hides project name separator when empty", () => {
    render(
      <TopHeader
        projectName=""
        currentStage="INIT"
        collaborationMode="live"
        autoSaveStatus="saved"
        collapsed={false}
        onToggleSidebar={() => {}}
      />
    );
    expect(screen.queryByText("/")).not.toBeInTheDocument();
  });
});

describe("TopHeader - sidebar toggle", () => {
  it("renders the hamburger button", () => {
    render(
      <TopHeader
        projectName=""
        currentStage="INIT"
        collaborationMode="live"
        autoSaveStatus="saved"
        collapsed={false}
        onToggleSidebar={() => {}}
      />
    );
    expect(screen.getByRole("button", { name: "收起侧边栏" })).toBeInTheDocument();
  });

  it("clicking the hamburger calls onToggleSidebar", () => {
    const onToggleSidebar = vi.fn();
    render(
      <TopHeader
        projectName=""
        currentStage="INIT"
        collaborationMode="live"
        autoSaveStatus="saved"
        collapsed={false}
        onToggleSidebar={onToggleSidebar}
      />
    );
    fireEvent.click(screen.getByRole("button", { name: "收起侧边栏" }));
    expect(onToggleSidebar).toHaveBeenCalledTimes(1);
  });

  it("shows expanded label when collapsed is true", () => {
    render(
      <TopHeader
        projectName=""
        currentStage="INIT"
        collaborationMode="live"
        autoSaveStatus="saved"
        collapsed={true}
        onToggleSidebar={() => {}}
      />
    );
    expect(screen.getByRole("button", { name: "展开侧边栏" })).toBeInTheDocument();
  });
});

describe("SideNavBar", () => {
  const onNavigate = vi.fn();

  it("renders project-center entry + workspace items (灵感库 disabled; style/review/impact/storyos enabled)", () => {
    render(
      <SideNavBar
        currentStage="STYLE"
        onNavigate={onNavigate}
        collapsed={false}
        width={280}
        onLiveWidthChange={() => {}}
        onCommitWidth={() => {}}
      />
    );
    expect(screen.getByText("项目中心")).toBeInTheDocument();
    expect(screen.getByText("风格沙盒")).toBeInTheDocument();
    expect(screen.getByText("章节审查")).toBeInTheDocument();
    expect(screen.getByText("影响分析")).toBeInTheDocument();
    expect(screen.getByText("资产中心")).toBeInTheDocument();
  });

  // v1.8.1: /workspace is a top-level route, so SideNavBar cannot link to
  // 工作台 / 全书诊断 / 导出中心. Those entries are gone.
  it("does NOT render the workspace entry (workspace is top-level)", () => {
    render(
      <SideNavBar
        currentStage="STYLE"
        onNavigate={onNavigate}
        collapsed={false}
        width={280}
        onLiveWidthChange={() => {}}
        onCommitWidth={() => {}}
      />
    );
    expect(screen.queryByText("工作台")).not.toBeInTheDocument();
    expect(screen.queryByText("全书诊断")).not.toBeInTheDocument();
    expect(screen.queryByText("导出中心")).not.toBeInTheDocument();
  });

  it("highlights the active stage", () => {
    render(
      <SideNavBar
        currentStage="STYLE"
        onNavigate={onNavigate}
        collapsed={false}
        width={280}
        onLiveWidthChange={() => {}}
        onCommitWidth={() => {}}
      />
    );
    const activeButton = screen.getByText("风格沙盒").closest("button");
    expect(activeButton).toHaveClass("border-primary-container");
  });

  it("renders disabled workspace items", () => {
    render(
      <SideNavBar
        currentStage="STYLE"
        onNavigate={onNavigate}
        collapsed={false}
        width={280}
        onLiveWidthChange={() => {}}
        onCommitWidth={() => {}}
      />
    );
    const inspirationBtn = screen.getByText("灵感库").closest("button");
    expect(inspirationBtn).toBeDisabled();
  });

  it("calls onNavigate when stage clicked", () => {
    render(
      <SideNavBar
        currentStage="STYLE"
        onNavigate={onNavigate}
        collapsed={false}
        width={280}
        onLiveWidthChange={() => {}}
        onCommitWidth={() => {}}
      />
    );
    fireEvent.click(screen.getByText("风格沙盒"));
    expect(onNavigate).toHaveBeenCalledWith("STYLE");
  });

  // v1.8.1: STAGES is empty so the 叙事阶段 header is hidden; only 项目
  // and 工作区 sections render.
  it("renders 项目 and 工作区 section headers (no 叙事阶段 since STAGES is empty)", () => {
    render(
      <SideNavBar
        currentStage="INIT"
        onNavigate={onNavigate}
        collapsed={false}
        width={280}
        onLiveWidthChange={() => {}}
        onCommitWidth={() => {}}
      />
    );
    expect(screen.getByText("项目")).toBeInTheDocument();
    expect(screen.queryByText("叙事阶段")).not.toBeInTheDocument();
    expect(screen.getByText("工作区")).toBeInTheDocument();
  });
});

describe("SideNavBar - collapsed", () => {
  it("returns null when collapsed is true", () => {
    const onNavigate = vi.fn();
    const { container } = render(
      <SideNavBar
        currentStage="STAGE4"
        onNavigate={onNavigate}
        collapsed={true}
        width={280}
        onLiveWidthChange={() => {}}
        onCommitWidth={() => {}}
      />
    );
    expect(container.firstChild).toBeNull();
  });

  it("renders ResizeHandle when expanded", () => {
    const onNavigate = vi.fn();
    render(
      <SideNavBar
        currentStage="STAGE4"
        onNavigate={onNavigate}
        collapsed={false}
        width={280}
        onLiveWidthChange={() => {}}
        onCommitWidth={() => {}}
      />
    );
    expect(screen.getByTestId("resize-handle")).toBeInTheDocument();
  });
});

describe("CircuitBreaker", () => {
  it("renders title and message", () => {
    render(
      <CircuitBreaker
        title="逻辑矛盾检测"
        message="Fact Guard 在第 3 次重试后仍未通过。"
        userOptions={[
          { label: "强制通过", action: vi.fn(), variant: "danger" },
          { label: "取消", action: vi.fn() },
        ]}
      />
    );
    expect(screen.getByText("逻辑矛盾检测")).toBeInTheDocument();
    expect(
      screen.getByText(/Fact Guard 在第 3 次重试后仍未通过/)
    ).toBeInTheDocument();
    expect(screen.getByText("强制通过")).toBeInTheDocument();
    expect(screen.getByText("取消")).toBeInTheDocument();
  });

  it("renders circuit breaker header", () => {
    render(
      <CircuitBreaker
        title="测试"
        message="错误"
        userOptions={[{ label: "确认", action: vi.fn() }]}
      />
    );
    expect(screen.getByText("电路熔断 — Circuit Breaker")).toBeInTheDocument();
  });

  it("applies danger variant styles", () => {
    render(
      <CircuitBreaker
        title="测试"
        message="错误"
        userOptions={[{ label: "危险操作", action: vi.fn(), variant: "danger" }]}
      />
    );
    const btn = screen.getByText("危险操作");
    expect(btn).toHaveClass("bg-error-p0/20");
  });

  it("applies primary variant styles", () => {
    render(
      <CircuitBreaker
        title="测试"
        message="错误"
        userOptions={[{ label: "主操作", action: vi.fn(), variant: "primary" }]}
      />
    );
    const btn = screen.getByText("主操作");
    expect(btn).toHaveClass("border-primary-container/30");
  });

  it("calls action on button click", () => {
    const action = vi.fn();
    render(
      <CircuitBreaker
        title="测试"
        message="错误"
        userOptions={[{ label: "执行", action }]}
      />
    );
    fireEvent.click(screen.getByText("执行"));
    expect(action).toHaveBeenCalledOnce();
  });
});

describe("SFLogFeed", () => {
  it("renders empty state", () => {
    render(<SFLogFeed logs={[]} />);
    expect(screen.getByText("等待解析...")).toBeInTheDocument();
  });

  it("renders log entries with Chinese labels", () => {
    const logs: ParsedLog[] = [
      {
        type: "conflict_escalate",
        params: { id: "cf_001", new_intensity: "critical" },
        raw_text: "",
      },
      {
        type: "knowledge_gain",
        params: { char: "林峰", content: "秘密联络记录" },
        raw_text: "",
      },
    ];
    render(<SFLogFeed logs={logs} />);
    expect(screen.getByText("冲突升级")).toBeInTheDocument();
    expect(screen.getByText("知识")).toBeInTheDocument();
  });

  it("falls back to raw type for unknown log types", () => {
    const logs: ParsedLog[] = [{ type: "custom_event", params: { key: "val" }, raw_text: "" }];
    render(<SFLogFeed logs={logs} />);
    expect(screen.getByText("custom_event")).toBeInTheDocument();
  });

  it("shows up to 3 params per log entry", () => {
    const logs: ParsedLog[] = [
      {
        type: "character_emotion",
        params: { a: "1", b: "2", c: "3", d: "4" },
        raw_text: "",
      },
    ];
    render(<SFLogFeed logs={logs} />);
    expect(screen.getByText("a=1")).toBeInTheDocument();
    expect(screen.getByText("b=2")).toBeInTheDocument();
    expect(screen.getByText("c=3")).toBeInTheDocument();
    expect(screen.queryByText("d=4")).not.toBeInTheDocument();
  });

  it("renders SF_LOG Feed header", () => {
    render(<SFLogFeed logs={[]} />);
    expect(screen.getByText("SF_LOG Feed")).toBeInTheDocument();
  });
});

describe("StageErrorBoundary", () => {
  it("renders children when no error", () => {
    render(
      <StageErrorBoundary>
        <span>正常内容</span>
      </StageErrorBoundary>
    );
    expect(screen.getByText("正常内容")).toBeInTheDocument();
  });

  it("shows CircuitBreaker on error", () => {
    const ThrowError = () => {
      throw new Error("渲染失败: 数据格式异常");
    };
    // Suppress expected error logging during test
    const spy = vi.spyOn(console, "error").mockImplementation(() => {});
    render(
      <StageErrorBoundary>
        <ThrowError />
      </StageErrorBoundary>
    );
    spy.mockRestore();

    expect(screen.getByText("页面渲染异常")).toBeInTheDocument();
    expect(
      screen.getByText("渲染失败: 数据格式异常")
    ).toBeInTheDocument();
  });

  it("provides reset and fallback options on error", () => {
    const ThrowError = () => {
      throw new Error("test error");
    };
    const spy = vi.spyOn(console, "error").mockImplementation(() => {});
    render(
      <StageErrorBoundary>
        <ThrowError />
      </StageErrorBoundary>
    );
    spy.mockRestore();

    expect(screen.getByText("重试渲染")).toBeInTheDocument();
    expect(screen.getByText("回退")).toBeInTheDocument();
  });
});
