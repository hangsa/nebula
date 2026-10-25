import SidebarToggleButton from "./SidebarToggleButton";

interface TopHeaderProps {
  projectName: string;
  currentStage: string;
  collaborationMode: string;
  autoSaveStatus: "saved" | "saving" | "error";
  collapsed: boolean;
  onToggleSidebar: () => void;
}

export default function TopHeader({
  projectName,
  currentStage,
  collaborationMode,
  autoSaveStatus,
  collapsed,
  onToggleSidebar,
}: TopHeaderProps) {
  return (
    <header className="fixed top-0 left-0 w-full z-50 h-16 bg-surface-container-low border-b border-outline-variant flex items-center justify-between px-6">
      <div className="flex items-center gap-4">
        <SidebarToggleButton collapsed={collapsed} onToggle={onToggleSidebar} />
        <span className="font-display text-lg text-primary-container font-semibold">
          nebula
        </span>
        {projectName && (
          <>
            <span className="text-system-log">/</span>
            <span className="font-body text-sm text-primary">{projectName}</span>
          </>
        )}
        <span className="font-label-mono text-primary-container bg-primary-container/10 px-2 py-0.5 rounded">
          {currentStage}
        </span>
        <span className="font-body-ui text-tertiary-container flex items-center gap-1">
          <span className="w-2 h-2 rounded-full bg-tertiary-container" />
          {collaborationMode === "live" ? "实时写作" : "讨论模式"}
        </span>
      </div>
      <div className="flex items-center gap-3">
        <button className="font-body-ui text-system-log hover:text-primary transition-colors">
          <span className="material-symbols-outlined text-lg">search</span>
        </button>
        <button className="font-body-ui text-system-log hover:text-primary transition-colors">
          <span className="material-symbols-outlined text-lg">
            notifications
          </span>
        </button>
        <div className="w-8 h-8 rounded-full bg-primary-container/20 flex items-center justify-center">
          <span className="material-symbols-outlined text-primary-container text-sm">
            person
          </span>
        </div>
      </div>
    </header>
  );
}
