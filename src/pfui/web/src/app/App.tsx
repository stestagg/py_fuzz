import {
  Button, Callout, Card, NonIdealState, Spinner,
} from "@blueprintjs/core";
import { lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ActionMenu } from "../features/actions/ActionMenu";
import { ArtifactBrowser } from "../features/artifacts/ArtifactBrowser";
import { ArtifactDetailView } from "../features/artifacts/ArtifactDetailView";
import { ClassifyDialog } from "../features/artifacts/ClassifyDialog";
import { Summary } from "../features/dashboard/ProjectOverview";
import { InputsDialog } from "../features/projects/InputsDialog";
import { CreateProjectDialog, EditProjectDialog } from "../features/projects/ProjectDialogs";
import { ProjectSelector } from "../features/projects/ProjectSelector";
import { TaskCenter } from "../features/tasks/TaskCenter";
import { ProtocolClient } from "../protocol/client";
import { statusToaster } from "../shared/toaster";
import type {
  ArtifactDetail, ArtifactSummary, ConnectionState, InputFilePayload, InputTreeNode, ProjectSnapshot, SessionReady, SummaryPayload, TaskInfo,
  TrendMetric, TrendPoint,
} from "../protocol/types";
import { useTheme } from "./useTheme";

const DEFAULT_GROUPS = ["type"];
const TrendChart = lazy(() => import("../features/dashboard/TrendChart").then((module) => ({ default: module.TrendChart })));

function queryProject(): string | null {
  return new URLSearchParams(window.location.search).get("project");
}

const GROUP_SPECS_KEY = (project: string) => `pfui.groupSpecs.${project}`;

function loadGroupSpecs(project: string): string[] {
  try {
    const raw = window.localStorage.getItem(GROUP_SPECS_KEY(project));
    if (!raw) return DEFAULT_GROUPS;
    const parsed = JSON.parse(raw);
    if (Array.isArray(parsed) && parsed.every((value) => typeof value === "string")) return parsed;
  } catch {
    // Ignore malformed/inaccessible storage; fall back to the default grouping.
  }
  return DEFAULT_GROUPS;
}

function saveGroupSpecs(project: string, specs: string[]): void {
  try {
    window.localStorage.setItem(GROUP_SPECS_KEY(project), JSON.stringify(specs));
  } catch {
    // Ignore storage failures (private mode, quota); grouping still works in-session.
  }
}

