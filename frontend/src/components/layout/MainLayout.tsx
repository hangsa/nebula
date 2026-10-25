import { useState, useEffect, useCallback } from "react";
import { Outlet, useParams, useLocation, useMatch, useNavigate } from "react-router-dom";
import TopHeader from "./TopHeader";
import SideNavBar from "./SideNavBar";
import { useSidebar } from "../../hooks/useSidebar";
import api from "../../api/client";

const STAGE_FROM_PATH: Record<string, string> = {
  stage1: "STAGE1",
  stage2: "STAGE2",
  stage3: "STAGE3",
  stage4: "STAGE4",
  stage5: "STAGE5",
  stage6: "STAGE6",
  style: "STYLE",
  settings: "SETTINGS",
  review: "REVIEW",
  impact: "IMPACT",
  storyos: "STORYOS",
  "stage3/outline": "STAGE3",
  "stage3/branches": "STAGE3",
  "stage6/plot": "STAGE6",
  // v1.8.1: /workspace is a top-level route (no longer nested under
  // MainLayout), so these mappings are dead. Kept commented for the
  // duration of this release in case the v1.9 plan reintroduces a
  // workspace-aware sidebar — remove entirely in v1.9 if not.
  // workspace: "WORKSPACE",
  // "workspace?mode=manual": "WORKSPACE",
  // "workspace?mode=manual&panel=diagnosis": "WORKSPACE_DIAGNOSIS",
  // "workspace?mode=manual&panel=export": "WORKSPACE_EXPORT",
};

const STAGE_TO_PATH: Record<string, string> = {
  STAGE1: "stage1",
  STAGE2: "stage2",
  STAGE3: "stage3",
  STAGE4: "stage4",
  STAGE5: "stage5",
  STAGE6: "stage6",
  STYLE: "style",
  SETTINGS: "settings",
  REVIEW: "review",
  IMPACT: "impact",
  STORYOS: "storyos",
  // v1.8.1: same as above — dead until v1.9 decides otherwise.
  // WORKSPACE: "workspace",
  // WORKSPACE_DIAGNOSIS: "workspace",
  // WORKSPACE_EXPORT: "workspace",
};

export default function MainLayout() {
  const { projectId: paramId } = useParams<{ projectId: string }>();
  const location = useLocation();
  const navigate = useNavigate();
  const match = useMatch("/project/:projectId/*");
  const projectId = match?.params.projectId || paramId || "";

  const [projectName, setProjectName] = useState("");

  const pathStage = match?.params["*"] || "";
  const currentStage = STAGE_FROM_PATH[pathStage] || "INIT";

  const { collapsed, width, setWidthLive, commitWidth, toggle } = useSidebar();

  useEffect(() => {
    if (!projectId) return;
    api
      .getProjectStatus(projectId)
      .then((status) => {
        if (status?.title) setProjectName(status.title);
      })
      .catch(() => {});
  }, [projectId]);

  const handleNavigate = useCallback(
    (stage: string) => {
      if (stage === "dashboard") {
        navigate("/");
        return;
      }
      // v1.8.1: WORKSPACE* branches removed — workspace is a top-level route
      // not reachable through MainLayout. Sidebar's STAGES list no longer
      // emits these keys, so this branch was dead code.
      const path = STAGE_TO_PATH[stage];
      if (path && projectId) {
        navigate(`/project/${projectId}/${path}`);
      }
    },
    [projectId, navigate]
  );

  return (
    <div className="min-h-screen bg-canvas-bg">
      <TopHeader
        projectName={projectName || projectId || "nebula"}
        currentStage={currentStage}
        collaborationMode="live"
        autoSaveStatus="saved"
        collapsed={collapsed}
        onToggleSidebar={toggle}
      />
      <SideNavBar
        currentStage={currentStage}
        onNavigate={handleNavigate}
        collapsed={collapsed}
        width={width}
        onLiveWidthChange={setWidthLive}
        onCommitWidth={commitWidth}
      />
      <main
        style={{ marginLeft: collapsed ? 0 : width }}
        className="mt-16 p-6 transition-all duration-200"
      >
        <Outlet />
      </main>
    </div>
  );
}
