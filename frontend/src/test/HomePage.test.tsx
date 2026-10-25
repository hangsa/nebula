import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import HomeLayout from "../components/layout/HomeLayout";
import HomePage from "../pages/HomePage";
import { ToastProvider } from "../hooks/useToast";
import api from "../api/client";

vi.mock("../hooks/useGenres", () => ({
  useGenres: () => [
    { id: "cool_novel", label_zh: "爽文", label_en: "Power Fantasy", family: "power_fantasy", ui_visible: true },
    { id: "xuanyi", label_zh: "悬疑", label_en: "Mystery", family: "mystery", ui_visible: true },
  ],
}));

beforeEach(() => {
  vi.restoreAllMocks();
  localStorage.removeItem("nebula.home.sidebar.collapsed");
});

function mockEmptyProjectEndpoints() {
  vi.spyOn(global, "fetch").mockImplementation((url) => {
    if (typeof url === "string" && url.includes("/api/project/list")) {
      return Promise.resolve(new Response(JSON.stringify({ error: false, detail: [] })));
    }
    if (typeof url === "string" && url.includes("/api/project/stats")) {
      return Promise.resolve(new Response(JSON.stringify({ error: false, detail: {
        total_books: 0, total_chapters: 0, total_words: 0,
        stage_distribution: {}, word_count_series: [],
      } })));
    }
    return Promise.resolve(new Response("{}"));
  });
}

// HomePage now reads project state from HomeLayout via Outlet context, so we
// render the full HomeLayout shell (same as the real /  route in App.tsx).
// `withWorkspaceStub` also registers a stub element at /project/:projectId/workspace
// so we can assert that create / resume navigation lands there.
function renderHome(initialPath = "/", options: { withWorkspaceStub?: boolean } = {}) {
  const { withWorkspaceStub = false } = options;
  return render(
    <ToastProvider>
      <MemoryRouter initialEntries={[initialPath]}>
        <Routes>
          <Route element={<HomeLayout />}>
            <Route path="/" element={<HomePage />} />
          </Route>
          {withWorkspaceStub && (
            <Route
              path="/project/:projectId/workspace"
              element={<div data-testid="workspace-stub">workspace</div>}
            />
          )}
        </Routes>
      </MemoryRouter>
    </ToastProvider>
  );
}

describe("HomePage", () => {
  it("renders TopBar + StatsSidebar + BookShelf after projects load", async () => {
    mockEmptyProjectEndpoints();
    renderHome();
    await waitFor(() => {
      expect(screen.getByTestId("home-top-bar")).toBeInTheDocument();
      expect(screen.getByTestId("stats-sidebar")).toBeInTheDocument();
      expect(screen.getByTestId("book-shelf")).toBeInTheDocument();
    });
    // Brand + version chip live in the global top bar.
    expect(screen.getByText("Nebula Forge")).toBeInTheDocument();
    expect(screen.getByTestId("version-chip")).toHaveTextContent("V0.1.0");
    // The card is gone — creation now flows through a modal opened by the
    // BookShelf header's "+ 新建项目" button.
    expect(screen.queryByTestId("create-project-card")).not.toBeInTheDocument();
    expect(screen.getByText("+ 新建项目")).toBeInTheDocument();
  });

  it("opens the create-project modal when the + 新建项目 button is clicked", async () => {
    mockEmptyProjectEndpoints();
    renderHome();
    await waitFor(() => screen.getByText("+ 新建项目"));
    expect(screen.queryByTestId("create-project-modal")).not.toBeInTheDocument();
    const button = screen.getByText("+ 新建项目");
    button.click();
    await waitFor(() => {
      expect(screen.getByTestId("create-project-modal")).toBeInTheDocument();
    });
  });

  // v2.x (workspace-wizard fusion, 2026-08-30): create + resume no longer
  // open the InitWizardModal overlay. They navigate to
  // /project/:id/workspace?tab=settings where the wizard panel lives.
  it("navigates to /project/:id/workspace?tab=settings after creating a project", async () => {
    mockEmptyProjectEndpoints();
    vi.spyOn(api, "createProject").mockResolvedValue({
      id: "proj_new", title: "新书", genre: "cool_novel",
      current_stage: "STAGE1", created_at: "2026-08-30T00:00:00Z",
      updated_at: 0, min_words: 2000, target_total_words: 100000,
      target_length_category: "标准连载",
    });
    vi.spyOn(api, "advance").mockResolvedValue({
      success: true, stage: "STAGE1", error: null,
    });
    renderHome("/", { withWorkspaceStub: true });
    await waitFor(() => screen.getByText("+ 新建项目"));
    fireEvent.click(screen.getByText("+ 新建项目"));
    await waitFor(() => screen.getByTestId("create-project-modal"));
    fireEvent.input(screen.getByTestId("title-input"), {
      target: { value: "新书测试" },
    });
    fireEvent.click(screen.getByTestId("create-submit"));
    await waitFor(() => {
      expect(screen.getByTestId("workspace-stub")).toBeInTheDocument();
    });
  });

  it("shows the bookshelf empty state when there are no projects", async () => {
    mockEmptyProjectEndpoints();
    renderHome();
    await waitFor(() => {
      expect(screen.getByText(/还没有项目/)).toBeInTheDocument();
    });
  });

  it("renders 设置/用户/支持 icon buttons in the top bar in that order", async () => {
    mockEmptyProjectEndpoints();
    renderHome();
    await waitFor(() => screen.getByTestId("home-top-bar"));

    const settings = screen.getByTestId("header-settings");
    const user = screen.getByTestId("header-user");
    const support = screen.getByTestId("header-support");
    expect(settings).toHaveAttribute("aria-label", "设置");
    expect(user).toHaveAttribute("aria-label", "用户");
    expect(support).toHaveAttribute("aria-label", "支持");

    const topBar = screen.getByTestId("home-top-bar");
    const order = Array.from(
      topBar.querySelectorAll('[data-testid^="header-"]')
    ).map((el) => el.getAttribute("data-testid"));
    expect(order).toEqual(["header-settings", "header-user", "header-support"]);
  });
});
