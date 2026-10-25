import { useMemo } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { ProjectStats } from "../../api/client";
import { PhaseIndicator, Sidebar, SidebarNavItem, Sparkline, StatCard } from "../ds";
import { BUSINESS_GROUPS, businessGroupOf } from "../ds/stages";

interface StatsSidebarProps {
  stats: ProjectStats | null;
  statsLoading: boolean;
}

export default function StatsSidebar({
  stats,
  statsLoading,
}: StatsSidebarProps) {
  const navigate = useNavigate();
  const location = useLocation();

  const isBookshelfActive = location.pathname === "/";
  const isConsoleActive = location.pathname === "/ai-console";
  const isPlazaActive = location.pathname === "/prompt-plaza";
  const isDimensionsActive = location.pathname === "/creative-dimensions";

  // Collapse the 8 backend stages into the 4 business-facing groups shown
  // in the sidebar. Sum counts across every stage that maps to each group,
  // so a project in STAGE3 contributes to 概念 alongside STAGE1/2.
  const phases = useMemo(
    () =>
      BUSINESS_GROUPS.map((label) => {
        const count = Object.entries(stats?.stage_distribution ?? {}).reduce(
          (acc, [stage, n]) => (businessGroupOf(stage) === label ? acc + (n ?? 0) : 0),
          0
        );
        return {
          key: label,
          label,
          count,
          active: label === "写作中",
          completed: label === "已完成",
        };
      }),
    [stats]
  );

  return (
    <Sidebar
      width={240}
      persistKey="nebula.home.sidebar.collapsed"
      header={null}
      testId="stats-sidebar"
    >
      {(collapsed) => (
        <div className="flex flex-col gap-4">
          <nav className="flex flex-col">
            <SidebarNavItem
              icon="auto_stories"
              label="图书墙"
              active={isBookshelfActive}
              onClick={() => navigate("/")}
              collapsed={collapsed}
              testId="nav-bookshelf"
            />
            <SidebarNavItem
              icon="smart_toy"
              label="AI 控制台"
              active={isConsoleActive}
              onClick={() => navigate("/ai-console")}
              collapsed={collapsed}
              testId="nav-ai-console"
            />
            <SidebarNavItem
              icon="forum"
              label="提示词广场"
              active={isPlazaActive}
              onClick={() => navigate("/prompt-plaza")}
              collapsed={collapsed}
              testId="nav-prompt-plaza"
            />
            <SidebarNavItem
              icon="palette"
              label="创作维度"
              active={isDimensionsActive}
              onClick={() => navigate("/creative-dimensions")}
              collapsed={collapsed}
              testId="nav-creative-dimensions"
            />
            <SidebarNavItem
              icon="monitoring"
              label="统计数据"
              collapsed={collapsed}
              testId="nav-stats"
            />
          </nav>

          {!collapsed && (
            <>
              <section>
                <h3 className="font-mono text-label-sm uppercase tracking-wider text-on-surface-variant mb-2">
                  全部统计
                </h3>
                <div className="flex flex-col gap-2">
                  <StatCard label="书籍数" value={stats?.total_books ?? null} size="sm" />
                  <StatCard label="总章节" value={stats?.total_chapters ?? null} size="sm" />
                  <StatCard
                    label="总字数"
                    value={stats?.total_words ?? null}
                    size="sm"
                    sparkline={
                      stats?.word_count_series && stats.word_count_series.length >= 2 ? (
                        <Sparkline data={stats.word_count_series} testId="sparkline-total-words" />
                      ) : null
                    }
                  />
                </div>
              </section>

              <section>
                <h3 className="font-mono text-label-sm uppercase tracking-wider text-on-surface-variant mb-2">
                  阶段分布
                </h3>
                <PhaseIndicator phases={phases} />
              </section>
            </>
          )}

          {statsLoading && (
            <div className="font-mono text-label-sm text-on-surface-variant/60">
              加载中…
            </div>
          )}
        </div>
      )}
    </Sidebar>
  );
}