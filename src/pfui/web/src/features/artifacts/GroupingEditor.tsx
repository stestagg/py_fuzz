import { TagInput, type TagProps } from "@blueprintjs/core";
import { useEffect, useState } from "react";
import { validateGroupSpec } from "./grouping";

const sameSpecs = (a: string[], b: string[]) => a.length === b.length && a.every((value, index) => value === b[index]);

export function GroupingEditor({ specs, onChange }: { specs: string[]; onChange: (specs: string[]) => void }) {
  // Local list holds every entered tag, including invalid ones; only the valid
  // subset is applied (sent upstream for grouping). Invalid tags render red.
  const [entries, setEntries] = useState<string[]>(specs);

  useEffect(() => {
    const valid = entries.filter((value) => !validateGroupSpec(value));
    if (!sameSpecs(valid, specs)) setEntries(specs);
  }, [specs, entries]);

  const apply = (next: string[]) => {
    setEntries(next);
    const valid = next.filter((value) => !validateGroupSpec(value));
    if (!sameSpecs(valid, specs)) onChange(valid);
  };

  return <div className="group-editor">
    <TagInput
      fill
      values={entries}
      placeholder="Group by: type, meta:<key>, file:<name>, exists:<name>…"
      aria-label="Artifact grouping levels"
      tagProps={(value): TagProps => {
        const issue = validateGroupSpec(String(value));
        return issue ? { intent: "danger", htmlTitle: issue } : {};
      }}
      onAdd={(added) => apply([
        ...entries,
        ...added.map((value) => value.trim()).filter((value) => value && !entries.includes(value)),
      ])}
      onRemove={(_value, index) => apply(entries.filter((_, item) => item !== index))}
    />
  </div>;
}
