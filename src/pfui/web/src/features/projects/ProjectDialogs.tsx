import {
  Button, Callout, Classes, Dialog, DialogBody, DialogFooter, FormGroup, InputGroup, TextArea,
} from "@blueprintjs/core";
import { useEffect, useMemo, useState } from "react";
import type { ProjectSnapshot } from "../../protocol/types";

const PROJECT_NAME_PATTERN = /^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$/;

export function projectNameError(name: string, projects: string[]): string | null {
  if (!PROJECT_NAME_PATTERN.test(name)) return "Start with a letter or number; use only letters, numbers, dots, underscores, and hyphens.";
  if (projects.includes(name)) return "A project with this name already exists.";
  return null;
}

export function CreateProjectDialog({ open, initialName, projects, onClose, onCreate }: {
  open: boolean;
  initialName: string;
  projects: string[];
  onClose: () => void;
  onCreate: (name: string) => Promise<void>;
}) {
  const [name, setName] = useState(initialName);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const validationError = useMemo(() => projectNameError(name.trim(), projects), [name, projects]);

  useEffect(() => {
    if (open) {
      setName(initialName);
      setError(null);
    }
  }, [initialName, open]);

  const submit = async () => {
    if (validationError) return;
    setSaving(true);
    setError(null);
    try {
      await onCreate(name.trim());
      onClose();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setSaving(false);
    }
  };

  return <Dialog isOpen={open} onClose={onClose} title="Create project" icon="folder-new" canEscapeKeyClose={!saving} canOutsideClickClose={!saving}>
    <DialogBody>
      <FormGroup
        label="Project name"
        labelFor="new-project-name"
        helperText={validationError ?? "Creates a new project with the standard directory skeleton and default configuration."}
        intent={validationError ? "danger" : "none"}
      >
        <InputGroup
          id="new-project-name"
          autoFocus
          disabled={saving}
          intent={validationError ? "danger" : "none"}
          leftIcon="folder-close"
          value={name}
          onChange={(event) => setName(event.target.value)}
          onKeyDown={(event) => { if (event.key === "Enter") void submit(); }}
        />
      </FormGroup>
      {error && <Callout compact intent="danger">{error}</Callout>}
    </DialogBody>
    <DialogFooter actions={<>
      <Button text="Cancel" disabled={saving} onClick={onClose} />
      <Button intent="primary" icon="add" text="Create project" loading={saving} disabled={!!validationError} onClick={() => void submit()} />
    </>} />
  </Dialog>;
}

export function EditProjectDialog({ project, open, onClose, onSave }: {
  project: ProjectSnapshot | null;
  open: boolean;
  onClose: () => void;
  onSave: (config: string) => Promise<void>;
}) {
  const [config, setConfig] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (open && project) {
      setConfig(`${JSON.stringify(project.config, null, 2)}\n`);
      setError(null);
    }
  }, [open, project]);

  const submit = async () => {
    setSaving(true);
    setError(null);
    try {
      await onSave(config);
      onClose();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setSaving(false);
    }
  };

  return <Dialog className="project-config-dialog" isOpen={open} onClose={onClose} title={`Edit ${project?.name ?? "project"} config`} icon="edit" canEscapeKeyClose={!saving} canOutsideClickClose={!saving}>
    <DialogBody>
      <p className={Classes.TEXT_MUTED}>This is the effective project configuration. Defaults are removed when it is saved, matching <code>pfx edit</code>.</p>
      <TextArea
        aria-label="Project configuration JSON"
        className={`${Classes.MONOSPACE_TEXT} project-config-editor`}
        disabled={saving}
        fill
        rows={22}
        spellCheck={false}
        value={config}
        onChange={(event) => setConfig(event.target.value)}
      />
      {error && <Callout compact intent="danger">{error}</Callout>}
    </DialogBody>
    <DialogFooter actions={<>
      <Button text="Cancel" disabled={saving} onClick={onClose} />
      <Button intent="primary" icon="floppy-disk" text="Save config" loading={saving} disabled={!config.trim()} onClick={() => void submit()} />
    </>} />
  </Dialog>;
}
