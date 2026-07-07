export type ConnectionState = "connecting" | "connected" | "disconnected";

export type ProjectSnapshot = {
  name: string;
  repo: string;
  cloneRef: string;
  fuzzTarget: string;
  config: Record<string, unknown>;
  importantConfig: Record<string, string | number | boolean | null>;
  paths: { root: string; config: string };
};

export type SummaryPayload = {
  status: "ready" | "unavailable";
  updatedAt: string;
  values: Record<string, string | number | boolean | null>;
  error: string | null;
};

export type TrendPoint = {
  time: number;
  execs_done: number;
  execs_per_sec: number;
  corpus_count: number;
  edges_found: number;
};

export type TrendMetric = keyof Omit<TrendPoint, "time">;

export type ArtifactGroupValue = { value: string; label: string };

export type ArtifactSummary = {
  hash: string;
  type: "crash" | "core";
  path: string;
  hasInput: boolean;
  inputSize: number | null;
  groupValues: ArtifactGroupValue[];
};

export type ArtifactFile = {
  name: string;
  symlink: string | null;
  preview: string | null;
  previewComplete: boolean;
  isBinary: boolean;
  lldbCommand: string | null;
};

export type ArtifactDetail = {
  hash: string;
  type: "crash" | "core";
  meta: Record<string, unknown>;
  files: ArtifactFile[];
  llmFiles: string[];
};

export type InputTreeNode = {
  path: string;
  name: string;
  kind: "directory" | "file";
  size?: number;
  children?: InputTreeNode[];
};

export type InputFilePayload = {
  path: string;
  content: string;
  size: number;
};

export type TaskInfo = {
  id: string;
  name: string;
  kind: string;
  project: string | null;
  startedAt: string;
  finishedAt: string | null;
  status: "running" | "done" | "error" | "cancelled";
  error: string | null;
  stoppable: boolean;
  progress?: number | null;
  etaSeconds?: number | null;
  phase?: string | null;
};

export type SessionReady = {
  projects: string[];
  defaultProject: string | null;
  defaultWarning: string | null;
  tasks: TaskInfo[];
};

export type ProtocolEvent = { event: string; data: unknown };

export type ProtocolResponse = {
  id: string | null;
  project?: string;
  ok: boolean;
  result?: unknown;
  error?: { code: string; message: string };
};
