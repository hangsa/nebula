import { render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi, beforeEach } from "vitest";
import ConceptStep from "./ConceptStep";
import api from "../../api/client";
import { WizardProvider } from "./WizardContext";

vi.mock("../../api/client", () => ({
  default: {
    generateConcept: vi.fn().mockResolvedValue({
      concept: {
        title: "AI 浪潮",
        genre: "科幻",
        premise: "AI 觉醒的故事",
        tone: "惊悚",
        theme: "人与机器",
        target_audience: "",
        style_template: "",
      },
      story_dna: { core_contradiction: { statement: "", side_a: "", side_b: "" }, value_stack: [] },
    }),
    getConcept: vi.fn().mockResolvedValue({
      concept: {
        title: "AI 浪潮",
        genre: "科幻",
        premise: "AI 觉醒的故事",
        tone: "惊悚",
        theme: "人与机器",
        target_audience: "",
        style_template: "",
      },
      story_dna: { core_contradiction: { statement: "", side_a: "", side_b: "" }, value_stack: [] },
    }),
    updateConcept: vi.fn().mockResolvedValue({}),
    regenerateConceptSection: vi.fn().mockResolvedValue({}),
    advance: vi.fn().mockResolvedValue({}),
  },
}));

describe("ConceptStep with creative-divergence prefill", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    sessionStorage.clear();
    // Pre-seed sessionStorage so WizardProvider loads wizard.data.concept
    // with source="creative_divergence" on first render. The auto-trigger
    // useEffect guards on `!wizard.data.concept`, so a populated concept
    // skips the generate call entirely.
    sessionStorage.setItem(
      "nebula.wizard.state.proj_test",
      JSON.stringify({
        currentStep: 2,
        completedSteps: [1],
        status: "completed",
        data: {
          creative_divergence: {
            variants: [{ id: "v1", label: "概念 ALPHA", title: "风暴密码", description: "AI 试图", tags: ["科幻"], created_at: "2026-08-30T00:00:00Z" }],
            selected_id: "v1",
          },
          concept: {
            title: "风暴密码",
            genre: "科幻",
            premise: "AI 试图摆脱人类控制",
            tone: "惊悚",
            theme: "人与机器",
            target_audience: "",
            style_template: "",
            source: "creative_divergence",
            source_variant_id: "v1",
          },
          story_dna: { core_contradiction: { statement: "", side_a: "", side_b: "" }, value_stack: [] },
        },
      }),
    );
  });

  it("renders the prefill banner when concept was prefilled from creative_divergence", async () => {
    render(
      <WizardProvider projectId="proj_test">
        <ConceptStep projectId="proj_test" />
      </WizardProvider>,
    );
    await waitFor(() => expect(screen.getByTestId("concept-form")).toBeInTheDocument());
    expect(screen.getByTestId("concept-prefill-banner")).toBeInTheDocument();
    expect(screen.getByTestId("concept-prefill-banner")).toHaveTextContent("由创意发散自动生成，可手动修改");
  });

  it("does NOT render the banner when concept.source is undefined (manual flow)", async () => {
    sessionStorage.setItem(
      "nebula.wizard.state.proj_test",
      JSON.stringify({
        currentStep: 2,
        completedSteps: [1],
        status: "completed",
        data: {
          creative_divergence: null,
          concept: {
            title: "我手写的标题",
            genre: "悬疑",
            premise: "自己敲的",
            tone: "温暖",
            theme: "救赎",
            target_audience: "",
            style_template: "",
            // source omitted on purpose — old manual project, no banner.
          },
          story_dna: { core_contradiction: { statement: "", side_a: "", side_b: "" }, value_stack: [] },
        },
      }),
    );
    render(
      <WizardProvider projectId="proj_test">
        <ConceptStep projectId="proj_test" />
      </WizardProvider>,
    );
    await waitFor(() => expect(screen.getByTestId("concept-form")).toBeInTheDocument());
    expect(screen.queryByTestId("concept-prefill-banner")).not.toBeInTheDocument();
  });
});