import { Button, Checkbox, Dialog, DialogBody, DialogFooter, FormGroup, InputGroup, TextArea } from "@blueprintjs/core";
import { useEffect, useMemo, useState } from "react";
import type { ArtifactDetail } from "../../protocol/types";

function nextFilename(files: string[]): string {
  const existing = new Set(files);
  let index = 1;
  while (existing.has(`llm_chat_${index}`)) index += 1;
  return `llm_chat_${index}`;
}

export function AskLlmDialog({ detail, open, loading, onClose, onSubmit }: {
  detail: ArtifactDetail;
  open: boolean;
  loading: boolean;
  onClose: () => void;
  onSubmit: (prompt: string, destination: string, filenames: string[]) => Promise<void>;
}) {
  const [selected, setSelected] = useState<string[]>(detail.llmFiles);
  const [prompt, setPrompt] = useState("");
  const [destination, setDestination] = useState(() => nextFilename(detail.llmFiles));
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (open) {
      setSelected(detail.llmFiles);
      setPrompt("");
      setDestination(nextFilename(detail.llmFiles));
      setError(null);
    }
  }, [detail, open]);

  const destinationError = useMemo(() => {
    const value = destination.trim();
    if (!value || value === "." || value === ".." || value.includes("/") || value.includes("\\") || value.endsWith(".marker")) {
      return "Use a visible filename local to the artifact.";
    }
    if (detail.llmFiles.includes(value)) return "That file already exists.";
    return null;
  }, [destination, detail.llmFiles]);

  const submit = async () => {
    setError(null);
    try {
      await onSubmit(prompt.trim(), destination.trim(), selected);
      onClose();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    }
  };

  return <Dialog isOpen={open} onClose={onClose} title="Ask the LLM" icon="chat" canEscapeKeyClose={!loading} canOutsideClickClose={!loading}>
    <DialogBody>
      <FormGroup label="Artifact files" helperText="Select the files to include as context.">
        <div className="file-selection">
          {detail.llmFiles.map((file) => <Checkbox
            key={file}
            checked={selected.includes(file)}
            disabled={loading}
            label={file}
            onChange={() => setSelected((current) => current.includes(file) ? current.filter((value) => value !== file) : [...current, file])}
          />)}
        </div>
      </FormGroup>
      <FormGroup label="Question" labelFor="llm-question">
        <TextArea id="llm-question" autoResize fill rows={6} value={prompt} disabled={loading} onChange={(event) => setPrompt(event.target.value)} />
      </FormGroup>
      <FormGroup label="Response file" labelFor="llm-destination" helperText={destinationError ?? error} intent={destinationError || error ? "danger" : "none"}>
        <InputGroup id="llm-destination" value={destination} disabled={loading} intent={destinationError ? "danger" : "none"} onChange={(event) => setDestination(event.target.value)} />
      </FormGroup>
    </DialogBody>
    <DialogFooter actions={<>
      <Button text="Cancel" disabled={loading} onClick={onClose} />
      <Button intent="primary" text="Ask LLM" loading={loading} disabled={!prompt.trim() || !!destinationError} onClick={() => void submit()} />
    </>} />
  </Dialog>;
}

export { nextFilename };
