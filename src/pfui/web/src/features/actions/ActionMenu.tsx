import {
  Alert, Button, Checkbox, Dialog, DialogBody, DialogFooter, FormGroup, Menu, MenuItem,
  NumericInput, Popover,
} from "@blueprintjs/core";
import { useState } from "react";
import type { TaskInfo } from "../../protocol/types";

type Action = "fuzz" | "build" | "clean";

export function ActionMenu({ project, tasks, disabled, onStart }: {
  project: string | null;
  tasks: TaskInfo[];
  disabled: boolean;
  onStart: (action: Action, params: Record<string, unknown>) => Promise<void>;
}) {
  const [dialog, setDialog] = useState<Action | null>(null);
  const [instances, setInstances] = useState(10);
  const [aflDebug, setAflDebug] = useState(false);
  const [monitor, setMonitor] = useState(true);
  const [buildTarget, setBuildTarget] = useState("all");
  const [cleanComponents, setCleanComponents] = useState<string[]>([]);
  const [cleanConfirm, setCleanConfirm] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const fuzzing = tasks.some((task) => task.project === project && task.kind === "fuzz" && task.status === "running");

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
    <Popover
      minimal
      content={<Menu>
        <MenuItem icon="flame" text="Fuzz" disabled={fuzzing} label={fuzzing ? "running" : undefined} onClick={() => setDialog("fuzz")} />
        <MenuItem icon="build" text="Build" onClick={() => setDialog("build")} />
        <MenuItem icon="trash" intent="danger" text="Clean" onClick={() => setDialog("clean")} />
      </Menu>}
    >
      <Button intent="primary" icon="play" rightIcon="caret-down" text="Run" disabled={disabled || !project} />
    </Popover>

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
