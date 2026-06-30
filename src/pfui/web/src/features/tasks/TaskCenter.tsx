import {
  Alert, Button, Card, Drawer, H5, Intent, OverlayToaster, Position, Spinner, Tag,
} from "@blueprintjs/core";
import { useEffect, useRef, useState } from "react";
import type { TaskInfo } from "../../protocol/types";
import { formatElapsed } from "../../shared/format";
import { statusToaster } from "../../shared/toaster";

const toaster = OverlayToaster.create({ position: Position.BOTTOM_RIGHT });

export function TaskCenter({ tasks, onStop }: { tasks: TaskInfo[]; onStop: (taskId: string) => Promise<void> }) {
  const [open, setOpen] = useState(false);
  const [confirming, setConfirming] = useState<TaskInfo | null>(null);
  const previous = useRef<Map<string, TaskInfo>>(new Map());
  const runningKey = useRef<string | undefined>(undefined);
  const running = tasks.filter((task) => task.status === "running").length;

  useEffect(() => {
    void statusToaster.then((instance) => {
      if (running === 0) {
        if (runningKey.current) instance.dismiss(runningKey.current);
        runningKey.current = undefined;
        return;
      }
      runningKey.current = instance.show({
        intent: Intent.PRIMARY,
        icon: "time",
        message: `${running} running task${running === 1 ? "" : "s"}`,
        action: { text: "View", onClick: () => setOpen(true) },
        timeout: 0,
      }, runningKey.current);
    });
  }, [running]);

  useEffect(() => {
    for (const task of tasks) {
      const old = previous.current.get(task.id);
      if (old?.status === "running" && task.status !== "running") {
        void toaster.then((instance) => instance.show({
          intent: task.status === "done" ? Intent.SUCCESS : task.status === "error" ? Intent.DANGER : Intent.WARNING,
          icon: task.status === "done" ? "tick" : "warning-sign",
          message: task.error ? `${task.name}: ${task.error}` : `${task.name}: ${task.status}`,
        }));
      }
    }
    previous.current = new Map(tasks.map((task) => [task.id, task]));
  }, [tasks]);

  return <>
    <Button
      minimal
      icon="timeline-events"
      text={running ? `Tasks (${running})` : "Tasks"}
      onClick={() => setOpen(true)}
    />
    <Drawer isOpen={open} onClose={() => setOpen(false)} title="Tasks" icon="timeline-events" position="right" size="420px">
      <div className="drawer-content">
        {!tasks.length && <p>No active or recent tasks.</p>}
        {tasks.map((task) => <Card compact key={task.id}>
          <div className="section-heading">
            <H5>{task.name}</H5>
            {task.status === "running" ? <Spinner size={16} /> : <Tag intent={task.status === "done" ? "success" : task.status === "error" ? "danger" : "warning"}>{task.status}</Tag>}
          </div>
          <div className="task-meta">
            {task.project && <Tag minimal icon="folder-close">{task.project}</Tag>}
            <span>{formatElapsed(task.startedAt, task.finishedAt)}</span>
          </div>
          {task.error && <p>{task.error}</p>}
          {task.stoppable && <Button small intent="danger" icon="stop" text="Stop" onClick={() => setConfirming(task)} />}
        </Card>)}
      </div>
    </Drawer>
    <Alert
      isOpen={confirming !== null}
      intent="danger"
      icon="stop"
      confirmButtonText="Stop task"
      cancelButtonText="Keep running"
      onCancel={() => setConfirming(null)}
      onConfirm={() => {
        if (confirming) void onStop(confirming.id);
        setConfirming(null);
      }}
    >
      Stop <strong>{confirming?.name}</strong>?
    </Alert>
  </>;
}
