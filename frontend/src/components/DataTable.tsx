import { ChevronDown, ChevronLeft, ChevronRight, ChevronsUpDown, Search } from "lucide-react";
import { ReactNode, useMemo, useState } from "react";

export interface Column<T> {
  key: string;
  header: string;
  render: (row: T) => ReactNode;
  sortValue?: (row: T) => string | number;
}

interface DataTableProps<T> {
  rows: T[];
  columns: Column<T>[];
  rowKey: (row: T) => string;
  searchPlaceholder?: string;
  searchValue?: (row: T) => string;
  emptyMessage?: string;
  pageSize?: number;
}

export default function DataTable<T>({
  rows,
  columns,
  rowKey,
  searchPlaceholder = "Search...",
  searchValue,
  emptyMessage = "No results.",
  pageSize = 10,
}: DataTableProps<T>) {
  const [query, setQuery] = useState("");
  const [sortKey, setSortKey] = useState<string | null>(null);
  const [sortDir, setSortDir] = useState<"asc" | "desc">("asc");
  const [page, setPage] = useState(1);

  const filtered = useMemo(() => {
    if (!query || !searchValue) return rows;
    const q = query.toLowerCase();
    return rows.filter((row) => searchValue(row).toLowerCase().includes(q));
  }, [rows, query, searchValue]);

  const sorted = useMemo(() => {
    if (!sortKey) return filtered;
    const column = columns.find((c) => c.key === sortKey);
    if (!column?.sortValue) return filtered;
    const copy = [...filtered];
    copy.sort((a, b) => {
      const av = column.sortValue!(a);
      const bv = column.sortValue!(b);
      const cmp = av < bv ? -1 : av > bv ? 1 : 0;
      return sortDir === "asc" ? cmp : -cmp;
    });
    return copy;
  }, [filtered, sortKey, sortDir, columns]);

  const totalPages = Math.max(1, Math.ceil(sorted.length / pageSize));
  const clampedPage = Math.min(page, totalPages);
  const pageRows = sorted.slice((clampedPage - 1) * pageSize, clampedPage * pageSize);

  function toggleSort(key: string) {
    if (sortKey === key) {
      setSortDir(sortDir === "asc" ? "desc" : "asc");
    } else {
      setSortKey(key);
      setSortDir("asc");
    }
  }

  return (
    <div>
      {searchValue && (
        <div className="relative mb-3 w-72">
          <Search size={15} strokeWidth={1.75} className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-neutral-400" />
          <input
            className="w-full rounded-md border border-neutral-300 dark:border-neutral-700 py-1.5 pl-8 pr-3 text-sm dark:bg-neutral-900"
            placeholder={searchPlaceholder}
            value={query}
            onChange={(e) => {
              setQuery(e.target.value);
              setPage(1);
            }}
          />
        </div>
      )}
      <div className="overflow-x-auto rounded-lg border border-neutral-200 bg-white dark:border-neutral-800 dark:bg-neutral-900">
        <table className="w-full text-left text-sm">
          <thead>
            <tr className="border-b border-neutral-200 text-neutral-500 dark:border-neutral-800 dark:text-neutral-400">
              {columns.map((col) => (
                <th
                  key={col.key}
                  className={`px-4 py-2 font-medium ${col.sortValue ? "cursor-pointer select-none" : ""}`}
                  onClick={() => col.sortValue && toggleSort(col.key)}
                >
                  <span className="inline-flex items-center gap-1">
                    {col.header}
                    {col.sortValue &&
                      (sortKey === col.key ? (
                        <ChevronDown
                          size={13}
                          strokeWidth={2}
                          className={sortDir === "desc" ? "rotate-180 transition-transform" : "transition-transform"}
                        />
                      ) : (
                        <ChevronsUpDown size={13} strokeWidth={2} className="text-neutral-300 dark:text-neutral-600" />
                      ))}
                  </span>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {pageRows.length === 0 && (
              <tr>
                <td className="px-4 py-6 text-center text-neutral-400" colSpan={columns.length}>
                  {emptyMessage}
                </td>
              </tr>
            )}
            {pageRows.map((row) => (
              <tr
                key={rowKey(row)}
                className="border-b border-neutral-100 last:border-0 hover:bg-neutral-50 dark:border-neutral-800 dark:hover:bg-neutral-800/50"
              >
                {columns.map((col) => (
                  <td key={col.key} className="px-4 py-2">
                    {col.render(row)}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {totalPages > 1 && (
        <div className="mt-3 flex items-center gap-2 text-sm text-neutral-500">
          <button
            className="flex items-center gap-1 rounded-md border border-neutral-300 dark:border-neutral-700 px-2 py-1 disabled:opacity-40"
            disabled={clampedPage <= 1}
            onClick={() => setPage(clampedPage - 1)}
          >
            <ChevronLeft size={14} strokeWidth={1.75} />
            Previous
          </button>
          <span>
            Page {clampedPage} of {totalPages}
          </span>
          <button
            className="flex items-center gap-1 rounded-md border border-neutral-300 dark:border-neutral-700 px-2 py-1 disabled:opacity-40"
            disabled={clampedPage >= totalPages}
            onClick={() => setPage(clampedPage + 1)}
          >
            Next
            <ChevronRight size={14} strokeWidth={1.75} />
          </button>
        </div>
      )}
    </div>
  );
}