export function App() {
  const client = useMemo(() => new ProtocolClient(), []);
  const generation = useRef(0);
  const detailGeneration = useRef(0);
  const activeSelection = useRef<{ project: string; hash: string } | null>(null);
  const previousTasks = useRef<Map<string, TaskInfo>>(new Map());
  const connectionKey = useRef<string | undefined>(undefined);
  const [connection, setConnection] = useState<ConnectionState>("connecting");
  const [ready, setReady] = useState(false);
  const [projects, setProjects] = useState<string[]>([]);
  const [projectName, setProjectName] = useState<string | null>(queryProject());
  const [project, setProject] = useState<ProjectSnapshot | null>(null);
  const [summary, setSummary] = useState<SummaryPayload | null>(null);
  const [trend, setTrend] = useState<TrendPoint[]>([]);
  const [metric, setMetric] = useState<TrendMetric>("execs_done");
  const [artifacts, setArtifacts] = useState<ArtifactSummary[]>([]);
  const [groupSpecs, setGroupSpecs] = useState(DEFAULT_GROUPS);
  const groupSpecsRef = useRef(groupSpecs);
  const [selectedHash, setSelectedHash] = useState<string | null>(null);
  const [detail, setDetail] = useState<ArtifactDetail | null>(null);
  const [fullFiles, setFullFiles] = useState<Record<string, string>>({});
  const [tasks, setTasks] = useState<TaskInfo[]>([]);
  const [loading, setLoading] = useState(false);
  const [detailLoading, setDetailLoading] = useState(false);
  const [artifactLoading, setArtifactLoading] = useState(false);
  const [action, setAction] = useState<"lldb" | "analyze" | "llm" | null>(null);
  const [analyzingAll, setAnalyzingAll] = useState(false);
  const [classifyingAll, setClassifyingAll] = useState(false);
  const [classifyOpen, setClassifyOpen] = useState(false);
  const pendingClassifyGroup = useRef<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [warning, setWarning] = useState<string | null>(null);
  const [refreshVersion, setRefreshVersion] = useState(0);
  const [createProjectName, setCreateProjectName] = useState<string | null>(null);
  const [editingProject, setEditingProject] = useState(false);
  const [inputsOpen, setInputsOpen] = useState(false);
  const { theme, toggleTheme } = useTheme();

  const report = useCallback((reason: unknown) => {
    const message = reason instanceof Error ? reason.message : String(reason);
    setError(message);
    throw reason;
  }, []);

  useEffect(() => {
    void statusToaster.then((instance) => {
      if (connection === "connected") {
        if (connectionKey.current) instance.dismiss(connectionKey.current);
        connectionKey.current = undefined;
        return;
      }
      connectionKey.current = instance.show({
        intent: connection === "connecting" ? "warning" : "danger",
        icon: "offline",
        message: connection === "connecting" ? "Connecting to server…" : "Disconnected — attempting to reconnect…",
        timeout: 0,
      }, connectionKey.current);
    });
  }, [connection]);

  useEffect(() => {
    const removeState = client.onState(setConnection);
    const removeReady = client.subscribe("session.ready", (raw) => {
      const data = raw as SessionReady;
      setProjects(data.projects);
      setTasks(data.tasks);
      setWarning(data.defaultWarning);
      const fromUrl = queryProject();
      const initial = fromUrl && data.projects.includes(fromUrl) ? fromUrl : data.defaultProject;
      if (fromUrl && !data.projects.includes(fromUrl)) setWarning(`Project '${fromUrl}' does not exist; choose another project.`);
      setProjectName(initial && data.projects.includes(initial) ? initial : null);
      setReady(true);
    });
    const removeTasks = client.subscribe("tasks.changed", (raw) => setTasks((raw as { tasks: TaskInfo[] }).tasks));
    client.connect();
    return () => { removeState(); removeReady(); removeTasks(); client.close(); };
  }, [client]);

  const applyProject = useCallback((name: string | null) => {
    generation.current += 1;
    setProjectName(name);
    setProject(null);
    setSummary(null);
    setTrend([]);
    setArtifacts([]);
    setSelectedHash(null);
    activeSelection.current = null;
    detailGeneration.current += 1;
    setDetail(null);
    setFullFiles({});
    setError(null);
    setWarning(null);
    setEditingProject(false);
    setInputsOpen(false);
  }, []);

  const selectProject = useCallback((name: string) => {
    if (name === projectName) return;
    const url = new URL(window.location.href);
    url.searchParams.set("project", name);
    window.history.pushState({}, "", url);
    applyProject(name);
  }, [applyProject, projectName]);

  useEffect(() => {
    const handlePopState = () => {
      const name = queryProject();
      if (name !== projectName && (name === null || projects.includes(name))) applyProject(name);
    };
    window.addEventListener("popstate", handlePopState);
    return () => window.removeEventListener("popstate", handlePopState);
  }, [applyProject, projectName, projects]);

  const loadArtifacts = useCallback(async (name: string, sync = false) => {
    setArtifactLoading(true);
    try {
      if (sync) await client.request("artifacts.sync", name);
      let result = await client.request<{ artifacts: ArtifactSummary[] }>("artifacts.list", name, { groupSpecs });
      if (!sync && !result.artifacts.length) {
        await client.request("artifacts.sync", name);
        result = await client.request("artifacts.list", name, { groupSpecs });
      }
      if (name === projectName) setArtifacts(result.artifacts);
    } finally {
      setArtifactLoading(false);
    }
  }, [client, groupSpecs, projectName]);

  // Apply a grouping change and persist it for the current project. Persisting
  // only on explicit changes (not on project-load) keeps switching projects from
  // clobbering the newly selected project's saved grouping.
  const changeGroupSpecs = useCallback((next: string[]) => {
    groupSpecsRef.current = next;
    setGroupSpecs(next);
    if (projectName) saveGroupSpecs(projectName, next);
  }, [projectName]);

  // Restore the saved grouping whenever the active project changes.
  useEffect(() => {
    if (!projectName) return;
    const stored = loadGroupSpecs(projectName);
    groupSpecsRef.current = stored;
    setGroupSpecs(stored);
  }, [projectName]);

  const analyzeAllActive = useMemo(
    () => analyzingAll || tasks.some(
      (task) => task.kind === "analyze-all" && task.project === projectName && task.status === "running",
    ),
    [analyzingAll, tasks, projectName],
  );

  const classifyAllActive = useMemo(
    () => classifyingAll || tasks.some(
      (task) => task.kind === "classify-all" && task.project === projectName && task.status === "running",
    ),
    [classifyingAll, tasks, projectName],
  );

  useEffect(() => {
    let cleanFinished = false;
    let analyzeAllFinished = false;
    let classifyAllFinished = false;
    for (const task of tasks) {
      if (task.project !== projectName) continue;
      const previous = previousTasks.current.get(task.id);
      if (previous?.status !== "running" || task.status === "running") continue;
      if (task.kind === "clean") cleanFinished = true;
      if (task.kind === "analyze-all") analyzeAllFinished = true;
      if (task.kind === "classify-all") classifyAllFinished = true;
    }
    previousTasks.current = new Map(tasks.map((task) => [task.id, task]));

    if (analyzeAllFinished) {
      setAnalyzingAll(false);
      if (projectName) void loadArtifacts(projectName).catch((reason) => setError(String(reason)));
    }
    if (classifyAllFinished) {
      setClassifyingAll(false);
      const spec = pendingClassifyGroup.current;
      pendingClassifyGroup.current = null;
      if (spec && !groupSpecsRef.current.includes(spec)) changeGroupSpecs([...groupSpecsRef.current, spec]);
      if (projectName) void loadArtifacts(projectName).catch((reason) => setError(String(reason)));
    }
    if (!cleanFinished) return;

    setArtifacts([]);
    setSelectedHash(null);
    activeSelection.current = null;
    detailGeneration.current += 1;
    setDetail(null);
    setFullFiles({});
    setRefreshVersion((current) => current + 1);
  }, [projectName, tasks, loadArtifacts, changeGroupSpecs]);

  useEffect(() => {
    if (!ready || !projectName || connection !== "connected") return;
    const currentGeneration = ++generation.current;
    setLoading(true);
    setError(null);
    void Promise.all([
      client.request<{ project: ProjectSnapshot }>("project.get", projectName),
      client.request<{ summary: SummaryPayload }>("summary.get", projectName),
      client.request<{ points: TrendPoint[] }>("trend.get", projectName),
      client.request<{ artifacts: ArtifactSummary[] }>("artifacts.list", projectName, { groupSpecs }),
    ]).then(([projectResult, summaryResult, trendResult, artifactResult]) => {
      if (generation.current !== currentGeneration) return;
      setProject(projectResult.project);
      setSummary(summaryResult.summary);
      setTrend(trendResult.points);
      setArtifacts(artifactResult.artifacts);
      if (!artifactResult.artifacts.length) void loadArtifacts(projectName).catch(() => undefined);
    }).catch((reason) => setError(reason instanceof Error ? reason.message : String(reason))).finally(() => {
      if (generation.current === currentGeneration) setLoading(false);
    });
  }, [client, connection, groupSpecs, loadArtifacts, projectName, ready, refreshVersion]);

  useEffect(() => {
    if (!projectName || connection !== "connected") return;
    const timer = window.setInterval(() => {
      const name = projectName;
      void Promise.all([
        client.request<{ summary: SummaryPayload }>("summary.get", name),
        client.request<{ points: TrendPoint[] }>("trend.get", name),
      ]).then(([summaryResult, trendResult]) => {
        if (projectName === name) {
          setSummary(summaryResult.summary);
          setTrend(trendResult.points);
        }
      }).catch(() => undefined);
    }, 60_000);
    return () => window.clearInterval(timer);
  }, [client, connection, projectName]);

  const selectArtifact = useCallback(async (hash: string) => {
    if (!projectName) return;
    const name = projectName;
    const requestGeneration = ++detailGeneration.current;
    activeSelection.current = { project: name, hash };
    setSelectedHash(hash);
    setDetailLoading(true);
    setFullFiles({});
    try {
      const result = await client.request<ArtifactDetail>("artifact.get", name, { hash });
      if (detailGeneration.current === requestGeneration) setDetail(result);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      if (detailGeneration.current === requestGeneration) setDetailLoading(false);
    }
  }, [client, projectName]);

  const runArtifactAction = async (kind: "lldb" | "analyze", method: string) => {
    if (!projectName || !selectedHash) return;
    const selection = { project: projectName, hash: selectedHash };
    setAction(kind);
    setError(null);
    try {
      const result = await client.request<ArtifactDetail>(method, selection.project, { hash: selection.hash });
      if (activeSelection.current?.project === selection.project && activeSelection.current.hash === selection.hash) setDetail(result);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setAction(null);
    }
  };

  const stopTask = useCallback(async (taskId: string) => {
    try { await client.request("task.stop", undefined, { taskId }); }
    catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)); }
  }, [client]);

  const themeToggle = <Button minimal icon={theme === "dark" ? "flash" : "moon"} aria-label="Toggle theme" title="Toggle theme" onClick={toggleTheme} />;
  const taskCenter = <TaskCenter tasks={tasks} onStop={stopTask} />;

  return <div className="app-shell">
    {project && <Summary
      project={project}
      projects={projects}
      summary={summary}
      loading={loading}
      actions={<ActionMenu
        project={projectName}
        tasks={tasks}
        disabled={connection !== "connected"}
        onStop={stopTask}
        onStart={async (kind, params) => {
          if (!projectName) return;
          try { await client.request("task.start", projectName, { action: kind, params }); }
          catch (reason) { report(reason); }
        }}
      />}
      tools={<>{taskCenter}{themeToggle}</>}
      onSelectProject={selectProject}
      onCreateProject={(initialName = "") => setCreateProjectName(initialName)}
      onOpenInputs={() => setInputsOpen(true)}
      onEditConfig={() => setEditingProject(true)}
    />}
    <main className="app-content">
      {warning && <Callout intent="warning"><div className="section-heading"><span>{warning}</span><Button minimal icon="cross" aria-label="Dismiss warning" onClick={() => setWarning(null)} /></div></Callout>}
      {error && <Callout intent="danger"><div className="section-heading"><span>{error}</span><Button minimal icon="cross" aria-label="Dismiss error" onClick={() => setError(null)} /></div></Callout>}
      {!ready ? <div className="centered"><Spinner /></div> : !projectName ? (
        <Card className="selection-card"><NonIdealState
          icon="folder-open"
          title="Choose a project"
          description="Select an existing pyfuzz project, or create a new one."
          action={<ProjectSelector
            projects={projects}
            selected={projectName}
            onSelect={selectProject}
            onCreate={(initialName = "") => setCreateProjectName(initialName)}
          />}
        /></Card>
      ) : (
        <section className="workspace-grid">
          <ArtifactBrowser
            artifacts={artifacts}
            specs={groupSpecs}
            selected={selectedHash}
            loading={artifactLoading}
            analyzing={analyzeAllActive}
            classifying={classifyAllActive}
            onSpecsChange={changeGroupSpecs}
            onSelect={(hash) => void selectArtifact(hash)}
            onRefresh={() => { if (projectName) void loadArtifacts(projectName, true).catch((reason) => setError(String(reason))); }}
            onAnalyzeAll={() => {
              if (!projectName) return;
              setAnalyzingAll(true);
              void client.request("artifacts.analyze", projectName).catch((reason) => { setAnalyzingAll(false); setError(String(reason)); });
            }}
            onClassify={() => setClassifyOpen(true)}
          />
          <div className="detail-panel">
            {selectedHash ? <ArtifactDetailView
              detail={detail}
              loading={detailLoading}
              action={action}
              fullFiles={fullFiles}
              onLinkedArtifact={(hash) => void selectArtifact(hash)}
              onRunLldb={() => runArtifactAction("lldb", "artifact.runLldb")}
              onAnalyze={() => runArtifactAction("analyze", "artifact.analyze")}
              onAskLlm={async (prompt, destination, filenames) => {
                if (!projectName || !selectedHash) return;
                const selection = { project: projectName, hash: selectedHash };
                setAction("llm");
                try {
                  const result = await client.request<ArtifactDetail>("artifact.askLlm", selection.project, { hash: selection.hash, prompt, dest: destination, filenames }, 300_000);
                  if (activeSelection.current?.project === selection.project && activeSelection.current.hash === selection.hash) setDetail(result);
                } finally { setAction(null); }
              }}
              onLoadFile={async (filename) => {
                if (!projectName || !selectedHash) return;
                const selection = { project: projectName, hash: selectedHash };
                const result = await client.request<{ content: string }>("artifact.file", selection.project, { hash: selection.hash, filename });
                if (activeSelection.current?.project === selection.project && activeSelection.current.hash === selection.hash) {
                  setFullFiles((current) => ({ ...current, [filename]: result.content }));
                }
              }}
            /> : <div className="detail-scroll"><Suspense fallback={<div className="centered"><Spinner /></div>}>
              <TrendChart points={trend} metric={metric} onMetricChange={setMetric} />
            </Suspense></div>}
          </div>
        </section>
      )}
    </main>
    <ClassifyDialog
      open={classifyOpen}
      loading={classifyingAll}
      onClose={() => setClassifyOpen(false)}
      onSubmit={async ({ dest, free, classes, extraText, applyGroup }) => {
        if (!projectName) return;
        setClassifyingAll(true);
        try {
          await client.request("artifacts.classify", projectName, { dest, free, classes, extraText });
          pendingClassifyGroup.current = applyGroup ? `file:${dest}` : null;
        } catch (reason) {
          setClassifyingAll(false);
          throw reason instanceof Error ? reason : new Error(String(reason));
        }
      }}
    />
    <CreateProjectDialog
      open={createProjectName !== null}
      initialName={createProjectName ?? ""}
      projects={projects}
      onClose={() => setCreateProjectName(null)}
      onCreate={async (name) => {
        const result = await client.request<{ project: ProjectSnapshot; projects: string[] }>("project.create", undefined, { name });
        setProjects(result.projects);
        selectProject(result.project.name);
      }}
    />
    <EditProjectDialog
      project={project}
      open={editingProject}
      onClose={() => setEditingProject(false)}
      onSave={async (config) => {
        if (!projectName) return;
        const result = await client.request<{ project: ProjectSnapshot }>("project.updateConfig", projectName, { config });
        setProject(result.project);
        setRefreshVersion((current) => current + 1);
      }}
    />
    <InputsDialog
      projectName={projectName}
      open={inputsOpen}
      onClose={() => setInputsOpen(false)}
      onList={async () => {
        if (!projectName) return { tree: [] };
        return client.request<{ tree: InputTreeNode[] }>("inputs.list", projectName);
      }}
      onRead={async (path) => {
        if (!projectName) throw new Error("No project selected");
        return client.request<InputFilePayload>("input.read", projectName, { path });
      }}
      onSave={async (path, content) => {
        if (!projectName) throw new Error("No project selected");
        return client.request<InputFilePayload>("input.update", projectName, { path, content });
      }}
      onDelete={async (path) => {
        if (!projectName) return { tree: [] };
        return client.request<{ tree: InputTreeNode[] }>("input.delete", projectName, { path });
      }}
    />
  </div>;
}
