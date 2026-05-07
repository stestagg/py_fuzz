import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Check, ChevronRight, Circle, Copy, RefreshCw } from "lucide-react";

type UiConfig = {
  wsUrl?: string;
  initialProject?: string | null;
};

declare global {
  interface Window {
    PYFUZZ_UI_CONFIG?: UiConfig;
  }
}

type ProjectSnapshot = {
  name: string;
  repo: string;
  cloneRef: string;
  fuzzTarget: string;
  importantConfig: Record<string, string | number | boolean | null>;
  paths: {
    root: string;
    config: string;
  };
};

type ArtifactPayload = {
  hash: string;
  type: "crash" | "core";
  path: string;
  hasInput: boolean;
  inputSize: number | null;
};

type ArtifactDetail = {
  hash: string;
  type: "crash" | "core";
  input: string | null;
  lldbOutput: string | null;
  linkedCrash: string | null;
  linkedCore: string | null;
  worker: string | null;
  sourceFilename: string | null;
};

type ArtifactsListResult = {
  artifacts: ArtifactPayload[];
};

type SummaryPayload = {
  status: "ready" | "unavailable";
  updatedAt: string;
  values: Record<string, string | number | boolean | null>;
  error: string | null;
};

type ReadyPayload = {
  projects: string[];
  selectedProject: ProjectSnapshot | null;
  summary: SummaryPayload | null;
};

type ServerMessage = {
  type: string;
  requestId?: string;
  ok?: boolean;
  data?: unknown;
  error?: string;
};

type PendingRequest = {
  resolve: (data: unknown) => void;
  reject: (reason?: unknown) => void;
};

const summaryOrder = [
  "workers",
  "execs_done",
  "execs_per_sec",
  "crashes",
  "saved_crashes",
  "saved_hangs",
  "core_dumps",
  "run_time",
  "corpus_count",
  "edges_found",
];

function wsUrl() {
  const configured = window.PYFUZZ_UI_CONFIG?.wsUrl ?? import.meta.env.VITE_PYFUZZ_WS_URL;
  if (configured) {
    return configured;
  }
  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${protocol}//${window.location.hostname}:8765/ws`;
}

function formatValue(value: string | number | boolean | null | undefined) {
  if (value === null || value === undefined || value === "") {
    return "none";
  }
  if (typeof value === "boolean") {
    return value ? "yes" : "no";
  }
  if (typeof value === "number") {
    return Number.isInteger(value) ? value.toLocaleString() : value.toLocaleString(undefined, { maximumFractionDigits: 2 });
  }
  return value;
}

