import { useEffect, useMemo, useState } from "react";
import { RegenerateModal } from "../shared/RegenerateModal";

const OUTLINE_COLLAPSED_KEY = "nebula.workspace.outline-collapsed";

export interface WritingCurrent {
  chapter_number: number;
  chapter_title: string;
  /** Chapter-level theme (from outline.json). Optional. */
  chapter_theme?: string;
  scene_id: string;
  scene_title: string;
  /** Scene-level outline details (from outline.json scene_plan). All optional
   *  — older outline.json files written before these fields were introduced
   *  are still loadable. */
  scene_goal?: string;
  scene_conflict?: string;
  scene_emotional_arc?: string;
  scene_narrative_role?: string;
  scene_beat_type?: string;
  /** Back-compat field — single-line summary shown when no structured fields
   *  are present. Prefer reading chapter_theme/scene_goal/etc. */
  outline_summary?: string;
}

interface Props {
  current: WritingCurrent | null;
  content: string;
  onContentChange: (next: string) => void;
  onSaveDraft: () => Promise<void>;
  /**
   * v1.9: the page passes user_modifications (collected by RegenerateModal)
   * to the API. Empty string means "regenerate without guidance" — the
   * page's call into the backend then becomes equivalent to today's
   * behavior (no user_modifications block appended to the prompt).
   */
  onRegenerate: (userModifications: string) => Promise<void>;
  /**
   * Optional: when true, the RegenerateModal renders an extra subtitle
   * warning the user that unsaved edits will be overwritten. Defaults to
   * false so the modal looks identical to other regenerate flows when
   * the editor matches the on-disk draft.
   */
  hasUnsavedChanges?: boolean;
  onFactGuard: () => Promise<void>;
  busy: boolean;
  /** Optional: scenario that's empty because the chapter has no outline yet */
  emptyStatePrompt?: string;
  /**
   * Optional: when set, the empty state renders a "前往生成大纲" button that
   * calls this. The page wires it to switch the right panel to the outline
   * tab (spec § WritingArea empty state).
   */
  onNavigateToOutline?: () => void;
}

