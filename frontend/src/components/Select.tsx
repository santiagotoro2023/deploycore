import { ChevronDown } from "lucide-react";
import { Children, isValidElement, KeyboardEvent, ReactNode, useEffect, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";

interface ParsedOption {
  value: string;
  label: ReactNode;
  disabled?: boolean;
}

function parseOptions(children: ReactNode): ParsedOption[] {
  const options: ParsedOption[] = [];
  Children.forEach(children, (child) => {
    if (!isValidElement(child) || child.type !== "option") return;
    const props = child.props as { value?: string; children?: ReactNode; disabled?: boolean };
    options.push({ value: String(props.value ?? ""), label: props.children, disabled: props.disabled });
  });
  return options;
}

interface SelectProps {
  className?: string;
  value: string;
  onChange: (e: { target: { value: string } }) => void;
  children: ReactNode;
  disabled?: boolean;
  id?: string;
}

/** A fully custom listbox, not a native <select>: the native element can't
 * be restyled once open (browsers always render the option popup with OS
 * chrome), which is the "still looks like plain HTML" gap this closes.
 * Accepts the same <option> children / value / onChange shape as a native
 * select so every existing call site works unchanged.
 *
 * The open option list is rendered through a portal into document.body,
 * positioned from the trigger's own bounding rect, rather than as a plain
 * absolutely-positioned child. A Select living inside a scrollable
 * container (a table wrapped in overflow-x-auto, for example) would
 * otherwise have its popup clipped by that ancestor's overflow, portaling
 * it out to the body sidesteps that entirely. */
export default function Select({ className = "", value, onChange, children, disabled, id }: SelectProps) {
  const [open, setOpen] = useState(false);
  const [highlighted, setHighlighted] = useState(0);
  const [rect, setRect] = useState<{ anchor: number; left: number; width: number; openUpward: boolean } | null>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const listRef = useRef<HTMLUListElement>(null);
  const options = parseOptions(children);
  const selected = options.find((o) => o.value === String(value ?? "")) ?? null;

  function updatePosition() {
    const el = containerRef.current;
    if (!el) return;
    const box = el.getBoundingClientRect();
    const spaceBelow = window.innerHeight - box.bottom;
    const openUpward = spaceBelow < 240 && box.top > spaceBelow;
    setRect({ anchor: openUpward ? box.top : box.bottom, left: box.left, width: box.width, openUpward });
  }

  useLayoutEffect(() => {
    if (open) updatePosition();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  useEffect(() => {
    if (!open) return;
    function onScrollOrResize() {
      updatePosition();
    }
    window.addEventListener("scroll", onScrollOrResize, true);
    window.addEventListener("resize", onScrollOrResize);
    return () => {
      window.removeEventListener("scroll", onScrollOrResize, true);
      window.removeEventListener("resize", onScrollOrResize);
    };
  }, [open]);

  useEffect(() => {
    function onClickOutside(e: MouseEvent) {
      const target = e.target as Node;
      if (containerRef.current?.contains(target)) return;
      if (listRef.current?.contains(target)) return;
      setOpen(false);
    }
    document.addEventListener("mousedown", onClickOutside);
    return () => document.removeEventListener("mousedown", onClickOutside);
  }, []);

  useEffect(() => {
    if (!open) return;
    const idx = options.findIndex((o) => o.value === String(value ?? ""));
    setHighlighted(idx >= 0 ? idx : 0);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  function selectOption(opt: ParsedOption | undefined) {
    if (!opt || opt.disabled) return;
    onChange({ target: { value: opt.value } });
    setOpen(false);
  }

  function onKeyDown(e: KeyboardEvent) {
    if (disabled) return;
    if (e.key === "ArrowDown") {
      e.preventDefault();
      if (!open) setOpen(true);
      else setHighlighted((h) => Math.min(options.length - 1, h + 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      if (!open) setOpen(true);
      else setHighlighted((h) => Math.max(0, h - 1));
    } else if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      if (open) selectOption(options[highlighted]);
      else setOpen(true);
    } else if (e.key === "Escape") {
      setOpen(false);
    } else if (e.key === "Tab") {
      setOpen(false);
    }
  }

  return (
    <div className="relative" ref={containerRef}>
      <button
        type="button"
        id={id}
        disabled={disabled}
        onClick={() => setOpen((o) => !o)}
        onKeyDown={onKeyDown}
        aria-haspopup="listbox"
        aria-expanded={open}
        className={`flex items-center justify-between bg-white pr-7 text-left focus:border-blue-400 focus:outline-none focus:ring-2 focus:ring-blue-600/15 disabled:cursor-not-allowed disabled:opacity-50 dark:bg-neutral-900 dark:text-neutral-100 ${className}`}
      >
        <span className="truncate">
          {selected ? selected.label : <span className="text-neutral-400">Select...</span>}
        </span>
      </button>
      <ChevronDown
        size={14}
        strokeWidth={2}
        className={`pointer-events-none absolute right-2 top-1/2 -translate-y-1/2 text-neutral-400 transition-transform ${open ? "rotate-180" : ""}`}
      />
      {open &&
        !disabled &&
        rect &&
        createPortal(
          <ul
            ref={listRef}
            role="listbox"
            style={{
              position: "fixed",
              top: rect.openUpward ? undefined : rect.anchor,
              bottom: rect.openUpward ? window.innerHeight - rect.anchor : undefined,
              left: rect.left,
              width: rect.width,
            }}
            className="z-50 mt-1 max-h-60 overflow-auto rounded-md border border-neutral-200 bg-white py-1 text-sm shadow-lg dark:border-neutral-700 dark:bg-neutral-800"
          >
            {options.length === 0 && <li className="px-3 py-1.5 text-neutral-400">No options</li>}
            {options.map((opt, i) => (
              <li
                key={opt.value}
                role="option"
                aria-selected={opt.value === selected?.value}
                onMouseEnter={() => setHighlighted(i)}
                onClick={() => selectOption(opt)}
                className={`cursor-pointer px-3 py-1.5 ${
                  opt.disabled
                    ? "cursor-not-allowed text-neutral-300 dark:text-neutral-600"
                    : i === highlighted
                      ? "bg-blue-600 text-white"
                      : opt.value === selected?.value
                        ? "bg-blue-50 dark:bg-blue-900/40"
                        : "hover:bg-neutral-50 dark:hover:bg-neutral-700"
                }`}
              >
                {opt.label}
              </li>
            ))}
          </ul>,
          document.body,
        )}
    </div>
  );
}