function labelFromKey(key: string) {
  return key
    .replace(/_/g, " ")
    .replace(/([a-z])([A-Z])/g, "$1 $2")
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function applyDashboardData(data: ReadyPayload, setters: {
  setProjects: (projects: string[]) => void;
  setSelectedProject: (project: ProjectSnapshot | null) => void;
  setSummary: (summary: SummaryPayload | null) => void;
  setInitialPayloadLoaded: (loaded: boolean) => void;
}) {
  setters.setProjects(data.projects);
  setters.setSelectedProject(data.selectedProject);
  setters.setSummary(data.summary);
  setters.setInitialPayloadLoaded(true);
}

function App() {
  const socketRef = useRef<WebSocket | null>(null);
  const pendingRef = useRef<Map<string, PendingRequest>>(new Map());
  const requestCountRef = useRef(0);
  const [connection, setConnection] = useState<"connecting" | "connected" | "disconnected">("connecting");
  const [error, setError] = useState<string | null>(null);
  const [projects, setProjects] = useState<string[]>([]);
  const [initialPayloadLoaded, setInitialPayloadLoaded] = useState(false);
  const [selectedProject, setSelectedProject] = useState<ProjectSnapshot | null>(null);
  const [summary, setSummary] = useState<SummaryPayload | null>(null);
  const [summaryLoading, setSummaryLoading] = useState(false);
  const [artifacts, setArtifacts] = useState<ArtifactPayload[]>([]);
  const [artifactsLoading, setArtifactsLoading] = useState(false);
  const [selectedArtifactHash, setSelectedArtifactHash] = useState<string | null>(null);
  const [artifactDetail, setArtifactDetail] = useState<ArtifactDetail | null>(null);
  const [artifactDetailLoading, setArtifactDetailLoading] = useState(false);

  useEffect(() => {
    const socket = new WebSocket(wsUrl());
    socketRef.current = socket;
    setConnection("connecting");

    socket.addEventListener("open", () => {
      setConnection("connected");
      setError(null);
    });

    socket.addEventListener("message", (event) => {
      const message = JSON.parse(event.data) as ServerMessage;
      if (message.type === "connection:ready") {
        applyDashboardData(message.data as ReadyPayload, {
          setProjects,
          setSelectedProject,
          setSummary,
          setInitialPayloadLoaded,
        });
        return;
      }

      if (message.requestId) {
        const pending = pendingRef.current.get(message.requestId);
        if (!pending) {
          return;
        }
        pendingRef.current.delete(message.requestId);
        if (message.ok) {
          pending.resolve(message.data);
        } else {
          pending.reject(new Error(message.error ?? "Request failed"));
        }
      }
    });

    socket.addEventListener("close", () => {
      setConnection("disconnected");
      for (const pending of pendingRef.current.values()) {
        pending.reject(new Error("WebSocket disconnected"));
      }
      pendingRef.current.clear();
    });

    socket.addEventListener("error", () => {
      setError("WebSocket connection failed");
    });

    return () => {
      socket.close();
    };
  }, []);

  const sendRequest = useCallback((type: string, payload: Record<string, unknown> = {}) => {
    const socket = socketRef.current;
    if (!socket || socket.readyState !== WebSocket.OPEN) {
      return Promise.reject(new Error("WebSocket is not connected"));
    }
    const requestId = `${Date.now()}-${requestCountRef.current++}`;
    const request = { type, requestId, ...payload };
    socket.send(JSON.stringify(request));
    return new Promise<unknown>((resolve, reject) => {
      pendingRef.current.set(requestId, { resolve, reject });
    });
  }, []);

  const selectProject = useCallback(async (projectName: string) => {
    setError(null);
    try {
      const data = await sendRequest("project:select", { projectName });
      applyDashboardData(data as ReadyPayload, {
        setProjects,
        setSelectedProject,
        setSummary,
        setInitialPayloadLoaded,
      });
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, [sendRequest]);

  const refreshSummary = useCallback(async () => {
    setError(null);
    setSummaryLoading(true);
    try {
      const data = await sendRequest("summary:refresh") as { summary: SummaryPayload };
      setSummary(data.summary);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSummaryLoading(false);
    }
  }, [sendRequest]);

  const loadArtifacts = useCallback(async () => {
    setArtifactsLoading(true);
    try {
      let result = await sendRequest("artifacts:list") as ArtifactsListResult;
      if (result.artifacts.length === 0) {
        await sendRequest("artifacts:sync");
        result = await sendRequest("artifacts:list") as ArtifactsListResult;
      }
      setArtifacts(result.artifacts);
    } catch {
      // silently ignore artifact load errors
    } finally {
      setArtifactsLoading(false);
    }
  }, [sendRequest]);

  const refreshArtifacts = useCallback(async () => {
    setArtifactsLoading(true);
    try {
      await sendRequest("artifacts:sync");
      const result = await sendRequest("artifacts:list") as ArtifactsListResult;
      setArtifacts(result.artifacts);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setArtifactsLoading(false);
    }
  }, [sendRequest]);

  const selectArtifact = useCallback(async (hash: string) => {
    setSelectedArtifactHash(hash);
    setArtifactDetailLoading(true);
    try {
      const detail = await sendRequest("artifact:get", { hash }) as ArtifactDetail;
      setArtifactDetail(detail);
    } catch {
      setArtifactDetail(null);
    } finally {
      setArtifactDetailLoading(false);
    }
  }, [sendRequest]);

  useEffect(() => {
    if (!selectedProject) {
      setArtifacts([]);
      setSelectedArtifactHash(null);
      setArtifactDetail(null);
      return;
    }
    void loadArtifacts();
  }, [selectedProject, loadArtifacts]);

  if (!selectedProject) {
    return (
      <main className="selection-shell">
        <section className="selection-panel">
          <div className="selection-heading">
            <h1>pyfuzz</h1>
            <ConnectionBadge connection={connection} />
          </div>
          {error && <div className="error-line">{error}</div>}
          <div className="project-list">
            {!initialPayloadLoaded && <div className="empty-row">Loading projects</div>}
            {initialPayloadLoaded && projects.map((project) => (
              <button key={project} className="project-row" onClick={() => void selectProject(project)}>
                <span>{project}</span>
                <ChevronRight size={18} aria-hidden />
              </button>
            ))}
            {initialPayloadLoaded && projects.length === 0 && <div className="empty-row">No projects found</div>}
          </div>
        </section>
      </main>
    );
  }

  return (
    <main className="dashboard-shell">
      <header className="topbar">
        <div className="project-title">
          <div>
            <h1>{selectedProject.name}</h1>
            <span>{selectedProject.repo}</span>
          </div>
          <ConnectionBadge connection={connection} />
        </div>
        <ConfigStrip project={selectedProject} />
      </header>

      <section className="summary-panel">
        <div className="panel-heading">
          <h2>Summary</h2>
          <button
            className="icon-button"
            type="button"
            onClick={() => void refreshSummary()}
            title="Refresh summary"
            aria-label="Refresh summary"
            disabled={summaryLoading}
          >
            <RefreshCw size={16} aria-hidden />
          </button>
        </div>
        <SummaryView summary={summary} />
      </section>

      {error && <div className="error-line dashboard-error">{error}</div>}

      <section className="workspace-split">
        <aside className="artifact-panel">
          <div className="panel-heading">
            <h2>Artifacts</h2>
            <button
              className="icon-button"
              type="button"
              onClick={() => void refreshArtifacts()}
              title="Sync and refresh artifacts"
              aria-label="Sync and refresh artifacts"
              disabled={artifactsLoading}
            >
              <RefreshCw size={16} aria-hidden />
            </button>
          </div>
          <ArtifactTree
            artifacts={artifacts}
            loading={artifactsLoading}
            selectedHash={selectedArtifactHash}
            onSelect={(hash) => void selectArtifact(hash)}
          />
        </aside>
        <section className="detail-panel" aria-label="Artifact detail">
          {selectedArtifactHash && (
            <ArtifactDetailView
              detail={artifactDetail}
              loading={artifactDetailLoading}
              onSelect={selectArtifact}
            />
          )}
        </section>
      </section>
    </main>
  );
}

function ConnectionBadge({ connection }: { connection: "connecting" | "connected" | "disconnected" }) {
  return (
    <span className={`connection-badge ${connection}`}>
      <Circle size={9} fill="currentColor" aria-hidden />
      {connection}
    </span>
  );
}

function ConfigStrip({ project }: { project: ProjectSnapshot }) {
  const config = project.importantConfig;
  const items = [
    ["Clone", project.cloneRef],
    ["Target", project.fuzzTarget.split("/").pop() ?? project.fuzzTarget],
    ["ASAN", config.asan],
    ["PEG", config.fuzzPeg],
    ["VM", `${formatValue(config.vmMem)} MB`],
    ["CPU", config.ncpu],
    ["Timeout", `${formatValue(config.fuzzTimeoutMs)} ms`],
    ["Memory", `${formatValue(config.fuzzMemLimit)} MB`],
  ];
  return (
    <div className="config-strip">
      {items.map(([label, value]) => (
        <div className="config-chip" key={String(label)}>
          <span>{label}</span>
          <strong>{formatValue(value)}</strong>
        </div>
      ))}
    </div>
  );
}

function SummaryView({ summary }: { summary: SummaryPayload | null }) {
  const summaryItems = useMemo(() => {
    if (!summary) {
      return [];
    }
    const keys = new Set(Object.keys(summary.values));
    const ordered = summaryOrder.filter((key) => keys.has(key));
    const rest = [...keys].filter((key) => !summaryOrder.includes(key) && key !== "project").sort();
    return [...ordered, ...rest].map((key) => [key, summary.values[key]] as const);
  }, [summary]);

  if (!summary) {
    return <div className="summary-grid empty-summary" />;
  }

  return (
    <>
      <div className="summary-grid">
        {summaryItems.map(([key, value]) => (
          <div className="summary-cell" key={key}>
            <span>{labelFromKey(key)}</span>
            <strong>{formatValue(value)}</strong>
          </div>
        ))}
      </div>
      {summary.error && <div className="summary-error">{summary.error}</div>}
    </>
  );
}

function ArtifactTree({
  artifacts,
  loading,
  selectedHash,
  onSelect,
}: {
  artifacts: ArtifactPayload[];
  loading: boolean;
  selectedHash: string | null;
  onSelect: (hash: string) => void;
}) {
  const [expanded, setExpanded] = useState(true);

  if (loading && artifacts.length === 0) {
    return <div className="tree-empty">Loading…</div>;
  }

  if (artifacts.length === 0) {
    return <div className="tree-empty">No artifacts</div>;
  }

  return (
    <div className="artifact-tree">
      <button
        className="tree-group-header"
        type="button"
        onClick={() => setExpanded((e) => !e)}
        aria-expanded={expanded}
      >
        <ChevronRight size={13} className={expanded ? "tree-chevron expanded" : "tree-chevron"} aria-hidden />
        <span>Ungrouped</span>
        <span className="tree-count">{artifacts.length}</span>
      </button>
      {expanded && (
        <ul className="tree-children" role="list">
          {artifacts.map((artifact) => (
            <li key={artifact.hash}>
              <button
                type="button"
                className={`tree-leaf-button${artifact.hash === selectedHash ? " tree-leaf-selected" : ""}`}
                onClick={() => onSelect(artifact.hash)}
              >
                <span className={`tree-type-badge tree-type-${artifact.type}`}>{artifact.type[0].toUpperCase()}</span>
                <span className="tree-hash">{artifact.hash}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function CopiableText({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);

  const handleCopy = useCallback(() => {
    navigator.clipboard.writeText(text).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    });
  }, [text]);

  return (
    <span className="copiable-text">
      <span className="copiable-text-value">{text}</span>
      <button type="button" className={`copiable-copy-btn${copied ? " copied" : ""}`} onClick={handleCopy} title="Copy to clipboard">
        {copied ? <Check size={13} /> : <Copy size={13} />}
      </button>
    </span>
  );
}

function ArtifactDetailView({ detail, loading, onSelect }: { detail: ArtifactDetail | null; loading: boolean; onSelect: (hash: string) => void }) {
  if (loading) {
    return (
      <div className="detail-stack">
        <div className="detail-card">
          <div className="detail-loading">Loading…</div>
        </div>
      </div>
    );
  }

  if (!detail) {
    return null;
  }

  const linkedHash = detail.linkedCrash ?? detail.linkedCore;
  const linkedLabel = detail.linkedCrash ? "linked crash" : detail.linkedCore ? "linked core" : null;

  return (
    <div className="detail-stack">
      <div className="detail-card">
        <div className="detail-card-title">
          <CopiableText text={detail.hash} />
          <span className={`detail-type-badge detail-type-${detail.type}`}>({detail.type})</span>
          {detail.sourceFilename != null && (
            <span className="detail-source-filename">{detail.sourceFilename.split("/").pop()}</span>
          )}
        </div>
        {(detail.worker != null || detail.sourceFilename != null) && (
          <div className="detail-source-meta">
            {detail.worker != null && (
              <span className="detail-source-row">
                <span className="detail-source-label">worker</span>
                <CopiableText text={detail.worker} />
              </span>
            )}
            {detail.sourceFilename != null && (
              <span className="detail-source-row">
                <span className="detail-source-label">file</span>
                <CopiableText text={detail.sourceFilename} />
              </span>
            )}
          </div>
        )}
        {linkedHash && (
          <button type="button" className="detail-linked-artifact" onClick={() => onSelect(linkedHash)}>
            {linkedLabel}: {linkedHash}
          </button>
        )}
        {detail.input != null && <>
          <div className="detail-field-label">Input</div>
          <pre className="detail-input-block"><code>{detail.input}</code></pre>
        </>}
      </div>
      {detail.lldbOutput != null && (
        <div className="detail-card">
          <div className="detail-field-label">LLDB</div>
          <pre className="detail-input-block"><code>{detail.lldbOutput}</code></pre>
        </div>
      )}
    </div>
  );
}

export default App;
