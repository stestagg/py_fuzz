import {
  Alert, Button, ButtonGroup, Checkbox, Dialog, DialogBody, DialogFooter, FormGroup,
  NumericInput, ProgressBar, Spinner, Tag,
} from "@blueprintjs/core";
import { useEffect, useRef, useState } from "react";
import type { TaskInfo } from "../../protocol/types";
import { formatElapsed, formatEta } from "../../shared/format";

type Action = "fuzz" | "build" | "clean";

// build/fuzz/clean are mutually exclusive per project: only one may run at a
// time for a given project (a different project can run its own concurrently).
const EXCLUSIVE_KINDS: readonly string[] = ["fuzz", "build", "clean"];

const RUNNING_LABEL: Record<string, string> = { fuzz: "Fuzzing", build: "Building", clean: "Cleaning" };

export function ActionMenu({ project, tasks, disabled, onStart, onStop }: {
  project: string | null;
  tasks: TaskInfo[];
  disabled: boolean;
  onStart: (action: Action, params: Record<string, unknown>) => Promise<void>;
  onStop: (taskId: string) => Promise<void>;
}) {
  const [dialog, setDialog] = useState<Action | null>(null);
  const [instances, setInstances] = useState(10);
  const [aflDebug, setAflDebug] = useState(false);
  const [monitor, setMonitor] = useState(true);
  const [buildTarget, setBuildTarget] = useState("all");
  const [cleanComponents, setCleanComponents] = useState<string[]>([]);
  const [cleanConfirm, setCleanConfirm] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [stopConfirm, setStopConfirm] = useState(false);
  const [, forceTick] = useState(0);
  const active = tasks.find((task) => task.project === project && task.status === "running" && EXCLUSIVE_KINDS.includes(task.kind));

  // Re-render once a second so the running task's elapsed time keeps ticking.
  useEffect(() => {
    if (!active) return;
    const timer = window.setInterval(() => forceTick((value) => value + 1), 1000);
    return () => window.clearInterval(timer);
  }, [active]);

  // The server pushes ETA only when a build milestone is reached; anchor it to
  // wall-clock time so the displayed estimate counts down between updates.
  const etaAnchor = useRef<{ eta: number; at: number } | null>(null);
  useEffect(() => {
    etaAnchor.current = active?.etaSeconds != null ? { eta: active.etaSeconds, at: Date.now() } : null;
  }, [active?.id, active?.etaSeconds]);
  const showProgress = active?.kind === "build" && active.progress != null;
  const displayEta = etaAnchor.current
    ? Math.max(0, etaAnchor.current.eta - (Date.now() - etaAnchor.current.at) / 1000)
    : null;

  const submit = async (action: Action, params: Record<string, unknown>) => {
    setSubmitting(true);
    try {
      await onStart(action, params);
      setDialog(null);
    } finally {
      setSubmitting(false);
    }
  };

  const toggleClean = (component: string) => {
    setCleanComponents((current) => current.includes(component) ? current.filter((item) => item !== component) : [...current, component]);
  };

  return <>
    {active ? (
      <div className="action-running">
        <Tag large minimal intent="primary" icon={<Spinner size={14} />}>
          {RUNNING_LABEL[active.kind] ?? active.kind} · {formatElapsed(active.startedAt, active.finishedAt)}
        </Tag>
        {showProgress && (
          <div className="action-progress">
            <ProgressBar value={active.progress ?? 0} intent="primary" stripes={false} animate={false} />
            <span className="action-progress-label">
              {Math.round((active.progress ?? 0) * 100)}%
              {displayEta != null ? ` · ~${formatEta(displayEta)} left` : ""}
              {active.phase ? ` · ${active.phase}` : ""}
            </span>
          </div>
        )}
        {active.stoppable && <Button minimal small intent="danger" icon="stop" title="Stop task" onClick={() => setStopConfirm(true)} />}
      </div>
    ) : (
      <ButtonGroup>
        <Button icon="build" text="Build" disabled={disabled || !project} onClick={() => setDialog("build")} />
        <Button intent="primary" icon="flame" text="Fuzz" disabled={disabled || !project} onClick={() => setDialog("fuzz")} />
        <Button icon="trash" text="Clean" disabled={disabled || !project} onClick={() => setDialog("clean")} />
      </ButtonGroup>
    )}
    <Alert
      isOpen={stopConfirm}
      intent="danger"
      icon="stop"
      confirmButtonText="Stop task"
      cancelButtonText="Keep running"
      canEscapeKeyCancel
      canOutsideClickCancel
      onCancel={() => setStopConfirm(false)}
      onConfirm={() => {
        setStopConfirm(false);
        if (active) void onStop(active.id);
      }}
    >
      Stop <strong>{active?.name}</strong>?
    </Alert>

    <Dialog isOpen={dialog === "fuzz"} onClose={() => setDialog(null)} title="Start fuzzing" icon="flame">
      <DialogBody>
        <FormGroup label="Instances" labelFor="fuzz-instances" helperText="Number of parallel AFL workers (1–128)">
          <NumericInput id="fuzz-instances" min={1} max={128} value={instances} onValueChange={(value) => setInstances(Number.isFinite(value) ? value : 10)} fill />
        </FormGroup>
        <Checkbox checked={aflDebug} label="AFL debug output" onChange={(event) => setAflDebug(event.currentTarget.checked)} />
        <Checkbox checked={monitor} label="Monitor and notifications" onChange={(event) => setMonitor(event.currentTarget.checked)} />
      </DialogBody>
      <DialogFooter
        actions={<><Button text="Cancel" onClick={() => setDialog(null)} /><Button intent="primary" loading={submitting} text="Start fuzzing" onClick={() => void submit("fuzz", { instances, aflDebug, monitor })} /></>}
      />
    </Dialog>

    <Dialog isOpen={dialog === "build"} onClose={() => setDialog(null)} title="Build project" icon="build">
      <DialogBody>
        <FormGroup label="Build target">
          {["all", "py", "helpers"].map((target) => <Checkbox key={target} checked={buildTarget === target} label={target} onChange={() => setBuildTarget(target)} />)}
        </FormGroup>
      </DialogBody>
      <DialogFooter actions={<><Button text="Cancel" onClick={() => setDialog(null)} /><Button intent="primary" loading={submitting} text="Build" onClick={() => void submit("build", { target: buildTarget })} /></>} />
    </Dialog>

    <Dialog isOpen={dialog === "clean"} onClose={() => setDialog(null)} title="Clean project" icon="trash">
      <DialogBody>
        <FormGroup label="Components to remove">
          {["all", "outputs", "build", "analysis"].map((component) => (
            <Checkbox
              key={component}
              checked={cleanComponents.includes(component)}
              disabled={component !== "all" && cleanComponents.includes("all")}
              label={component}
              onChange={() => toggleClean(component)}
            />
          ))}
        </FormGroup>
      </DialogBody>
      <DialogFooter actions={<><Button text="Cancel" onClick={() => setDialog(null)} /><Button intent="danger" disabled={!cleanComponents.length} text="Continue" onClick={() => setCleanConfirm(true)} /></>} />
    </Dialog>
    <Alert
      isOpen={cleanConfirm}
      intent="danger"
      icon="trash"
      confirmButtonText="Clean"
      cancelButtonText="Cancel"
      canEscapeKeyCancel
      canOutsideClickCancel
      loading={submitting}
      onCancel={() => setCleanConfirm(false)}
      onConfirm={() => {
        setCleanConfirm(false);
        void submit("clean", { components: cleanComponents.includes("all") ? ["all"] : cleanComponents });
      }}
    >
      This permanently removes the selected data for <strong>{project}</strong>.
    </Alert>
  </>;
}
