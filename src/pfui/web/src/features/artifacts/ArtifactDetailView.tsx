import {
  Button, ButtonGroup, Collapse, H5, HTMLTable, NonIdealState, Spinner, Tag,
} from "@blueprintjs/core";
import { useLayoutEffect, useRef, useState } from "react";
import type { ArtifactDetail, ArtifactFile } from "../../protocol/types";
import { MacLight, MacWindow } from "../../shared/MacWindow";
import { AskLlmDialog } from "./AskLlmDialog";
import { usePinnedFiles } from "./usePinnedFiles";

function PinButton({ pinned, onToggle }: { pinned: boolean; onToggle: () => void }) {
  return <Button
    minimal
    small
    className="mac-pin"
    icon="pin"
    active={pinned}
    aria-pressed={pinned}
    title={pinned ? "Unpin — stop floating this file to the top" : "Pin — float files with this name to the top of every artifact"}
    aria-label={pinned ? "Unpin file" : "Pin file"}
    onClick={onToggle}
  />;
}

function CopyButton({ value }: { value: string }) {
  const [copied, setCopied] = useState(false);
  return <Button
    minimal
    small
    icon={copied ? "tick" : "duplicate"}
    intent={copied ? "success" : "none"}
    title="Copy"
    aria-label="Copy to clipboard"
    onClick={() => void navigator.clipboard.writeText(value).then(() => {
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1200);
    })}
  />;
}

type FileViewMode = "min" | "normal" | "max";

function FileCard({ file, fullContent, pinned, onTogglePin, onLoad }: {
  file: ArtifactFile;
  fullContent?: string;
  pinned: boolean;
  onTogglePin: () => void;
  onLoad: () => Promise<void>;
}) {
  const [mode, setMode] = useState<FileViewMode>("normal");
  const [loading, setLoading] = useState(false);
  const [overflowing, setOverflowing] = useState(false);
  const previewRef = useRef<HTMLPreElement>(null);
  const content = fullContent ?? file.preview ?? "";
  const pin = <PinButton pinned={pinned} onToggle={onTogglePin} />;

  // Let the browser tell us whether the capped preview actually overflows; only
  // then is the maximize option meaningful. Re-measure on content/size changes.
  useLayoutEffect(() => {
    const node = previewRef.current;
    if (!node || mode !== "normal") return;
    const measure = () => setOverflowing(node.scrollHeight > node.clientHeight + 1);
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(node);
    return () => observer.disconnect();
  }, [content, mode]);

  if (file.symlink !== null) return <MacWindow title={file.name} pin={pin} badge={<Tag minimal icon="link" className="mac-badge">symlink</Tag>}>
    <div className="mac-window-body">
      <div className="mac-mono">{file.symlink}</div>
      {file.lldbCommand && <div className="mac-run">
        <div className="mac-run-label">Run command</div>
        <div className="mac-run-command"><span className="mac-mono">{file.lldbCommand}</span><CopyButton value={file.lldbCommand} /></div>
      </div>}
    </div>
  </MacWindow>;
  return <MacWindow
    title={file.name}
    pin={pin}
    badge={file.isBinary ? <Tag minimal className="mac-badge">binary</Tag> : undefined}
    lights={<>
      <MacLight tone="red" label="Title only" active={mode === "min"} onClick={() => setMode("min")} />
      <MacLight tone="yellow" label="Default height" active={mode === "normal"} onClick={() => setMode("normal")} />
      <MacLight tone="green" label="Full content" active={mode === "max"} disabled={!overflowing && mode !== "max"} onClick={() => setMode("max")} />
    </>}
  >
    <Collapse isOpen={mode !== "min"}>
      <pre ref={previewRef} className={`file-preview ${mode === "max" ? "full" : ""}`}><code>{content}</code></pre>
      {!file.previewComplete && fullContent === undefined && <div className="mac-window-footer">
        <Button small icon="document-open" loading={loading} text="Load full file" onClick={() => {
          setLoading(true);
          void onLoad().finally(() => setLoading(false));
        }} />
      </div>}
    </Collapse>
  </MacWindow>;
}

export function ArtifactDetailView({ detail, loading, action, fullFiles, onLinkedArtifact, onRunLldb, onAnalyze, onAskLlm, onLoadFile }: {
  detail: ArtifactDetail | null;
  loading: boolean;
  action: "lldb" | "analyze" | "llm" | null;
  fullFiles: Record<string, string>;
  onLinkedArtifact: (hash: string) => void;
  onRunLldb: () => Promise<void>;
  onAnalyze: () => Promise<void>;
  onAskLlm: (prompt: string, destination: string, filenames: string[]) => Promise<void>;
  onLoadFile: (filename: string) => Promise<void>;
}) {
  const [llmOpen, setLlmOpen] = useState(false);
  const { isPinned, togglePin } = usePinnedFiles();
  if (loading) return <div className="detail-scroll"><div className="centered"><Spinner /></div></div>;
  if (!detail) return <div className="detail-scroll"><NonIdealState icon="search" title="Select an artifact" description="Choose an artifact to inspect its metadata and files." /></div>;
  const busy = action !== null;
  const renderFile = (file: ArtifactFile) => <FileCard
    key={file.name}
    file={file}
    fullContent={fullFiles[file.name]}
    pinned={isPinned(file.name)}
    onTogglePin={() => togglePin(file.name)}
    onLoad={() => onLoadFile(file.name)}
  />;
  const pinnedFiles = detail.files.filter((file) => isPinned(file.name));
  const otherFiles = detail.files.filter((file) => !isPinned(file.name));
  return <>
    <div className="detail-toolbar">
      <div className="artifact-title"><H5>{detail.hash}</H5><CopyButton value={detail.hash} /><Tag intent={detail.type === "core" ? "primary" : "danger"}>{detail.type}</Tag></div>
      <ButtonGroup>
        <Button small icon="chat" text="Ask LLM" loading={action === "llm"} disabled={busy && action !== "llm"} onClick={() => setLlmOpen(true)} />
        <Button small icon="automatic-updates" text="Analyze" loading={action === "analyze"} disabled={busy && action !== "analyze"} onClick={() => void onAnalyze()} />
        <Button small icon="console" text="Run LLDB" loading={action === "lldb"} disabled={busy && action !== "lldb"} onClick={() => void onRunLldb()} />
      </ButtonGroup>
    </div>
    <div className="detail-scroll">
      <div className="detail-stack">
        {pinnedFiles.map(renderFile)}
        <MacWindow title="Info">
          <HTMLTable compact striped className="metadata-table">
            <tbody>{Object.entries(detail.meta).map(([key, value]) => <tr key={key}>
              <th>{key}</th>
              <td>{(key === "linked_crash" || key === "linked_core") && typeof value === "string"
                ? <Button minimal small text={value} onClick={() => onLinkedArtifact(value)} />
                : value == null ? "—"
                : typeof value === "object" ? <span className="metadata-json">{JSON.stringify(value)}</span>
                : String(value)}</td>
            </tr>)}</tbody>
          </HTMLTable>
        </MacWindow>
        {otherFiles.map(renderFile)}
      </div>
    </div>
    <AskLlmDialog detail={detail} open={llmOpen} loading={action === "llm"} onClose={() => setLlmOpen(false)} onSubmit={onAskLlm} />
  </>;
}
