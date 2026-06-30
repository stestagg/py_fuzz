import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ComponentProps } from "react";
import type { InputFilePayload, InputTreeNode } from "../../protocol/types";
import { InputsDialog, flattenInputFiles } from "./InputsDialog";

const TREE: InputTreeNode[] = [
  {
    path: "seed",
    name: "seed",
    kind: "directory",
    children: [
      { path: "seed/case.txt", name: "case.txt", kind: "file", size: 6 },
    ],
  },
];

function renderInputsDialog(overrides: Partial<ComponentProps<typeof InputsDialog>> = {}) {
  const onList = vi.fn().mockResolvedValue({ tree: TREE });
  const onRead = vi.fn().mockImplementation(async (path: string): Promise<InputFilePayload> => ({ path, content: "hello\n", size: 6 }));
  const onSave = vi.fn().mockImplementation(async (path: string, content: string): Promise<InputFilePayload> => ({ path, content, size: content.length }));
  const onDelete = vi.fn().mockResolvedValue({ tree: [{ ...TREE[0], children: [] }] });
  render(<InputsDialog
    projectName="alpha"
    open
    onClose={() => undefined}
    onList={onList}
    onRead={onRead}
    onSave={onSave}
    onDelete={onDelete}
    {...overrides}
  />);
  return { onList, onRead, onSave, onDelete };
}

describe("InputsDialog", () => {
  it("flattens nested input files in display order", () => {
    expect(flattenInputFiles(TREE)).toEqual(["seed/case.txt"]);
  });

  it("loads a selected file and supports edit, revert, and save", async () => {
    const user = userEvent.setup();
    const { onRead, onSave } = renderInputsDialog();

    await screen.findByText("case.txt");
    await user.click(screen.getByText("case.txt"));
    await waitFor(() => expect(onRead).toHaveBeenCalledWith("seed/case.txt"));

    const editor = await screen.findByLabelText("Input contents");
    expect(editor).toHaveValue("hello\n");
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();

    fireEvent.change(editor, { target: { value: "bye\n" } });
    expect(screen.getByRole("button", { name: "Save" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Revert" })).toBeEnabled();

    await user.click(screen.getByRole("button", { name: "Revert" }));
    expect(editor).toHaveValue("hello\n");
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();

    fireEvent.change(editor, { target: { value: "bye\n" } });
    await user.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(onSave).toHaveBeenCalledWith("seed/case.txt", "bye\n"));
  });

  it("confirms and deletes the selected file", async () => {
    const user = userEvent.setup();
    const { onDelete } = renderInputsDialog();

    await screen.findByText("case.txt");
    await user.click(screen.getByText("case.txt"));
    await screen.findByLabelText("Input contents");
    await user.click(screen.getByRole("button", { name: "Delete" }));

    const deleteButtons = await screen.findAllByRole("button", { name: "Delete" });
    await user.click(deleteButtons[deleteButtons.length - 1]);
    await waitFor(() => expect(onDelete).toHaveBeenCalledWith("seed/case.txt"));
  });
});
