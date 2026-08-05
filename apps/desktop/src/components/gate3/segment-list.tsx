import { useEffect, useRef } from "react";
import { useVirtualizer } from "@tanstack/react-virtual";
import { Loader } from "lucide-react";
import type { components } from "@/lib/api-client";
import { SegmentRow } from "@/components/gate3/segment-row";

type Segment = components["schemas"]["Segment"];

interface SegmentListProps {
  segments: Segment[];
  isLoading: boolean;
  hasNextPage: boolean;
  isFetchingNextPage: boolean;
  fetchNextPage: () => void;
  retranslating: Set<string>;
  onSave: (id: string, targetText: string) => void;
  onToggleLock: (segment: Segment) => void;
  onApprove: (segment: Segment) => void;
  onRetranslate: (id: string) => void;
}

//  Segment counts can run into the thousands for a full book, and each row
//  carries its own editable textarea, so only the rows near the viewport are
//  mounted at once — the rest are fetched and measured lazily as the reader
//  scrolls, instead of loading and rendering every segment up front.
export function SegmentList({
  segments,
  isLoading,
  hasNextPage,
  isFetchingNextPage,
  fetchNextPage,
  retranslating,
  onSave,
  onToggleLock,
  onApprove,
  onRetranslate,
}: SegmentListProps) {
  const scrollRef = useRef<HTMLDivElement>(null);

  const rowVirtualizer = useVirtualizer({
    count: segments.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: () => 140,
    overscan: 6,
  });

  const virtualItems = rowVirtualizer.getVirtualItems();

  useEffect(() => {
    const lastItem = virtualItems[virtualItems.length - 1];
    if (!lastItem) return;
    if (lastItem.index >= segments.length - 1 && hasNextPage && !isFetchingNextPage) {
      fetchNextPage();
    }
  }, [virtualItems, segments.length, hasNextPage, isFetchingNextPage, fetchNextPage]);

  if (isLoading) {
    return (
      <div className="grid h-32 place-items-center">
        <Loader className="size-5 animate-spin text-muted-foreground" aria-hidden />
      </div>
    );
  }

  if (segments.length === 0) {
    return <p className="p-8 text-center text-sm text-muted-foreground">No segments match this filter.</p>;
  }

  return (
    <div ref={scrollRef} className="flex-1 overflow-y-auto">
      <div style={{ height: rowVirtualizer.getTotalSize(), position: "relative" }}>
        {virtualItems.map((virtualRow) => {
          const segment = segments[virtualRow.index];
          return (
            <div
              key={segment.id}
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
              <SegmentRow
                segment={segment}
                retranslating={retranslating.has(segment.id)}
                onSave={(target_text) => onSave(segment.id, target_text)}
                onToggleLock={() => onToggleLock(segment)}
                onApprove={() => onApprove(segment)}
                onRetranslate={() => onRetranslate(segment.id)}
              />
            </div>
          );
        })}
      </div>
      {isFetchingNextPage && (
        <div className="grid h-16 place-items-center">
          <Loader className="size-4 animate-spin text-muted-foreground" aria-hidden />
        </div>
      )}
    </div>
  );
}
