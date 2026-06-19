import { type KeyboardEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ArrowLeft, ArrowRight, Check, ChevronRight, Circle, Copy, Hammer, LoaderCircle, MessageCircle, Play, Plus, RefreshCw, Trash2, X, Zap } from "lucide-react";

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
  groupValues: ArtifactGroupValue[];
};

type ArtifactGroupValue = {
  value: string;
  label: string;
};

type ArtifactFile = {
  name: string;
  symlink: string | null;
  preview: string | null;
  previewComplete: boolean;
  isBinary: boolean;
  lldbCommand?: string | null;
};

type ArtifactDetail = {
  hash: string;
  type: "crash" | "core";
  meta: Record<string, unknown>;
  files: ArtifactFile[];
  llmFiles: string[];
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

type TrendPoint = {
  time: number;
  execs_done: number;
  execs_per_sec: number;
  corpus_count: number;
  edges_found: number;
};

type TrendMetric = "execs_done" | "execs_per_sec" | "corpus_count" | "edges_found";

type TaskInfo = {
  id: string;
  name: string;
  kind: string;
  project: string | null;
  startedAt: string;
  finishedAt: string | null;
  status: "running" | "done" | "error" | "cancelled";
  error: string | null;
  stoppable: boolean;
};

type TaskAction = "fuzz" | "build" | "clean";

type ReadyPayload = {
  projects: string[];
  selectedProject: ProjectSnapshot | null;
  summary: SummaryPayload | null;
  tasks?: TaskInfo[];
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

const DEFAULT_GROUP_SPECS = ["type"];

function wsUrl() {
  const configured = window.PYFUZZ_UI_CONFIG?.wsUrl ?? import.meta.env.VITE_PYFUZZ_WS_URL;
  if (configured) {
    return configured;
  }
  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${protocol}//${window.location.hostname}:8767/ws`;
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

function isSafeGroupFilename(filename: string) {
  return filename !== "" && filename !== "." && filename !== ".." && !filename.includes("/") && !filename.includes("\\");
}

function validateArtifactGroupSpec(raw: string) {
  const spec = raw.trim();
  if (!spec) {
    return "Grouping spec cannot be empty";
  }
  if (spec === "type") {
    return null;
  }
  const separator = spec.indexOf(":");
  if (separator === -1) {
    return "Use type, file:<name>, meta:<key>, or exists:<filename>";
  }

  const key = spec.slice(0, separator);
  const value = spec.slice(separator + 1);
  if (key === "meta") {
    return value ? null : "meta: requires a key";
  }
  if (key === "file" || key === "exists") {
    return isSafeGroupFilename(value) ? null : `${key}: filename must be local to the artifact directory`;
  }
  return "Use type, file:<name>, meta:<key>, or exists:<filename>";
}

function applyDashboardData(data: ReadyPayload, setters: {
  setProjects: (projects: string[]) => void;
  setSelectedProject: (project: ProjectSnapshot | null) => void;
  setSummary: (summary: SummaryPayload | null) => void;
  setInitialPayloadLoaded: (loaded: boolean) => void;
  setTasks: (tasks: TaskInfo[]) => void;
}) {
  setters.setProjects(data.projects);
  setters.setSelectedProject(data.selectedProject);
  setters.setSummary(data.summary);
  if (data.tasks) {
    setters.setTasks(data.tasks);
  }
  setters.setInitialPayloadLoaded(true);
}

function formatElapsed(startedAt: string, finishedAt: string | null) {
  const start = Date.parse(startedAt);
  const end = finishedAt ? Date.parse(finishedAt) : Date.now();
  const totalSeconds = Math.max(0, Math.floor((end - start) / 1000));
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;
  if (hours > 0) {
    return `${hours}:${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
  }
  return `${minutes}:${String(seconds).padStart(2, "0")}`;
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
  const [groupSpecs, setGroupSpecs] = useState<string[]>(DEFAULT_GROUP_SPECS);
  const [selectedArtifactHash, setSelectedArtifactHash] = useState<string | null>(null);
  const selectedArtifactHashRef = useRef<string | null>(null);
  const [artifactDetail, setArtifactDetail] = useState<ArtifactDetail | null>(null);
  const [artifactDetailLoading, setArtifactDetailLoading] = useState(false);
  const [lldbRunning, setLldbRunning] = useState(false);
  const [analyzeRunning, setAnalyzeRunning] = useState(false);
  const [llmRunning, setLlmRunning] = useState(false);
  const [analyzeCoresRunning, setAnalyzeCoresRunning] = useState(false);
  const [expandedFiles, setExpandedFiles] = useState<Record<string, string>>({});
  const [tasks, setTasks] = useState<TaskInfo[]>([]);
  const [trendData, setTrendData] = useState<TrendPoint[]>([]);
  const [trendMetric, setTrendMetric] = useState<TrendMetric>("execs_done");

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
          setTasks,
        });
        return;
      }

      if (message.type === "tasks:update") {
        setTasks((message.data as { tasks: TaskInfo[] }).tasks);
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
        setTasks,
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

  const loadTrendData = useCallback(async () => {
    try {
      const data = await sendRequest("plot:data") as { points: TrendPoint[] };
      setTrendData(data.points);
    } catch {
      // silently ignore
    }
  }, [sendRequest]);

  const loadArtifacts = useCallback(async () => {
    setArtifactsLoading(true);
    try {
      let result = await sendRequest("artifacts:list", { groupSpecs }) as ArtifactsListResult;
      if (result.artifacts.length === 0) {
        await sendRequest("artifacts:sync");
        result = await sendRequest("artifacts:list", { groupSpecs }) as ArtifactsListResult;
      }
      setArtifacts(result.artifacts);
    } catch {
      // silently ignore artifact load errors
    } finally {
      setArtifactsLoading(false);
    }
  }, [groupSpecs, sendRequest]);

  const refreshArtifacts = useCallback(async () => {
    setArtifactsLoading(true);
    try {
      await sendRequest("artifacts:sync");
      const result = await sendRequest("artifacts:list", { groupSpecs }) as ArtifactsListResult;
      setArtifacts(result.artifacts);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setArtifactsLoading(false);
    }
  }, [groupSpecs, sendRequest]);

  const runAnalyze = useCallback(async (hash: string) => {
    setAnalyzeRunning(true);
    setError(null);
    try {
      const detail = await sendRequest("artifact:analyze-core", { hash }) as ArtifactDetail;
      setArtifactDetail(detail);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setAnalyzeRunning(false);
    }
  }, [sendRequest]);

  const analyzeCores = useCallback(async () => {
    setAnalyzeCoresRunning(true);
    setError(null);
    try {
      await sendRequest("artifacts:analyze-cores");
      const result = await sendRequest("artifacts:list", { groupSpecs }) as ArtifactsListResult;
      setArtifacts(result.artifacts);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setAnalyzeCoresRunning(false);
    }
  }, [sendRequest, groupSpecs]);

  const runLldb = useCallback(async (hash: string) => {
    setLldbRunning(true);
    setError(null);
    try {
      const detail = await sendRequest("artifact:run-lldb", { hash }) as ArtifactDetail;
      setArtifactDetail(detail);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLldbRunning(false);
    }
  }, [sendRequest]);

  const askLlm = useCallback(async (
    hash: string,
    prompt: string,
    dest: string,
    filenames: string[],
  ) => {
    setLlmRunning(true);
    setError(null);
    try {
      const detail = await sendRequest("artifact:ask-llm", { hash, prompt, dest, filenames }) as ArtifactDetail;
      if (selectedArtifactHashRef.current === hash) {
        setArtifactDetail(detail);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      throw err;
    } finally {
      setLlmRunning(false);
    }
  }, [sendRequest]);

  const selectArtifact = useCallback(async (hash: string) => {
    selectedArtifactHashRef.current = hash;
    setSelectedArtifactHash(hash);
    setArtifactDetailLoading(true);
    setExpandedFiles({});
    try {
      const detail = await sendRequest("artifact:get", { hash }) as ArtifactDetail;
      setArtifactDetail(detail);
    } catch {
      setArtifactDetail(null);
    } finally {
      setArtifactDetailLoading(false);
    }
  }, [sendRequest]);

  const loadFullFile = useCallback(async (hash: string, filename: string) => {
    const data = await sendRequest("artifact:file", { hash, filename }) as { content: string };
    setExpandedFiles(prev => ({ ...prev, [filename]: data.content }));
  }, [sendRequest]);

  const startTask = useCallback(async (action: TaskAction, params: Record<string, unknown>) => {
    setError(null);
    try {
      await sendRequest("task:start", { action, params });
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, [sendRequest]);

  const stopTask = useCallback((taskId: string) => {
    sendRequest("task:stop", { taskId }).catch((err) => {
      setError(err instanceof Error ? err.message : String(err));
    });
  }, [sendRequest]);

  const fuzzRunning = tasks.some(
    (task) => task.status === "running" && task.kind === "fuzz" && task.project === selectedProject?.name,
  );

  useEffect(() => {
    if (!selectedProject) {
      setArtifacts([]);
      selectedArtifactHashRef.current = null;
      setSelectedArtifactHash(null);
      setArtifactDetail(null);
      setTrendData([]);
      return;
    }
    void loadArtifacts();
    void loadTrendData();
  }, [selectedProject, loadArtifacts, loadTrendData]);

  useEffect(() => {
    if (!selectedProject || connection !== "connected") {
      return;
    }
    const id = setInterval(() => { void refreshSummary(); void loadTrendData(); }, 60_000);
    return () => clearInterval(id);
  }, [selectedProject, connection, refreshSummary, loadTrendData]);

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
        <TaskToasts tasks={tasks} onStop={stopTask} />
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
          <div className="project-title-actions">
            <ActionMenu fuzzRunning={fuzzRunning} onStart={(action, params) => void startTask(action, params)} />
            <ConnectionBadge connection={connection} />
          </div>
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
              onClick={() => void analyzeCores()}
              title="Analyze all cores"
              aria-label="Analyze all cores"
              disabled={analyzeCoresRunning || artifactsLoading}
            >
              <Zap size={16} aria-hidden />
            </button>
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
          <GroupingEditor groupSpecs={groupSpecs} onChange={setGroupSpecs} />
          <ArtifactTree
            artifacts={artifacts}
            groupSpecs={groupSpecs}
            loading={artifactsLoading}
            selectedHash={selectedArtifactHash}
            onSelect={(hash) => void selectArtifact(hash)}
          />
        </aside>
        <section className="detail-panel" aria-label="Artifact detail">
          {selectedArtifactHash ? (
            <ArtifactDetailView
              detail={artifactDetail}
              loading={artifactDetailLoading}
              onSelect={selectArtifact}
              onRunLldb={(hash) => void runLldb(hash)}
              lldbRunning={lldbRunning}
              onRunAnalyze={(hash) => void runAnalyze(hash)}
              analyzeRunning={analyzeRunning}
              onAskLlm={askLlm}
              llmRunning={llmRunning}
              expandedFiles={expandedFiles}
              onLoadFile={(filename) => void loadFullFile(selectedArtifactHash!, filename)}
            />
          ) : (
            <TrendChart data={trendData} metric={trendMetric} onMetricChange={setTrendMetric} />
          )}
        </section>
      </section>
      <TaskToasts tasks={tasks} onStop={stopTask} />
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

const CLEAN_COMPONENTS = ["all", "outputs", "build", "analysis"];
const BUILD_TARGETS = ["all", "py", "helpers"] as const;

function ActionMenu({ fuzzRunning, onStart }: {
  fuzzRunning: boolean;
  onStart: (action: TaskAction, params: Record<string, unknown>) => void;
}) {
  const [open, setOpen] = useState(false);
  const [view, setView] = useState<"root" | "fuzz" | "build" | "clean">("root");
  const [instances, setInstances] = useState("10");
  const [aflDebug, setAflDebug] = useState(false);
  const [monitor, setMonitor] = useState(true);
  const [cleanComponents, setCleanComponents] = useState<string[]>([]);
  const containerRef = useRef<HTMLDivElement | null>(null);

  const close = useCallback(() => {
    setOpen(false);
    setView("root");
  }, []);

  useEffect(() => {
    if (!open) {
      return;
    }
    const onMouseDown = (event: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(event.target as Node)) {
        close();
      }
    };
    document.addEventListener("mousedown", onMouseDown);
    return () => document.removeEventListener("mousedown", onMouseDown);
  }, [open, close]);

  const startFuzz = () => {
    const parsed = Number.parseInt(instances, 10);
    onStart("fuzz", {
      instances: Number.isFinite(parsed) && parsed > 0 ? parsed : 10,
      aflDebug,
      monitor,
    });
    close();
  };

  const startBuild = (target: string) => {
    onStart("build", { target });
    close();
  };

  const startClean = () => {
    const components = cleanComponents.includes("all") ? ["all"] : cleanComponents;
    onStart("clean", { components });
    setCleanComponents([]);
    close();
  };

  const toggleCleanComponent = (component: string) => {
    setCleanComponents((prev) =>
      prev.includes(component) ? prev.filter((item) => item !== component) : [...prev, component],
    );
  };

  return (
    <div className="action-menu" ref={containerRef}>
      <button
        className="action-menu-trigger"
        type="button"
        onClick={() => (open ? close() : setOpen(true))}
        aria-expanded={open}
      >
        <Play size={14} aria-hidden />
        Actions
      </button>
      {open && (
        <div className="action-menu-popup">
          {view === "root" && (
            <>
              <button className="action-menu-item" type="button" disabled={fuzzRunning} onClick={() => setView("fuzz")}>
                <Zap size={15} aria-hidden />
                Fuzz
                {fuzzRunning && <span className="action-menu-hint">running</span>}
              </button>
              <button className="action-menu-item" type="button" onClick={() => setView("build")}>
                <Hammer size={15} aria-hidden />
                Build
              </button>
              <button className="action-menu-item" type="button" onClick={() => setView("clean")}>
                <Trash2 size={15} aria-hidden />
                Clean
              </button>
            </>
          )}
          {view === "fuzz" && (
            <div className="action-menu-panel">
              <button className="action-menu-back" type="button" onClick={() => setView("root")}>
                <ArrowLeft size={13} aria-hidden />
                Back
              </button>
              <label className="action-menu-field">
                Instances (-j)
                <input
                  type="number"
                  min={1}
                  max={128}
                  value={instances}
                  onChange={(event) => setInstances(event.target.value)}
                />
              </label>
              <label className="action-menu-checkbox">
                <input type="checkbox" checked={aflDebug} onChange={(event) => setAflDebug(event.target.checked)} />
                AFL debug
              </label>
              <label className="action-menu-checkbox">
                <input type="checkbox" checked={monitor} onChange={(event) => setMonitor(event.target.checked)} />
                Monitor + notifications
              </label>
              <button className="action-menu-start" type="button" onClick={startFuzz}>
                Start fuzzing
              </button>
            </div>
          )}
          {view === "build" && (
            <div className="action-menu-panel">
              <button className="action-menu-back" type="button" onClick={() => setView("root")}>
                <ArrowLeft size={13} aria-hidden />
                Back
              </button>
              {BUILD_TARGETS.map((target) => (
                <button key={target} className="action-menu-item" type="button" onClick={() => startBuild(target)}>
                  <Hammer size={15} aria-hidden />
                  {target}
                </button>
              ))}
            </div>
          )}
          {view === "clean" && (
            <div className="action-menu-panel">
              <button className="action-menu-back" type="button" onClick={() => setView("root")}>
                <ArrowLeft size={13} aria-hidden />
                Back
              </button>
              {CLEAN_COMPONENTS.map((component) => (
                <label key={component} className="action-menu-checkbox">
                  <input
                    type="checkbox"
                    checked={cleanComponents.includes(component)}
                    disabled={component !== "all" && cleanComponents.includes("all")}
                    onChange={() => toggleCleanComponent(component)}
                  />
                  {component}
                </label>
              ))}
              <button
                className="action-menu-start"
                type="button"
                disabled={cleanComponents.length === 0}
                onClick={startClean}
              >
                Clean
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function TaskToasts({ tasks, onStop }: { tasks: TaskInfo[]; onStop: (taskId: string) => void }) {
  const [confirmingId, setConfirmingId] = useState<string | null>(null);
  const [dismissed, setDismissed] = useState<Set<string>>(new Set());
  const [, setTick] = useState(0);

  const anyRunning = tasks.some((task) => task.status === "running");
  useEffect(() => {
    if (!anyRunning) {
      return;
    }
    const id = setInterval(() => setTick((tick) => tick + 1), 1000);
    return () => clearInterval(id);
  }, [anyRunning]);

  // Auto-dismiss finished toasts after 2s; errors stay until dismissed by hand.
  useEffect(() => {
    const finished = tasks.filter(
      (task) => (task.status === "done" || task.status === "cancelled") && !dismissed.has(task.id),
    );
    if (finished.length === 0) {
      return;
    }
    const timers = finished.map((task) =>
      setTimeout(() => {
        setDismissed((prev) => new Set(prev).add(task.id));
      }, 2000),
    );
    return () => timers.forEach(clearTimeout);
  }, [tasks, dismissed]);

  const visible = tasks.filter((task) => !dismissed.has(task.id));
  if (visible.length === 0) {
    return null;
  }

  const dismiss = (taskId: string) => {
    setDismissed((prev) => new Set(prev).add(taskId));
  };

  return (
    <div className="task-toasts">
      {visible.map((task) => (
        <div key={task.id} className={`task-toast ${task.status}`}>
          {task.status === "running" ? (
            <Circle size={9} className="task-toast-status task-toast-dot" aria-hidden />
          ) : task.status === "done" ? (
            <Check size={15} className="task-toast-status" aria-hidden />
          ) : (
            <X size={15} className="task-toast-status" aria-hidden />
          )}
          <div className="task-toast-body">
            <span className="task-toast-label">{task.name}</span>
            <span className="task-toast-elapsed">
              {`${task.status} · `}
              {formatElapsed(task.startedAt, task.finishedAt)}
            </span>
            {task.error && <span className="task-toast-error-line">{task.error}</span>}
          </div>
          {task.status === "running" ? (
            confirmingId === task.id ? (
              <span className="task-toast-confirm">
                Stop?
                <button
                  className="task-toast-confirm-yes"
                  type="button"
                  onClick={() => {
                    onStop(task.id);
                    setConfirmingId(null);
                  }}
                >
                  Yes
                </button>
                <button className="task-toast-confirm-no" type="button" onClick={() => setConfirmingId(null)}>
                  No
                </button>
              </span>
            ) : (
              task.stoppable && (
                <button
                  className="task-toast-stop"
                  type="button"
                  title="Stop task"
                  aria-label={`Stop ${task.name}`}
                  onClick={() => setConfirmingId(task.id)}
                >
                  <X size={14} aria-hidden />
                </button>
              )
            )
          ) : (
            <button
              className="task-toast-stop"
              type="button"
              title="Dismiss"
              aria-label={`Dismiss ${task.name}`}
              onClick={() => dismiss(task.id)}
            >
              <X size={14} aria-hidden />
            </button>
          )}
        </div>
      ))}
    </div>
  );
}

function ConfigStrip({ project }: { project: ProjectSnapshot }) {
  const config = project.importantConfig;
  const items = [
    ["Clone", project.cloneRef],
    ["Target", project.fuzzTarget.split("/").pop() ?? project.fuzzTarget],
    ["ASAN", config.asan],
    ["Harness", config.harness],
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

function GroupingEditor({ groupSpecs, onChange }: {
  groupSpecs: string[];
  onChange: (groupSpecs: string[]) => void;
}) {
  const [newSpec, setNewSpec] = useState("");
  const [editingIndex, setEditingIndex] = useState<number | null>(null);
  const [editingDraft, setEditingDraft] = useState("");
  const [validationError, setValidationError] = useState<string | null>(null);

  const commitNewSpec = useCallback(() => {
    const spec = newSpec.trim();
    if (!spec) {
      return;
    }
    const error = validateArtifactGroupSpec(spec);
    if (error) {
      setValidationError(error);
      return;
    }
    onChange([...groupSpecs, spec]);
    setNewSpec("");
    setValidationError(null);
  }, [groupSpecs, newSpec, onChange]);

  const commitEditing = useCallback(() => {
    if (editingIndex === null) {
      return;
    }
    const spec = editingDraft.trim();
    const error = validateArtifactGroupSpec(spec);
    if (error) {
      setValidationError(error);
      return;
    }
    onChange(groupSpecs.map((item, index) => index === editingIndex ? spec : item));
    setEditingIndex(null);
    setEditingDraft("");
    setValidationError(null);
  }, [editingDraft, editingIndex, groupSpecs, onChange]);

  const startEditing = useCallback((index: number) => {
    setEditingIndex(index);
    setEditingDraft(groupSpecs[index]);
    setValidationError(null);
  }, [groupSpecs]);

  const removeSpec = useCallback((index: number) => {
    onChange(groupSpecs.filter((_, itemIndex) => itemIndex !== index));
    setValidationError(null);
    if (editingIndex === index) {
      setEditingIndex(null);
      setEditingDraft("");
    }
  }, [editingIndex, groupSpecs, onChange]);

  const moveSpec = useCallback((index: number, direction: -1 | 1) => {
    const nextIndex = index + direction;
    if (nextIndex < 0 || nextIndex >= groupSpecs.length) {
      return;
    }
    const next = [...groupSpecs];
    [next[index], next[nextIndex]] = [next[nextIndex], next[index]];
    onChange(next);
    setValidationError(null);
  }, [groupSpecs, onChange]);

  const handleNewSpecKeyDown = useCallback((event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key === "Enter") {
      event.preventDefault();
      commitNewSpec();
    }
  }, [commitNewSpec]);

  const handleEditKeyDown = useCallback((event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key === "Enter") {
      event.preventDefault();
      commitEditing();
    } else if (event.key === "Escape") {
      setEditingIndex(null);
      setEditingDraft("");
      setValidationError(null);
    }
  }, [commitEditing]);

  return (
    <div className="artifact-grouping-wrap">
      <div className="artifact-grouping-editor" aria-label="Artifact grouping levels">
        {groupSpecs.map((spec, index) => (
          <div className="grouping-chip" key={`${spec}-${index}`}>
            <button
              className="grouping-chip-step"
              type="button"
              onClick={() => moveSpec(index, -1)}
              disabled={index === 0}
              title="Move grouping level left"
              aria-label="Move grouping level left"
            >
              <ArrowLeft size={12} aria-hidden />
            </button>
            {editingIndex === index ? (
              <input
                className="grouping-chip-input"
                value={editingDraft}
                autoFocus
                onBlur={commitEditing}
                onChange={(event) => setEditingDraft(event.target.value)}
                onKeyDown={handleEditKeyDown}
              />
            ) : (
              <button
                className="grouping-chip-label"
                type="button"
                onClick={() => startEditing(index)}
                title="Edit grouping level"
              >
                [{spec}]
              </button>
            )}
            <button
              className="grouping-chip-step"
              type="button"
              onClick={() => moveSpec(index, 1)}
              disabled={index === groupSpecs.length - 1}
              title="Move grouping level right"
              aria-label="Move grouping level right"
            >
              <ArrowRight size={12} aria-hidden />
            </button>
            <button
              className="grouping-chip-remove"
              type="button"
              onClick={() => removeSpec(index)}
              title="Remove grouping level"
              aria-label="Remove grouping level"
            >
              <X size={12} aria-hidden />
            </button>
          </div>
        ))}
        <div className="grouping-add">
          <input
            className="grouping-add-input"
            value={newSpec}
            placeholder="meta:worker"
            onChange={(event) => setNewSpec(event.target.value)}
            onKeyDown={handleNewSpecKeyDown}
          />
          <button
            className="grouping-add-button"
            type="button"
            onClick={commitNewSpec}
            title="Add grouping level"
            aria-label="Add grouping level"
          >
            <Plus size={13} aria-hidden />
          </button>
        </div>
      </div>
      {validationError && <div className="grouping-error">{validationError}</div>}
    </div>
  );
}

type ArtifactGroupNode = {
  key: string;
  label: string;
  count: number;
  children: ArtifactGroupNode[];
  artifacts: ArtifactPayload[];
};

type ArtifactGroupBuilder = {
  path: string[];
  label: string;
  count: number;
  children: Map<string, ArtifactGroupBuilder>;
  artifacts: ArtifactPayload[];
};

function missingGroupValue(spec: string): ArtifactGroupValue {
  return { value: `missing ${spec}`, label: `missing ${spec}` };
}

function makeBuilderNode(path: string[], label: string): ArtifactGroupBuilder {
  return { path, label, count: 0, children: new Map(), artifacts: [] };
}

function finalizeGroupNode(node: ArtifactGroupBuilder): ArtifactGroupNode {
  const children = [...node.children.values()]
    .map(finalizeGroupNode)
    .sort((a, b) => a.label.localeCompare(b.label) || a.key.localeCompare(b.key));
  return {
    key: JSON.stringify(node.path),
    label: node.label,
    count: node.count,
    children,
    artifacts: node.artifacts,
  };
}

function buildArtifactTree(artifacts: ArtifactPayload[], groupSpecs: string[]) {
  if (groupSpecs.length === 0) {
    return [{
      key: "ungrouped",
      label: "Ungrouped",
      count: artifacts.length,
      children: [],
      artifacts,
    }];
  }

  const root = makeBuilderNode([], "root");
  for (const artifact of artifacts) {
    let parent = root;
    for (let index = 0; index < groupSpecs.length; index++) {
      const groupValue = artifact.groupValues[index] ?? missingGroupValue(groupSpecs[index]);
      let child = parent.children.get(groupValue.value);
      if (!child) {
        child = makeBuilderNode([...parent.path, groupValue.value], groupValue.label);
        parent.children.set(groupValue.value, child);
      }
      child.count += 1;
      parent = child;
    }
    parent.artifacts.push(artifact);
  }
  return [...root.children.values()]
    .map(finalizeGroupNode)
    .sort((a, b) => a.label.localeCompare(b.label) || a.key.localeCompare(b.key));
}

function ArtifactTree({
  artifacts,
  groupSpecs,
  loading,
  selectedHash,
  onSelect,
}: {
  artifacts: ArtifactPayload[];
  groupSpecs: string[];
  loading: boolean;
  selectedHash: string | null;
  onSelect: (hash: string) => void;
}) {
  const [collapsedGroups, setCollapsedGroups] = useState<Set<string>>(() => new Set());
  const tree = useMemo(() => buildArtifactTree(artifacts, groupSpecs), [artifacts, groupSpecs]);

  const toggleGroup = useCallback((key: string) => {
    setCollapsedGroups((previous) => {
      const next = new Set(previous);
      if (next.has(key)) {
        next.delete(key);
      } else {
        next.add(key);
      }
      return next;
    });
  }, []);

  if (loading && artifacts.length === 0) {
    return <div className="tree-empty">Loading…</div>;
  }

  if (artifacts.length === 0) {
    return <div className="tree-empty">No artifacts</div>;
  }

  return (
    <div className="artifact-tree">
      {tree.map((node) => (
        <ArtifactGroupNodeView
          key={node.key}
          node={node}
          collapsedGroups={collapsedGroups}
          onToggle={toggleGroup}
          selectedHash={selectedHash}
          onSelect={onSelect}
        />
      ))}
    </div>
  );
}

function ArtifactGroupNodeView({ node, collapsedGroups, onToggle, selectedHash, onSelect }: {
  node: ArtifactGroupNode;
  collapsedGroups: Set<string>;
  onToggle: (key: string) => void;
  selectedHash: string | null;
  onSelect: (hash: string) => void;
}) {
  const expanded = !collapsedGroups.has(node.key);
  return (
    <div className="tree-group-node">
      <button
        className="tree-group-header"
        type="button"
        onClick={() => onToggle(node.key)}
        aria-expanded={expanded}
      >
        <ChevronRight size={13} className={expanded ? "tree-chevron expanded" : "tree-chevron"} aria-hidden />
        <span className="tree-group-label" title={node.label}>{node.label}</span>
        <span className="tree-count">{node.count}</span>
      </button>
      {expanded && (
        <ul className="tree-children" role="list">
          {node.children.map((child) => (
            <li key={child.key}>
              <ArtifactGroupNodeView
                node={child}
                collapsedGroups={collapsedGroups}
                onToggle={onToggle}
                selectedHash={selectedHash}
                onSelect={onSelect}
              />
            </li>
          ))}
          {node.artifacts.map((artifact) => (
            <li key={artifact.hash}>
              <ArtifactLeaf
                artifact={artifact}
                selected={artifact.hash === selectedHash}
                onSelect={onSelect}
              />
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function ArtifactLeaf({ artifact, selected, onSelect }: {
  artifact: ArtifactPayload;
  selected: boolean;
  onSelect: (hash: string) => void;
}) {
  return (
    <button
      type="button"
      className={`tree-leaf-button${selected ? " tree-leaf-selected" : ""}`}
      onClick={() => onSelect(artifact.hash)}
    >
      <span className={`tree-type-badge tree-type-${artifact.type}`}>{artifact.type[0].toUpperCase()}</span>
      <span className="tree-hash">{artifact.hash}</span>
    </button>
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

function ArtifactDetailView({ detail, loading, onSelect, onRunLldb, lldbRunning, onRunAnalyze, analyzeRunning, onAskLlm, llmRunning, expandedFiles, onLoadFile }: {
  detail: ArtifactDetail | null;
  loading: boolean;
  onSelect: (hash: string) => void;
  onRunLldb: (hash: string) => void;
  lldbRunning: boolean;
  onRunAnalyze: (hash: string) => void;
  analyzeRunning: boolean;
  onAskLlm: (hash: string, prompt: string, dest: string, filenames: string[]) => Promise<void>;
  llmRunning: boolean;
  expandedFiles: Record<string, string>;
  onLoadFile: (filename: string) => void;
}) {
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

  return (
    <div className="detail-stack">
      <ArtifactMetaCard
        detail={detail}
        onSelect={onSelect}
        onRunLldb={onRunLldb}
        lldbRunning={lldbRunning}
        onRunAnalyze={onRunAnalyze}
        analyzeRunning={analyzeRunning}
        onAskLlm={onAskLlm}
        llmRunning={llmRunning}
      />
      {detail.files.map((file) => (
        <ArtifactFileCard
          key={file.name}
          file={file}
          expandedContent={expandedFiles[file.name]}
          onLoadFull={() => onLoadFile(file.name)}
        />
      ))}
    </div>
  );
}

function ArtifactMetaCard({ detail, onSelect, onRunLldb, lldbRunning, onRunAnalyze, analyzeRunning, onAskLlm, llmRunning }: {
  detail: ArtifactDetail;
  onSelect: (hash: string) => void;
  onRunLldb: (hash: string) => void;
  lldbRunning: boolean;
  onRunAnalyze: (hash: string) => void;
  analyzeRunning: boolean;
  onAskLlm: (hash: string, prompt: string, dest: string, filenames: string[]) => Promise<void>;
  llmRunning: boolean;
}) {
  const [askOpen, setAskOpen] = useState(false);

  return (
    <div className="detail-card">
      <div className="detail-card-title">
        <CopiableText text={detail.hash} />
        <span className={`detail-type-badge detail-type-${detail.type}`}>({detail.type})</span>
        <div className="detail-header-actions">
          <button
            type="button"
            className="detail-run-lldb-button detail-ask-llm-button"
            onClick={() => setAskOpen(true)}
            disabled={llmRunning || analyzeRunning || lldbRunning}
          >
            {llmRunning ? <LoaderCircle className="spin" size={14} aria-hidden /> : <MessageCircle size={14} aria-hidden />}
            {llmRunning ? "Asking…" : "Ask LLM"}
          </button>
          <button
            type="button"
            className="detail-run-lldb-button"
            onClick={() => onRunAnalyze(detail.hash)}
            disabled={analyzeRunning || lldbRunning || llmRunning}
          >
            {analyzeRunning ? "Analyzing…" : "Analyze"}
          </button>
          <button
            type="button"
            className="detail-run-lldb-button"
            onClick={() => onRunLldb(detail.hash)}
            disabled={lldbRunning || analyzeRunning || llmRunning}
          >
            {lldbRunning ? "Running…" : "Run LLDB"}
          </button>
        </div>
      </div>
      <div className="detail-meta-rows">
        {Object.entries(detail.meta).map(([key, value]) => (
          <div className="detail-meta-row" key={key}>
            <span className="detail-meta-label">{key}</span>
            {(key === "linked_crash" || key === "linked_core") && typeof value === "string" ? (
              <button type="button" className="detail-meta-link" onClick={() => onSelect(value)}>
                {value}
              </button>
            ) : (
              <span className="detail-meta-value">{value == null ? "—" : String(value)}</span>
            )}
          </div>
        ))}
      </div>
      {askOpen && (
        <AskLlmDialog
          detail={detail}
          running={llmRunning}
          onCancel={() => setAskOpen(false)}
          onSubmit={onAskLlm}
        />
      )}
    </div>
  );
}

function firstAvailableLlmFilename(filenames: string[]): string {
  const existing = new Set(filenames);
  let suffix = 1;
  while (existing.has(`llm_chat_${suffix}`)) {
    suffix += 1;
  }
  return `llm_chat_${suffix}`;
}

function AskLlmDialog({ detail, running, onCancel, onSubmit }: {
  detail: ArtifactDetail;
  running: boolean;
  onCancel: () => void;
  onSubmit: (hash: string, prompt: string, dest: string, filenames: string[]) => Promise<void>;
}) {
  const [filenames, setFilenames] = useState(detail.llmFiles);
  const [prompt, setPrompt] = useState("");
  const [dest, setDest] = useState(() => firstAvailableLlmFilename(detail.llmFiles));
  const [submitError, setSubmitError] = useState<string | null>(null);

  useEffect(() => {
    const handleKeyDown = (event: globalThis.KeyboardEvent) => {
      if (event.key === "Escape" && !running) {
        onCancel();
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [onCancel, running]);

  const normalizedDest = dest.trim();
  const invalidDest = !normalizedDest
    || normalizedDest === "."
    || normalizedDest === ".."
    || normalizedDest.includes("/")
    || normalizedDest.includes("\\")
    || normalizedDest.endsWith(".marker");
  const destExists = detail.llmFiles.includes(normalizedDest);
  const canSubmit = prompt.trim().length > 0 && !invalidDest && !destExists && !running;

  const handleSubmit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!canSubmit) {
      return;
    }
    setSubmitError(null);
    try {
      await onSubmit(detail.hash, prompt.trim(), normalizedDest, filenames);
      onCancel();
    } catch (err) {
      setSubmitError(err instanceof Error ? err.message : String(err));
    }
  };

  return (
    <div
      className="llm-dialog-backdrop"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget && !running) onCancel();
      }}
    >
      <form className="llm-dialog" role="dialog" aria-modal="true" aria-labelledby="llm-dialog-title" onSubmit={handleSubmit}>
        <div className="llm-dialog-header">
          <div>
            <h2 id="llm-dialog-title">Ask the LLM</h2>
            <p>Choose the artifact files to include as context.</p>
          </div>
          <button type="button" className="llm-dialog-close" onClick={onCancel} disabled={running} aria-label="Close">
            <X size={17} aria-hidden />
          </button>
        </div>

        <div className="llm-dialog-body">
          <div className="llm-dialog-field">
            <span className="llm-dialog-label">Artifact files</span>
            <div className="llm-file-stack">
              {filenames.map((filename) => (
                <div className="llm-file-row" key={filename}>
                  <span>{filename}</span>
                  <button
                    type="button"
                    onClick={() => setFilenames((current) => current.filter((name) => name !== filename))}
                    disabled={running}
                    aria-label={`Remove ${filename}`}
                    title={`Remove ${filename}`}
                  >
                    <X size={13} aria-hidden />
                  </button>
                </div>
              ))}
              {filenames.length === 0 && <div className="llm-file-empty">No artifact files selected</div>}
            </div>
          </div>

          <label className="llm-dialog-field">
            <span className="llm-dialog-label">Question</span>
            <textarea
              value={prompt}
              onChange={(event) => setPrompt(event.target.value)}
              placeholder="What would you like to know about this artifact?"
              rows={6}
              disabled={running}
              autoFocus
            />
          </label>

          <label className="llm-dialog-field">
            <span className="llm-dialog-label">Response file</span>
            <input type="text" value={dest} onChange={(event) => setDest(event.target.value)} disabled={running} />
            {invalidDest && dest.length > 0 && <span className="llm-dialog-validation">Use a visible local filename, not a path.</span>}
            {destExists && <span className="llm-dialog-validation">That file already exists.</span>}
          </label>
        </div>

        <div className="llm-dialog-footer">
          <div className="llm-dialog-status" role="status">
            {running && <><LoaderCircle className="spin" size={15} aria-hidden /> Waiting for the LLM…</>}
            {!running && submitError && <span>{submitError}</span>}
          </div>
          <button type="button" className="llm-dialog-secondary" onClick={onCancel} disabled={running}>Cancel</button>
          <button type="submit" className="llm-dialog-primary" disabled={!canSubmit}>
            {running ? "Asking…" : "OK"}
          </button>
        </div>
      </form>
    </div>
  );
}

function ArtifactFileCard({ file, expandedContent, onLoadFull }: {
  file: ArtifactFile;
  expandedContent: string | undefined;
  onLoadFull: () => void;
}) {
  const [expanded, setExpanded] = useState(false);

  if (file.symlink !== null) {
    return (
      <div className="detail-card">
        <div className="detail-symlink-line">
          <span className="detail-meta-label">{file.name}</span>
          <span className="detail-symlink-arrow">→</span>
          <code className="detail-symlink-target">{file.symlink}</code>
        </div>
        {file.lldbCommand && (
          <div className="detail-cmd-box">
            <CopiableText text={file.lldbCommand} />
          </div>
        )}
      </div>
    );
  }

  if (file.isBinary) {
    return (
      <div className="detail-card">
        <div className="detail-field-label">{file.name}</div>
        <span className="detail-binary-note">(binary file)</span>
      </div>
    );
  }

  return (
    <div className={`detail-card detail-card-file${expanded ? " is-expanded" : ""}`}>
      <div className="detail-card-file-head">
        <div className="detail-field-label">{file.name}</div>
        <button
          type="button"
          className="detail-expand-btn"
          aria-expanded={expanded}
          onClick={() => setExpanded((v) => !v)}
        >
          {expanded ? "Collapse" : "Expand"}
        </button>
      </div>
      <pre className="detail-input-block"><code>{expandedContent ?? file.preview ?? ""}</code></pre>
      {!file.previewComplete && expandedContent === undefined && (
        <button type="button" className="detail-load-btn" onClick={onLoadFull}>
          Load full
        </button>
      )}
    </div>
  );
}

const TREND_METRICS: { key: TrendMetric; label: string }[] = [
  { key: "execs_done", label: "Execs Done" },
  { key: "execs_per_sec", label: "Execs/sec" },
  { key: "corpus_count", label: "Corpus" },
  { key: "edges_found", label: "Edges Found" },
];

const CHART_W = 560;
const CHART_H = 240;
const PAD_L = 64;
const PAD_R = 16;
const PAD_T = 16;
const PAD_B = 32;
const INNER_W = CHART_W - PAD_L - PAD_R;
const INNER_H = CHART_H - PAD_T - PAD_B;

function formatTrendTime(seconds: number): string {
  if (seconds === 0) return "0";
  if (seconds < 60) return `${seconds}s`;
  if (seconds < 3600) return `${Math.round(seconds / 60)}m`;
  const h = Math.floor(seconds / 3600);
  const m = Math.round((seconds % 3600) / 60);
  return m > 0 ? `${h}h${m}m` : `${h}h`;
}

function formatTrendValue(value: number): string {
  if (!Number.isFinite(value)) return "—";
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}M`;
  if (value >= 1_000) return `${(value / 1_000).toFixed(value >= 10_000 ? 0 : 1)}K`;
  if (value < 100) return value.toFixed(1);
  return String(Math.round(value));
}

function TrendChart({ data, metric, onMetricChange }: {
  data: TrendPoint[];
  metric: TrendMetric;
  onMetricChange: (m: TrendMetric) => void;
}) {
  const last = data.length > 0 ? data[data.length - 1] : null;
  const hasData = data.length >= 2;

  const values = hasData ? data.map((p) => p[metric]) : [];
  const tMax = hasData ? data[data.length - 1].time : 0;
  const vMin = hasData ? Math.min(...values) : 0;
  const vMax = hasData ? Math.max(...values) : 1;
  const vRange = vMax - vMin || 1;

  const toX = (t: number) => PAD_L + (tMax > 0 ? (t / tMax) * INNER_W : 0);
  const toY = (v: number) => PAD_T + INNER_H - ((v - vMin) / vRange) * INNER_H;

  const polylinePoints = hasData
    ? data.map((p) => `${toX(p.time).toFixed(1)},${toY(p[metric]).toFixed(1)}`).join(" ")
    : "";

  const gridFractions = [0, 1 / 3, 2 / 3, 1];
  const xLabelCount = 6;
  const xLabels = hasData
    ? Array.from({ length: xLabelCount }, (_, i) => Math.round((i / (xLabelCount - 1)) * tMax))
    : [];

  return (
    <div className="trend-chart">
      <div className="trend-tabs">
        {TREND_METRICS.map(({ key, label }) => (
          <button
            key={key}
            type="button"
            className={`trend-tab${metric === key ? " active" : ""}`}
            onClick={() => onMetricChange(key)}
          >
            {label}
            {last !== null && (
              <span className="trend-tab-value">{formatTrendValue(last[key])}</span>
            )}
          </button>
        ))}
      </div>
      {!hasData ? (
        <div className="trend-empty">No trend data</div>
      ) : (
        <svg
          className="trend-svg"
          viewBox={`0 0 ${CHART_W} ${CHART_H}`}
          aria-label={`${metric} over time`}
        >
          {gridFractions.map((f) => {
            const y = PAD_T + INNER_H * (1 - f);
            const v = vMin + vRange * f;
            return (
              <g key={f}>
                <line
                  x1={PAD_L} y1={y} x2={PAD_L + INNER_W} y2={y}
                  className="trend-grid-line"
                />
                <text x={PAD_L - 6} y={y + 4} className="trend-axis-label" textAnchor="end">
                  {formatTrendValue(v)}
                </text>
              </g>
            );
          })}
          {xLabels.map((t) => (
            <text
              key={t}
              x={toX(t)}
              y={PAD_T + INNER_H + 22}
              className="trend-axis-label"
              textAnchor="middle"
            >
              {formatTrendTime(t)}
            </text>
          ))}
          <rect
            x={PAD_L} y={PAD_T} width={INNER_W} height={INNER_H}
            className="trend-border"
          />
          <polyline points={polylinePoints} className="trend-line" />
        </svg>
      )}
    </div>
  );
}

export default App;
