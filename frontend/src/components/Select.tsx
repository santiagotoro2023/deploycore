import { ChevronDown } from "lucide-react";
import { SelectHTMLAttributes } from "react";

export default function Select({ className = "", ...props }: SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <div className="relative">
      <select
        className={`appearance-none bg-white pr-7 focus:border-blue-400 focus:outline-none focus:ring-2 focus:ring-blue-600/15 dark:bg-neutral-900 dark:text-neutral-100 ${className}`}
        {...props}
      />
      <ChevronDown
        size={14}
        strokeWidth={2}
        className="pointer-events-none absolute right-2 top-1/2 -translate-y-1/2 text-neutral-400"
      />
    </div>
  );
}
