import { UploadCloud } from "lucide-react";
import { ChangeEvent, useRef } from "react";

export default function FileDropzone({
  accept,
  onSelect,
  fileName,
  hint,
}: {
  accept?: string;
  onSelect: (file: File | null) => void;
  fileName?: string;
  hint?: string;
}) {
  const inputRef = useRef<HTMLInputElement>(null);

  function handleChange(e: ChangeEvent<HTMLInputElement>) {
    onSelect(e.target.files?.[0] ?? null);
  }

  return (
    <div
      className="flex cursor-pointer flex-col items-center justify-center rounded-md border border-dashed border-neutral-300 px-4 py-6 text-center hover:border-neutral-400 hover:bg-neutral-50 dark:border-neutral-700 dark:hover:border-neutral-600 dark:hover:bg-neutral-800/50"
      onClick={() => inputRef.current?.click()}
    >
      <UploadCloud size={20} strokeWidth={1.5} className="mb-2 text-neutral-400" />
      <p className="text-sm text-neutral-600 dark:text-neutral-300">{fileName || "Click to choose a file"}</p>
      {hint && <p className="mt-1 text-xs text-neutral-400">{hint}</p>}
      <input ref={inputRef} type="file" accept={accept} className="hidden" onChange={handleChange} />
    </div>
  );
}