export default function WritingArea({
  current, content, onContentChange, onSaveDraft, onRegenerate, hasUnsavedChanges = false, onFactGuard, busy, emptyStatePrompt, onNavigateToOutline,
}: Props) {
  // Hooks must be called in the same order every render — declare them all
  // before any early return. (Rules of Hooks: never conditional.)
  // v1.9: RegenerateModal state. Opens when the user clicks the footer's
  // "重新生成" button; closes after confirm/cancel. The modal returns
  // user_modifications which the page passes to /stage4/write-scene.
  const [showRegenerateModal, setShowRegenerateModal] = useState(false);
  const [outlineCollapsed, setOutlineCollapsed] = useState<boolean>(() => {
    if (typeof localStorage === "undefined") return false;
    try {
      return localStorage.getItem(OUTLINE_COLLAPSED_KEY) === "1";
    } catch {
      return false;
    }
  });

  useEffect(() => {
    try {
      localStorage.setItem(OUTLINE_COLLAPSED_KEY, outlineCollapsed ? "1" : "0");
    } catch {
      // localStorage unavailable (SSR, quota) — non-fatal.
    }
  }, [outlineCollapsed]);

  const wordCount = useMemo(
    () => content.replace(/\s+/g, "").length,
    [content],
  );

  if (!current) {
    return (
      <div
        data-testid="writing-empty"
        className="h-full flex flex-col items-center justify-center text-center p-8 space-y-3"
      >
        <span className="material-symbols-outlined text-5xl text-system-log/30">edit_note</span>
        <p className="font-body-ui text-system-log">{emptyStatePrompt ?? "本章节尚未生成大纲"}</p>
        {onNavigateToOutline && (
          <button
            type="button"
            data-testid="writing-empty-go-to-outline"
            onClick={onNavigateToOutline}
            className="px-4 py-2 rounded-lg bg-tertiary-container text-surface-container-low hover:opacity-90"
          >
            前往生成大纲
          </button>
        )}
      </div>
    );
  }

  return (
    <div data-testid="writing-area" className="h-full flex flex-col">
      <header className="px-6 py-3 border-b border-outline-variant">
        <div className="flex items-center gap-2 font-display text-primary text-base">
          第 {current.chapter_number} 章 ·{" "}
          <span data-testid="writing-chapter-title">{current.chapter_title}</span>
        </div>
        <div data-testid="writing-scene-title" className="font-label-mono text-[10px] uppercase tracking-wider text-system-log mt-1">
          {current.scene_id} · {current.scene_title}
        </div>
        {/* v1.8 Bug 2 fix: surface real outline content above the editor.
            Chapter theme + scene goal/conflict/emotional_arc rendered as
            labeled rows when present. Older outline.json files fall back to
            the single-line outline_summary (or hide the block entirely). */}
        {(current.chapter_theme || current.scene_goal || current.scene_conflict || current.scene_emotional_arc || current.outline_summary) && (
          <div
            data-testid="writing-outline-block"
            className="mt-3 p-3 bg-surface-container rounded-lg border border-outline-variant"
          >
            <button
              type="button"
              data-testid="writing-outline-toggle"
              onClick={() => setOutlineCollapsed((c) => !c)}
              aria-expanded={!outlineCollapsed}
              aria-controls="writing-outline-rows"
              className="flex items-center gap-1 font-label-mono text-[10px] uppercase tracking-wider text-system-log/70 hover:text-system-log w-full text-left"
            >
              <span className="text-xs leading-none">{outlineCollapsed ? "▸" : "▾"}</span>
              <span>{outlineCollapsed ? "显示大纲信息" : "收起大纲信息"}</span>
            </button>
            {!outlineCollapsed && (
              <div id="writing-outline-rows" data-testid="writing-outline-rows" className="space-y-1 mt-2">
                {current.chapter_theme && (
                  <p data-testid="writing-chapter-theme" className="font-body-narrative text-xs text-system-log">
                    <span className="font-label-mono text-system-log/70 mr-1">主题：</span>
                    {current.chapter_theme}
                  </p>
                )}
                {current.scene_goal && (
                  <p data-testid="writing-scene-goal" className="font-body-narrative text-xs text-system-log">
                    <span className="font-label-mono text-system-log/70 mr-1">目标：</span>
                    {current.scene_goal}
                  </p>
                )}
                {current.scene_conflict && (
                  <p data-testid="writing-scene-conflict" className="font-body-narrative text-xs text-system-log">
                    <span className="font-label-mono text-system-log/70 mr-1">冲突：</span>
                    {current.scene_conflict}
                  </p>
                )}
                {current.scene_emotional_arc && (
                  <p data-testid="writing-scene-emotional-arc" className="font-body-narrative text-xs text-system-log">
                    <span className="font-label-mono text-system-log/70 mr-1">情绪弧线：</span>
                    {current.scene_emotional_arc}
                  </p>
                )}
                {current.scene_narrative_role && (
                  <p data-testid="writing-scene-role" className="font-body-narrative text-xs text-system-log">
                    <span className="font-label-mono text-system-log/70 mr-1">叙事角色：</span>
                    {current.scene_narrative_role}
                    {current.scene_beat_type && <> · {current.scene_beat_type}</>}
                  </p>
                )}
                {!current.chapter_theme && !current.scene_goal && current.outline_summary && (
                  <p
                    data-testid="writing-outline-summary"
                    className="font-body-narrative text-xs text-system-log italic"
                  >
                    大纲：{current.outline_summary}
                  </p>
                )}
              </div>
            )}
          </div>
        )}
      </header>

      <textarea
        data-testid="editor-body"
        value={content}
        onChange={(e) => onContentChange(e.target.value)}
        className="flex-1 w-full p-6 bg-surface-container-lowest text-primary font-body-narrative text-base resize-none focus:outline-none"
        style={{ fontFamily: "Georgia, '宋体', serif", lineHeight: 1.8 }}
        spellCheck={false}
      />

      <footer className="flex items-center justify-between px-6 py-2 border-t border-outline-variant text-xs font-body-ui text-system-log">
        <span>
          字数 <span data-testid="editor-word-count">{wordCount}</span>
        </span>
        <div className="flex gap-2">
          <button
            type="button"
            data-testid="editor-regenerate"
            onClick={() => setShowRegenerateModal(true)}
            disabled={busy}
            className="px-3 py-1 rounded bg-surface-container hover:bg-surface-container-low disabled:opacity-40"
          >重新生成</button>
          <button
            type="button"
            data-testid="editor-fact-guard"
            onClick={() => onFactGuard()}
            disabled={busy}
            className="px-3 py-1 rounded bg-surface-container hover:bg-surface-container-low disabled:opacity-40"
          >Fact Guard</button>
          <button
            type="button"
            data-testid="editor-save"
            onClick={() => onSaveDraft()}
            disabled={busy}
            className="px-4 py-1 rounded bg-tertiary-container text-surface-container-low hover:opacity-90 disabled:opacity-40"
          >保存草稿</button>
        </div>
      </footer>

      <RegenerateModal
        open={showRegenerateModal}
        // target uses the scene's display title (e.g. "第2章第3场") so
        // the user can confirm they're regenerating the right scene.
        target={`第${current.chapter_number}章${current.scene_id.split("-")[1] ?? ""}场`}
        placeholder={hasUnsavedChanges ? "你有未保存的修改,继续前请输入修改意见或留空" : undefined}
        onConfirm={async (text) => {
          setShowRegenerateModal(false);
          await onRegenerate(text);
        }}
        onCancel={() => setShowRegenerateModal(false)}
      />
    </div>
  );
}
