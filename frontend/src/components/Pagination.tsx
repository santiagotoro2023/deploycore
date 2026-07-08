import { ChevronLeft, ChevronRight } from "lucide-react";

interface PaginationProps {
  offset: number;
  limit: number;
  count: number;
  onOffsetChange: (offset: number) => void;
}

export default function Pagination({ offset, limit, count, onOffsetChange }: PaginationProps) {
  const page = Math.floor(offset / limit) + 1;
  const hasNext = count === limit;
  const hasPrev = offset > 0;

  if (offset === 0 && !hasNext) return null;

  return (
    <div className="flex items-center justify-between text-xs text-neutral-500">
      <span>Page {page}</span>
      <div className="flex items-center gap-2">
        <button
          className="flex items-center gap-1 rounded-md border border-neutral-300 dark:border-neutral-700 px-2 py-1 hover:bg-neutral-50 dark:hover:bg-neutral-800 disabled:opacity-40"
          disabled={!hasPrev}
          onClick={() => onOffsetChange(Math.max(0, offset - limit))}
        >
          <ChevronLeft size={13} strokeWidth={1.75} />
          Prev
        </button>
        <button
          className="flex items-center gap-1 rounded-md border border-neutral-300 dark:border-neutral-700 px-2 py-1 hover:bg-neutral-50 dark:hover:bg-neutral-800 disabled:opacity-40"
          disabled={!hasNext}
          onClick={() => onOffsetChange(offset + limit)}
        >
          Next
          <ChevronRight size={13} strokeWidth={1.75} />
        </button>
      </div>
    </div>
  );
}
