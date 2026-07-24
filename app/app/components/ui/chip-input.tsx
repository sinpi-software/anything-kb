import { X } from "lucide-react";
import { type KeyboardEvent, useState } from "react";

import { cn } from "~/lib/utils";

interface ChipInputProps {
  values: string[];
  onChange: (next: string[]) => void;
  placeholder?: string;
  disabled?: boolean;
}

// A tag editor: type + Enter (or comma) to add, × or Backspace to remove.
export function ChipInput({ values, onChange, placeholder, disabled }: ChipInputProps) {
  const [draft, setDraft] = useState("");

  function commit(raw: string) {
    const value = raw.trim();
    setDraft("");
    if (value && !values.includes(value)) onChange([...values, value]);
  }

  function handleKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key === "Enter" || event.key === ",") {
      event.preventDefault();
      commit(draft);
    } else if (event.key === "Backspace" && !draft && values.length > 0) {
      onChange(values.slice(0, -1));
    }
  }

  return (
    <div
      className={cn(
        "flex w-full flex-wrap items-center gap-2 rounded-lg border border-line-strong bg-surface px-2.5 py-2 focus-within:border-accent-fill",
        disabled && "opacity-50",
      )}
    >
      {values.map((value) => (
        <span
          key={value}
          className="inline-flex items-center gap-1 rounded-md bg-panel px-2 py-1 font-display text-xs text-panel-ink"
        >
          {value}
          <button
            type="button"
            onClick={() => onChange(values.filter((v) => v !== value))}
            disabled={disabled}
            aria-label={`Remove ${value}`}
            className="text-panel-muted hover:text-panel-ink disabled:opacity-50"
          >
            <X className="size-3" aria-hidden="true" />
          </button>
        </span>
      ))}
      <input
        value={draft}
        onChange={(event) => setDraft(event.target.value)}
        onKeyDown={handleKeyDown}
        onBlur={() => commit(draft)}
        disabled={disabled}
        placeholder={values.length ? "" : placeholder}
        className="min-w-[8ch] flex-1 bg-transparent px-1 py-1 text-sm text-ink placeholder:text-muted focus:outline-none disabled:opacity-50"
      />
    </div>
  );
}
