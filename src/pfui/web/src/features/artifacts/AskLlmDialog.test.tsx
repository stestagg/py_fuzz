import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ArtifactDetail } from "../../protocol/types";
import { AskLlmDialog, nextFilename } from "./AskLlmDialog";

const DETAIL: ArtifactDetail = {
  hash: "abc",
  type: "crash",
  meta: {},
  files: [],
  llmFiles: ["input.txt"],
};

describe("AskLlmDialog", () => {
  it("uses the first available LLM response name", () => {
    expect(nextFilename(["input.txt", "llm_chat_1", "llm_chat_3"])).toBe("llm_chat_2");
  });

  it("validates the prompt and destination before submitting", async () => {
    const user = userEvent.setup();
    const submit = vi.fn().mockResolvedValue(undefined);
    render(<AskLlmDialog detail={DETAIL} open loading={false} onClose={() => undefined} onSubmit={submit} />);
    const ask = screen.getByRole("button", { name: "Ask LLM" });
    expect(ask).toBeDisabled();
    await user.type(screen.getByLabelText("Question"), "What happened?");
    expect(ask).toBeEnabled();
    await user.clear(screen.getByLabelText("Response file"));
    await user.type(screen.getByLabelText("Response file"), "../bad");
    expect(ask).toBeDisabled();
  });
});
