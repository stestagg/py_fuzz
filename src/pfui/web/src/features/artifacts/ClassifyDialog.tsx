import { Button, Checkbox, Dialog, DialogBody, DialogFooter, FormGroup, InputGroup, TextArea } from "@blueprintjs/core";
import { useEffect, useMemo, useState } from "react";

export type ClassifyClass = { name: string; description: string };

export type ClassifySubmit = {
  dest: string;
  free: boolean;
  classes: ClassifyClass[];
  extraText: string;
  applyGroup: boolean;
};

// Seeded into the additional prompt when free-class mode is enabled so the LLM
// produces tidy, tag-like labels. The user can edit or extend this freely.
const FREE_CLASSES_GUIDANCE =
  "Assign a short tag-style label: lowercase, hyphen-separated, with no whitespace or punctuation. Keep it brief — ideally a single word, at most three words joined by hyphens.";

function emptyClass(): ClassifyClass {
  return { name: "", description: "" };
}

const STORAGE_KEY = "pfui.classifyForm";

type StoredForm = { dest: string; free: boolean; classes: ClassifyClass[]; extraText: string; applyGroup: boolean };

function defaultForm(): StoredForm {
  return { dest: "classification", free: false, classes: [emptyClass(), emptyClass()], extraText: "", applyGroup: true };
}

function loadForm(): StoredForm {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return defaultForm();
    const parsed = JSON.parse(raw) as Partial<StoredForm>;
    return {
      dest: typeof parsed.dest === "string" ? parsed.dest : "classification",
      free: parsed.free === true,
      classes: Array.isArray(parsed.classes) && parsed.classes.length
        ? parsed.classes.map((item) => ({ name: String(item?.name ?? ""), description: String(item?.description ?? "") }))
        : [emptyClass(), emptyClass()],
      extraText: typeof parsed.extraText === "string" ? parsed.extraText : "",
      applyGroup: parsed.applyGroup !== false,
    };
  } catch {
    return defaultForm();
  }
}

export function ClassifyDialog({ open, loading, onClose, onSubmit }: {
  open: boolean;
  loading: boolean;
  onClose: () => void;
  onSubmit: (values: ClassifySubmit) => Promise<void>;
}) {
  const [dest, setDest] = useState("classification");
  const [free, setFree] = useState(false);
  const [classes, setClasses] = useState<ClassifyClass[]>([emptyClass(), emptyClass()]);
  const [extraText, setExtraText] = useState("");
  const [applyGroup, setApplyGroup] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Restore the last-used form when opening so a new batch of artifacts can be
  // classified with the same classes without re-entering everything.
  useEffect(() => {
    if (open) {
      const stored = loadForm();
      setDest(stored.dest);
      setFree(stored.free);
      setClasses(stored.classes);
      setExtraText(stored.extraText);
      setApplyGroup(stored.applyGroup);
      setError(null);
    }
  }, [open]);

  // Persist edits so they survive a cancel/close and the next open reflects them.
  useEffect(() => {
    if (!open) return;
    try {
      window.localStorage.setItem(STORAGE_KEY, JSON.stringify({ dest, free, classes, extraText, applyGroup }));
    } catch {
      // Ignore storage failures (private mode, quota); the form still works.
    }
  }, [open, dest, free, classes, extraText, applyGroup]);

  const destError = useMemo(() => {
    const value = dest.trim();
    if (!value || value === "." || value === ".." || value.includes("/") || value.includes("\\") || value.endsWith(".marker")) {
      return "Use a visible filename local to the artifact.";
    }
    return null;
  }, [dest]);

  const labels = useMemo(() => classes.map((item) => item.name.trim()).filter(Boolean), [classes]);
  const classesError = useMemo(() => {
    if (free) return null;
    if (labels.length < 1) return "Enter at least one class.";
    if (new Set(labels).size !== labels.length) return "Class names must be unique.";
    return null;
  }, [free, labels]);

  const updateClass = (index: number, patch: Partial<ClassifyClass>) =>
    setClasses((current) => current.map((item, position) => (position === index ? { ...item, ...patch } : item)));

  // Enabling free classes seeds the guidance into the prompt (without clobbering
  // anything the user has already written).
  const toggleFree = () => {
    setFree((current) => {
      const next = !current;
      if (next) {
        setExtraText((text) => {
          if (text.includes(FREE_CLASSES_GUIDANCE)) return text;
          return text.trim() ? `${FREE_CLASSES_GUIDANCE}\n\n${text}` : FREE_CLASSES_GUIDANCE;
        });
      }
      return next;
    });
  };

  const submit = async () => {
    setError(null);
    try {
      await onSubmit({
        dest: dest.trim(),
        free,
        classes: free ? [] : classes.filter((item) => item.name.trim()).map((item) => ({ name: item.name.trim(), description: item.description.trim() })),
        extraText: extraText.trim(),
        applyGroup,
      });
      onClose();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    }
  };

  return <Dialog isOpen={open} onClose={onClose} title="Classify artifacts" icon="predictive-analysis" canEscapeKeyClose={!loading} canOutsideClickClose={!loading}>
    <DialogBody>
      <FormGroup label="Output file" labelFor="classify-dest" helperText={destError ?? "Each artifact without this file is classified."} intent={destError ? "danger" : "none"}>
        <InputGroup id="classify-dest" value={dest} disabled={loading} intent={destError ? "danger" : "none"} onChange={(event) => setDest(event.target.value)} />
      </FormGroup>
      <Checkbox checked={free} disabled={loading} label="Free classes (let the model invent labels instead of using a fixed list)" onChange={toggleFree} />
      {!free && <FormGroup label="Classes" helperText={classesError ?? "Name each class, with an optional one-line description."} intent={classesError ? "danger" : "none"}>
        <div className="classify-classes">
          {classes.map((item, index) => <div className="classify-class-row" key={index}>
            <InputGroup placeholder="Class name" value={item.name} disabled={loading} onChange={(event) => updateClass(index, { name: event.target.value })} />
            <InputGroup fill placeholder="Description (optional)" value={item.description} disabled={loading} onChange={(event) => updateClass(index, { description: event.target.value })} />
            <Button minimal icon="cross" aria-label="Remove class" disabled={loading || classes.length <= 1} onClick={() => setClasses((current) => current.filter((_, position) => position !== index))} />
          </div>)}
        </div>
        <Button minimal small icon="add" text="Add class" disabled={loading} onClick={() => setClasses((current) => [...current, emptyClass()])} />
      </FormGroup>}
      <FormGroup label="Additional prompt" labelFor="classify-prompt" helperText={error} intent={error ? "danger" : "none"}>
        <TextArea id="classify-prompt" autoResize fill rows={3} value={extraText} disabled={loading} onChange={(event) => setExtraText(event.target.value)} />
      </FormGroup>
      <Checkbox checked={applyGroup} disabled={loading} label="Group artifacts by the result when done" onChange={() => setApplyGroup((current) => !current)} />
    </DialogBody>
    <DialogFooter actions={<>
      <Button text="Cancel" disabled={loading} onClick={onClose} />
      <Button intent="primary" text="OK" loading={loading} disabled={!!destError || !!classesError} onClick={() => void submit()} />
    </>} />
  </Dialog>;
}
