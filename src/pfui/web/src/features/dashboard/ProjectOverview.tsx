import { Button, Callout, Classes, Section, SectionCard } from "@blueprintjs/core";
import { type ReactNode, useMemo } from "react";
import type { ProjectSnapshot, SummaryPayload } from "../../protocol/types";
import { formatValue, labelFromKey } from "../../shared/format";
import { ProjectSelector } from "../projects/ProjectSelector";

const SUMMARY_ORDER = [
  "workers", "execs_done", "execs_per_sec", "crashes", "saved_crashes", "saved_hangs",
  "core_dumps", "run_time", "corpus_count", "edges_found",
];

export function Summary({ project, projects, summary, loading, actions, tools, onSelectProject, onCreateProject, onOpenInputs, onEditConfig }: {
  project: ProjectSnapshot;
  projects: string[];
  summary: SummaryPayload | null;
  loading: boolean;
  actions: ReactNode;
  tools: ReactNode;
  onSelectProject: (name: string) => void;
  onCreateProject: (initialName?: string) => void;
  onOpenInputs: () => void;
  onEditConfig: () => void;
}) {
  const items = useMemo(() => {
    if (!summary) return [];
    const keys = Object.keys(summary.values).filter((key) => key !== "project");
    return [...SUMMARY_ORDER.filter((key) => keys.includes(key)), ...keys.filter((key) => !SUMMARY_ORDER.includes(key)).sort()];
  }, [summary]);

  return (
    <Section
      compact
      elevation={0}
      title={
        <div className="project-title">
          <ProjectSelector projects={projects} selected={project.name} onSelect={onSelectProject} onCreate={onCreateProject} />
          {actions}
        </div>
      }
      rightElement={
        <div className="project-controls">
          {tools}
          <Button icon="folder-open" text="Inputs" onClick={onOpenInputs} />
          <Button icon="edit" text="Config" onClick={onEditConfig} />
        </div>
      }
      className="summary-section"
    >
      {summary?.error && <Callout intent="warning" compact>{summary.error}</Callout>}
      <SectionCard padded={false}>
        <div
          className="metric-grid"
          aria-busy={loading}
          style={items.length ? { gridTemplateColumns: `repeat(${items.length}, minmax(82px, 1fr))` } : undefined}
        >
          {items.map((key) => (
            <div className="metric-cell" key={key} title={labelFromKey(key)}>
              <strong className="metric-value">{formatValue(summary?.values[key])}</strong>
              <span className={`metric-label ${Classes.TEXT_MUTED}`}>{labelFromKey(key)}</span>
            </div>
          ))}
          {!summary && <span className={Classes.TEXT_MUTED}>{loading ? "Loading summary…" : "No summary available"}</span>}
        </div>
      </SectionCard>
    </Section>
  );
}
