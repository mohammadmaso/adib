import type { components } from "@adib/schema";

export type DocNode = components["schemas"]["DocNode-Output"];
export type DocTree = components["schemas"]["DocTree-Output"];
export type NodeKind = components["schemas"]["NodeKind"];

export const NODE_KINDS: NodeKind[] = [
  "heading",
  "paragraph",
  "list",
  "list_item",
  "table",
  "figure",
  "code",
  "quote",
  "footnote",
  "toc",
  "front_matter",
  "back_matter",
  "page_break",
  "equation",
];

function children(node: DocNode): DocNode[] {
  return node.children ?? [];
}

/** Depth-first location of a node's containing sibling list and its index there. */
function locate(nodes: DocNode[], id: string): { list: DocNode[]; index: number } | null {
  const idx = nodes.findIndex((n) => n.id === id);
  if (idx !== -1) return { list: nodes, index: idx };
  for (const node of nodes) {
    const found = locate(children(node), id);
    if (found) return found;
  }
  return null;
}

export function canMoveUp(nodes: DocNode[], id: string): boolean {
  const loc = locate(nodes, id);
  return !!loc && loc.index > 0;
}

export function canMoveDown(nodes: DocNode[], id: string): boolean {
  const loc = locate(nodes, id);
  return !!loc && loc.index < loc.list.length - 1;
}

/** Indenting nests a node under its previous sibling, so it needs one to exist. */
export function canIndent(nodes: DocNode[], id: string): boolean {
  return canMoveUp(nodes, id);
}

/** Outdenting promotes a node to its parent's level; a root-level node has nowhere to go. */
export function canOutdent(nodes: DocNode[], id: string): boolean {
  return !nodes.some((n) => n.id === id);
}

export function canMergeNext(nodes: DocNode[], id: string): boolean {
  const loc = locate(nodes, id);
  return !!loc && loc.index < loc.list.length - 1;
}

export function deleteNode(nodes: DocNode[], id: string): DocNode[] {
  const out: DocNode[] = [];
  for (const node of nodes) {
    if (node.id === id) continue;
    out.push({ ...node, children: deleteNode(children(node), id) });
  }
  return out;
}

export function updateNode(nodes: DocNode[], id: string, patch: Partial<DocNode>): DocNode[] {
  return nodes.map((node) =>
    node.id === id
      ? { ...node, ...patch }
      : { ...node, children: updateNode(children(node), id, patch) },
  );
}

export function moveNode(nodes: DocNode[], id: string, dir: -1 | 1): DocNode[] {
  const idx = nodes.findIndex((n) => n.id === id);
  if (idx === -1) {
    return nodes.map((node) => ({ ...node, children: moveNode(children(node), id, dir) }));
  }
  const swapWith = idx + dir;
  if (swapWith < 0 || swapWith >= nodes.length) return nodes;
  const out = [...nodes];
  [out[idx], out[swapWith]] = [out[swapWith], out[idx]];
  return out;
}

export function mergeWithNext(nodes: DocNode[], id: string): DocNode[] {
  const idx = nodes.findIndex((n) => n.id === id);
  if (idx === -1) {
    return nodes.map((node) => ({ ...node, children: mergeWithNext(children(node), id) }));
  }
  if (idx === nodes.length - 1) return nodes;
  const a = nodes[idx];
  const b = nodes[idx + 1];
  const merged: DocNode = {
    ...a,
    text: [a.text, b.text].filter(Boolean).join(" ") || null,
    children: [...children(a), ...children(b)],
    assets: [...(a.assets ?? []), ...(b.assets ?? [])],
  };
  return [...nodes.slice(0, idx), merged, ...nodes.slice(idx + 2)];
}

export function indentNode(nodes: DocNode[], id: string): DocNode[] {
  const idx = nodes.findIndex((n) => n.id === id);
  if (idx === -1) {
    return nodes.map((node) => ({ ...node, children: indentNode(children(node), id) }));
  }
  if (idx === 0) return nodes;
  const target = nodes[idx];
  const prev = nodes[idx - 1];
  const updatedPrev = { ...prev, children: [...children(prev), target] };
  return [...nodes.slice(0, idx - 1), updatedPrev, ...nodes.slice(idx + 1)];
}

export function outdentNode(nodes: DocNode[], id: string): DocNode[] {
  for (let i = 0; i < nodes.length; i++) {
    const node = nodes[i];
    const kids = children(node);
    const childIdx = kids.findIndex((c) => c.id === id);
    if (childIdx !== -1) {
      const target = kids[childIdx];
      const newChildren = [...kids.slice(0, childIdx), ...kids.slice(childIdx + 1)];
      const updatedNode = { ...node, children: newChildren };
      return [...nodes.slice(0, i), updatedNode, target, ...nodes.slice(i + 1)];
    }
    const deeper = outdentNode(kids, id);
    if (deeper !== kids) {
      const out = [...nodes];
      out[i] = { ...node, children: deeper };
      return out;
    }
  }
  return nodes;
}

/** Splits a node's text at a character offset into two sibling nodes. */
export function splitNode(nodes: DocNode[], id: string, splitAt: number): DocNode[] {
  return nodes.flatMap((node) => {
    if (node.id === id) {
      const text = node.text ?? "";
      const first = text.slice(0, splitAt).trimEnd();
      const second = text.slice(splitAt).trimStart();
      if (!second) return [node];
      const newNode: DocNode = {
        ...node,
        id: `${node.id}-split-${Math.random().toString(36).slice(2, 8)}`,
        text: second,
        children: [],
        assets: [],
      };
      return [{ ...node, text: first }, newNode];
    }
    return [{ ...node, children: splitNode(children(node), id, splitAt) }];
  });
}

export function countNodes(nodes: DocNode[]): number {
  return nodes.reduce((sum, n) => sum + 1 + countNodes(children(n)), 0);
}

export interface FlatDocNode {
  node: DocNode;
  depth: number;
  hasChildren: boolean;
  can: {
    moveUp: boolean;
    moveDown: boolean;
    indent: boolean;
    outdent: boolean;
    mergeNext: boolean;
  };
}

/**
 * Flattens the tree into the currently-visible rows (depth-first, skipping the
 * children of collapsed nodes) so callers can window-render a large document
 * instead of mounting every node up front.
 */
export function flattenVisible(
  nodes: DocNode[],
  collapsed: Set<string>,
  depth = 0,
): FlatDocNode[] {
  const out: FlatDocNode[] = [];
  nodes.forEach((node, i) => {
    const kids = children(node);
    out.push({
      node,
      depth,
      hasChildren: kids.length > 0,
      can:
        depth === 0
          ? {
              moveUp: i > 0,
              moveDown: i < nodes.length - 1,
              indent: canIndent(nodes, node.id),
              outdent: false,
              mergeNext: canMergeNext(nodes, node.id),
            }
          : {
              moveUp: i > 0,
              moveDown: i < nodes.length - 1,
              indent: i > 0,
              outdent: true,
              mergeNext: i < nodes.length - 1,
            },
    });
    if (kids.length > 0 && !collapsed.has(node.id)) {
      out.push(...flattenVisible(kids, collapsed, depth + 1));
    }
  });
  return out;
}
