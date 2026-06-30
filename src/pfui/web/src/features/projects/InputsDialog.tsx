import {
  Alert, Button, Callout, Classes, Dialog, DialogBody, DialogFooter, H5, NonIdealState, Spinner, Tag, TextArea, Tree,
  type TreeNodeInfo,
} from "@blueprintjs/core";
import { useEffect, useMemo, useRef, useState } from "react";
import type { InputFilePayload, InputTreeNode } from "../../protocol/types";

type TreeResult = { tree: InputTreeNode[] };

function formatBytes(size: number | undefined): string {
  if (size === undefined) return "";
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KiB`;
  return `${(size / (1024 * 1024)).toFixed(1)} MiB`;
}

export function flattenInputFiles(nodes: InputTreeNode[]): string[] {
  const files: string[] = [];
  for (const node of nodes) {
    if (node.kind === "file") files.push(node.path);
    else files.push(...flattenInputFiles(node.children ?? []));
  }
  return files;
}

function updateTreeFileSize(nodes: InputTreeNode[], path: string, size: number): InputTreeNode[] {
  return nodes.map((node) => {
    if (node.path === path && node.kind === "file") return { ...node, size };
    if (node.kind === "directory") return { ...node, children: updateTreeFileSize(node.children ?? [], path, size) };
    return node;
  });
}

function toTreeNode(node: InputTreeNode, collapsed: Set<string>, selectedPath: string | null): TreeNodeInfo {
  if (node.kind === "directory") {
    return {
      id: `directory:${node.path}`,
      icon: "folder-close",
      label: node.name,
      nodeData: node,
      isExpanded: !collapsed.has(node.path),
      childNodes: (node.children ?? []).map((child) => toTreeNode(child, collapsed, selectedPath)),
    };
  }
  return {
    id: `file:${node.path}`,
    icon: "document",
    label: node.name,
    secondaryLabel: <Tag minimal>{formatBytes(node.size)}</Tag>,
    nodeData: node,
    isSelected: node.path === selectedPath,
  };
}

export function InputsDialog({ projectName, open, onClose, onList, onRead, onSave, onDelete }: {
  projectName: string | null;
  open: boolean;
  onClose: () => void;
  onList: () => Promise<TreeResult>;
  onRead: (path: string) => Promise<InputFilePayload>;
  onSave: (path: string, content: string) => Promise<InputFilePayload>;
  onDelete: (path: string) => Promise<TreeResult>;
}) {
  const fileGeneration = useRef(0);
  const [tree, setTree] = useState<InputTreeNode[]>([]);
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());
  const [selectedPath, setSelectedPath] = useState<string | null>(null);
  const [file, setFile] = useState<InputFilePayload | null>(null);
  const [content, setContent] = useState("");
  const [originalContent, setOriginalContent] = useState("");
  const [loadingTree, setLoadingTree] = useState(false);
  const [loadingFile, setLoadingFile] = useState(false);
  const [saving, setSaving] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const busy = loadingTree || loadingFile || saving || deleting;
  const dirty = file !== null && content !== originalContent;
  const contents = useMemo(
    () => tree.map((node) => toTreeNode(node, collapsed, selectedPath)),
    [collapsed, selectedPath, tree],
  );

  const clearSelection = () => {
    fileGeneration.current += 1;
    setSelectedPath(null);
    setFile(null);
    setContent("");
    setOriginalContent("");
    setLoadingFile(false);
  };

  const loadTree = async () => {
    setLoadingTree(true);
    setError(null);
    try {
      const result = await onList();
      setTree(result.tree);
      if (selectedPath && !flattenInputFiles(result.tree).includes(selectedPath)) clearSelection();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setLoadingTree(false);
    }
  };

  const loadFile = async (path: string) => {
    const generation = ++fileGeneration.current;
    setSelectedPath(path);
    setLoadingFile(true);
    setError(null);
    try {
      const result = await onRead(path);
      if (fileGeneration.current !== generation) return;
      setFile(result);
      setContent(result.content);
      setOriginalContent(result.content);
    } catch (reason) {
      if (fileGeneration.current === generation) setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      if (fileGeneration.current === generation) setLoadingFile(false);
    }
  };

  useEffect(() => {
    if (!open) return;
    setTree([]);
    setCollapsed(new Set());
    clearSelection();
    void loadTree();
  }, [open, projectName]);

  const setExpanded = (node: TreeNodeInfo, expanded: boolean) => {
    const input = node.nodeData as InputTreeNode | undefined;
    if (!input) return;
    setCollapsed((current) => {
      const next = new Set(current);
      if (expanded) next.delete(input.path); else next.add(input.path);
      return next;
    });
  };

  const save = async () => {
    if (!selectedPath) return;
    setSaving(true);
    setError(null);
    try {
      const result = await onSave(selectedPath, content);
      setFile(result);
      setContent(result.content);
      setOriginalContent(result.content);
      setTree((current) => updateTreeFileSize(current, result.path, result.size));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setSaving(false);
    }
  };

  const deleteSelected = async () => {
    if (!selectedPath) return;
    const previousFiles = flattenInputFiles(tree);
    const previousIndex = Math.max(0, previousFiles.indexOf(selectedPath));
    setDeleting(true);
    setError(null);
    try {
      const result = await onDelete(selectedPath);
      setTree(result.tree);
      const nextFiles = flattenInputFiles(result.tree);
      const nextPath = nextFiles[Math.min(previousIndex, nextFiles.length - 1)] ?? null;
      if (nextPath) await loadFile(nextPath);
      else clearSelection();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setDeleting(false);
      setConfirmDelete(false);
    }
  };

  return <>
    <Dialog
      className="inputs-dialog"
      isOpen={open}
      onClose={onClose}
      title={`${projectName ?? "Project"} inputs`}
      icon="folder-open"
      canEscapeKeyClose={!busy}
      canOutsideClickClose={!busy}
    >
      <DialogBody className="inputs-dialog-body">
        {error && <Callout compact intent="danger">{error}</Callout>}
        <div className="inputs-layout">
          <section className="inputs-tree-pane">
            <div className="section-heading">
              <H5>Inputs</H5>
              <Button minimal icon="refresh" title="Refresh inputs" aria-label="Refresh inputs" loading={loadingTree} disabled={busy} onClick={() => void loadTree()} />
            </div>
            <div className="inputs-tree-scroll">
              {loadingTree && !tree.length ? <Spinner size={24} /> : !tree.length ? (
                <NonIdealState icon="folder-open" title="No inputs" />
              ) : (
                <Tree
                  contents={contents}
                  onNodeClick={(node) => {
                    if (busy) return;
                    const input = node.nodeData as InputTreeNode | undefined;
                    if (!input) return;
                    if (input.kind === "file") void loadFile(input.path);
                    else setExpanded(node, collapsed.has(input.path));
                  }}
                  onNodeCollapse={(node) => setExpanded(node, false)}
                  onNodeExpand={(node) => setExpanded(node, true)}
                />
              )}
            </div>
          </section>
          <section className="inputs-editor-pane">
            {loadingFile ? <div className="centered"><Spinner size={24} /></div> : file ? <>
              <div className="section-heading inputs-file-heading">
                <H5 title={file.path}>{file.path}</H5>
                <Tag minimal>{formatBytes(file.size)}</Tag>
              </div>
              <TextArea
                aria-label="Input contents"
                className={`${Classes.MONOSPACE_TEXT} inputs-editor`}
                disabled={saving || deleting}
                fill
                spellCheck={false}
                value={content}
                onChange={(event) => setContent(event.target.value)}
              />
            </> : <NonIdealState icon="document-open" title="Choose an input file" />}
          </section>
        </div>
      </DialogBody>
      <DialogFooter actions={<>
        <Button text="Close" disabled={busy} onClick={onClose} />
        <Button icon="trash" intent="danger" text="Delete" disabled={!file || busy} onClick={() => setConfirmDelete(true)} />
        <Button icon="undo" text="Revert" disabled={!dirty || busy} onClick={() => setContent(originalContent)} />
        <Button icon="floppy-disk" intent="primary" text="Save" loading={saving} disabled={!dirty || busy} onClick={() => void save()} />
      </>} />
    </Dialog>
    <Alert
      isOpen={confirmDelete}
      intent="danger"
      icon="trash"
      confirmButtonText="Delete"
      cancelButtonText="Cancel"
      canEscapeKeyCancel={!deleting}
      canOutsideClickCancel={!deleting}
      loading={deleting}
      onCancel={() => setConfirmDelete(false)}
      onConfirm={() => void deleteSelected()}
    >
      Delete <strong>{selectedPath}</strong> from this project&apos;s inputs?
    </Alert>
  </>;
}
