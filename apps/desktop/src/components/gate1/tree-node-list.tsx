import { useRef } from "react";
import { useVirtualizer } from "@tanstack/react-virtual";
import type { FlatDocNode } from "@/lib/doc-tree";
import { TreeNodeRow, type NodeAction } from "@/components/gate1/tree-node-row";

interface TreeNodeListProps {
  projectId: string | undefined;
  items: FlatDocNode[];
  collapsed: Set<string>;
  onToggleCollapse: (id: string) => void;
  onAction: (id: string, action: NodeAction) => void;
}

//  Large books can produce thousands of structure nodes; only the rows near
//  the viewport are mounted, and collapsing a section removes its subtree
//  from the flattened list entirely rather than just hiding it in the DOM.
export function TreeNodeList({ projectId, items, collapsed, onToggleCollapse, onAction }: TreeNodeListProps) {
  const scrollRef = useRef<HTMLDivElement>(null);

  const rowVirtualizer = useVirtualizer({
    count: items.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: () => 56,
    overscan: 10,
  });

  return (
    <div ref={scrollRef} className="h-full overflow-y-auto px-6 py-4">
      <div style={{ height: rowVirtualizer.getTotalSize(), position: "relative" }}>
        {rowVirtualizer.getVirtualItems().map((virtualRow) => {
          const item = items[virtualRow.index];
          return (
            <div
              key={item.node.id}
              data-index={virtualRow.index}
              ref={rowVirtualizer.measureElement}
              style={{
                position: "absolute",
                top: 0,
                left: 0,
                width: "100%",
                transform: `translateY(${virtualRow.start}px)`,
              }}
            >
              <TreeNodeRow
                projectId={projectId}
                node={item.node}
                depth={item.depth}
                hasChildren={item.hasChildren}
                collapsed={collapsed.has(item.node.id)}
                onToggleCollapse={() => onToggleCollapse(item.node.id)}
                can={item.can}
                onAction={onAction}
              />
            </div>
          );
        })}
      </div>
    </div>
  );
}
