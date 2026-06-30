import type { ArtifactSummary } from "../../protocol/types";

export type ArtifactGroupNode = {
  id: string;
  label: string;
  count: number;
  groups: ArtifactGroupNode[];
  artifacts: ArtifactSummary[];
};

type Builder = {
  path: string[];
  label: string;
  count: number;
  groups: Map<string, Builder>;
  artifacts: ArtifactSummary[];
};

export function validateGroupSpec(raw: string): string | null {
  const value = raw.trim();
  if (!value) return "Grouping spec cannot be empty";
  if (value === "type") return null;
  const separator = value.indexOf(":");
  if (separator < 0) return "Use type, file:<name>, meta:<key>, or exists:<name>";
  const kind = value.slice(0, separator);
  const argument = value.slice(separator + 1);
  if (kind === "meta") return argument ? null : "meta: requires a key";
  if (kind === "file" || kind === "exists") {
    return argument && argument !== "." && argument !== ".." && !argument.includes("/") && !argument.includes("\\")
      ? null
      : `${kind}: requires a local filename`;
  }
  return "Use type, file:<name>, meta:<key>, or exists:<name>";
}

export function buildArtifactGroups(artifacts: ArtifactSummary[], specs: string[]): ArtifactGroupNode[] {
  if (!specs.length) {
    return [{ id: "group:ungrouped", label: "Ungrouped", count: artifacts.length, groups: [], artifacts }];
  }
  const root: Builder = { path: [], label: "root", count: 0, groups: new Map(), artifacts: [] };
  for (const artifact of artifacts) {
    let current = root;
    specs.forEach((spec, index) => {
      const group = artifact.groupValues[index] ?? { value: `missing ${spec}`, label: `missing ${spec}` };
      let child = current.groups.get(group.value);
      if (!child) {
        child = { path: [...current.path, group.value], label: group.label, count: 0, groups: new Map(), artifacts: [] };
        current.groups.set(group.value, child);
      }
      child.count += 1;
      current = child;
    });
    current.artifacts.push(artifact);
  }
  const finalize = (builder: Builder): ArtifactGroupNode => ({
    id: `group:${JSON.stringify(builder.path)}`,
    label: builder.label,
    count: builder.count,
    groups: [...builder.groups.values()].map(finalize).sort((a, b) => a.label.localeCompare(b.label)),
    artifacts: builder.artifacts,
  });
  return [...root.groups.values()].map(finalize).sort((a, b) => a.label.localeCompare(b.label));
}
