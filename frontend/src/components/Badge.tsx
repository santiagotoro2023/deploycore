const STYLES: Record<string, { pill: string; dot: string }> = {
  ok: { pill: "bg-emerald-50 text-emerald-700 border-emerald-200", dot: "bg-emerald-500" },
  completed: { pill: "bg-emerald-50 text-emerald-700 border-emerald-200", dot: "bg-emerald-500" },
  complete: { pill: "bg-emerald-50 text-emerald-700 border-emerald-200", dot: "bg-emerald-500" },
  active: { pill: "bg-emerald-50 text-emerald-700 border-emerald-200", dot: "bg-emerald-500" },
  failed: { pill: "bg-red-50 text-red-700 border-red-200", dot: "bg-red-500" },
  error: { pill: "bg-red-50 text-red-700 border-red-200", dot: "bg-red-500" },
  unknown: { pill: "bg-neutral-100 text-neutral-600 border-neutral-200", dot: "bg-neutral-400" },
  pending: { pill: "bg-neutral-100 text-neutral-600 border-neutral-200", dot: "bg-neutral-400" },
  uploading: { pill: "bg-amber-50 text-amber-700 border-amber-200", dot: "bg-amber-500" },
  warn: { pill: "bg-amber-50 text-amber-700 border-amber-200", dot: "bg-amber-500" },
};

const DEFAULT = { pill: "bg-blue-50 text-blue-700 border-blue-200", dot: "bg-blue-500" };

export default function Badge({ value }: { value: string }) {
  const style = STYLES[value] ?? DEFAULT;
  return (
    <span className={`inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-xs font-medium ${style.pill}`}>
      <span className={`h-1.5 w-1.5 rounded-full ${style.dot}`} />
      {value.replace(/_/g, " ")}
    </span>
  );
}
