import { Button, Card, H5, NonIdealState, Spinner, Tag, Tree, type TreeNodeInfo } from "@blueprintjs/core";
import { useMemo, useState } from "react";
import type { ArtifactSummary } from "../../protocol/types";
import { buildArtifactGroups, type ArtifactGroupNode } from "./grouping";
import { GroupingEditor } from "./GroupingEditor";

function toTreeNode(group: ArtifactGroupNode, collapsed: Set<string>, selected: string | null, analyzing: string | null): TreeNodeInfo {
  return {
    id: group.id,
    icon: "folder-close",
    label: group.label,
    secondaryLabel: <Tag minimal round>{group.count}</Tag>,
    isExpanded: !collapsed.has(group.id),
    childNodes: [
      ...group.groups.map((child) => toTreeNode(child, collapsed, selected, analyzing)),
      ...group.artifacts.map((artifact): TreeNodeInfo => ({
        id: `artifact:${artifact.hash}`,
        icon: artifact.hash === analyzing
          ? <Spinner size={16} />
          : artifact.type === "core" ? "cube" : "error",
        label: artifact.hash,
        isSelected: artifact.hash === selected,
        nodeData: artifact,
      })),
    ],
  };
}

export function ArtifactBrowser({ artifacts, specs, selected, loading, analyzing, analyzingHash, classifying, onSpecsChange, onSelect, onRefresh, onAnalyzeAll, onClassify }: {
  artifacts: ArtifactSummary[];
  specs: string[];
  selected: string | null;
  loading: boolean;
  analyzing: boolean;
  analyzingHash: string | null;
  classifying: boolean;
  onSpecsChange: (specs: string[]) => void;
  onSelect: (hash: string) => void;
  onRefresh: () => void;
  onAnalyzeAll: () => void;
  onClassify: () => void;
}) {
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());
  const contents = useMemo(
    () => buildArtifactGroups(artifacts, specs).map((group) => toTreeNode(group, collapsed, selected, analyzingHash)),
    [artifacts, analyzingHash, collapsed, selected, specs],
  );
  const setExpanded = (node: TreeNodeInfo, expanded: boolean) => {
    setCollapsed((current) => {
      const next = new Set(current);
      if (expanded) next.delete(String(node.id)); else next.add(String(node.id));
      return next;
    });
  };

  return <Card className="artifact-browser">
    <div className="section-heading">
      <H5>Artifacts</H5>
      <div>
        <Button minimal text="Analyze all" loading={analyzing} title="Analyze all unanalyzed artifacts" onClick={onAnalyzeAll} />
        <Button minimal text="Classify" loading={classifying} title="Classify artifacts with the LLM" onClick={onClassify} />
        <Button minimal icon="refresh" loading={loading} title="Sync and refresh" aria-label="Sync and refresh artifacts" onClick={onRefresh} />
      </div>
    </div>
    <GroupingEditor specs={specs} onChange={onSpecsChange} />
    <div className="artifact-tree">
      {loading && !artifacts.length ? <Spinner size={24} /> : !artifacts.length ? <NonIdealState icon="folder-open" title="No artifacts" /> : (
        <Tree
          contents={contents}
          onNodeClick={(node) => {
            const id = String(node.id);
            if (id.startsWith("artifact:")) onSelect(id.slice("artifact:".length));
            else setExpanded(node, collapsed.has(id));
          }}
          onNodeCollapse={(node) => setExpanded(node, false)}
          onNodeExpand={(node) => setExpanded(node, true)}
        />
      )}
    </div>
  </Card>;
}
