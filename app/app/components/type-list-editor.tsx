import { Ban, Pin, Plus, X } from "lucide-react";

import { Button } from "~/components/ui/button";
import { Input } from "~/components/ui/input";
import { cn } from "~/lib/utils";
import type { TypeDef } from "~/lib/types";

interface TypeListEditorProps {
  values: TypeDef[];
  onChange: (next: TypeDef[]) => void;
  disabled?: boolean;
  namePlaceholder?: string;
  descriptionPlaceholder?: string;
}

// A list of {name, description} rows: a short name and a free-text description
// that tells the extractor what the type means in this knowledge base.
export function TypeListEditor({
  values,
  onChange,
  disabled,
  namePlaceholder,
  descriptionPlaceholder,
}: TypeListEditorProps) {
  function update(index: number, patch: Partial<TypeDef>) {
    onChange(values.map((value, i) => (i === index ? { ...value, ...patch } : value)));
  }

  return (
    <div className="flex flex-col gap-2.5">
      {values.map((value, index) => (
        <div
          key={index}
          className={cn(
            "flex flex-col gap-2 rounded-lg border border-line-strong bg-surface p-2.5 sm:flex-row sm:items-center",
            value.pinned && "border-accent-fill",
            value.banned && "opacity-60",
          )}
        >
          <Input
            className={cn("font-display sm:w-44 sm:flex-none", value.banned && "line-through")}
            placeholder={namePlaceholder ?? "TYPE_NAME"}
            disabled={disabled}
            value={value.name}
            onValueChange={(name) => update(index, { name })}
          />
          <Input
            className="flex-1"
            placeholder={descriptionPlaceholder ?? "What this type means…"}
            disabled={disabled}
            value={value.description}
            onValueChange={(description) => update(index, { description })}
          />
          <div className="flex items-center gap-1 self-end sm:self-center">
            <button
              type="button"
              onClick={() => update(index, { pinned: !value.pinned })}
              disabled={disabled}
              aria-label={value.pinned ? `Unpin ${value.name || "type"}` : `Pin ${value.name || "type"}`}
              aria-pressed={value.pinned}
              className={cn(
                "rounded-md p-1.5 text-muted hover:text-ink disabled:opacity-50",
                value.pinned && "text-accent hover:text-accent",
              )}
            >
              <Pin className="size-4" aria-hidden="true" />
            </button>
            <button
              type="button"
              onClick={() => update(index, { banned: !value.banned })}
              disabled={disabled}
              aria-label={value.banned ? `Unban ${value.name || "type"}` : `Ban ${value.name || "type"}`}
              aria-pressed={value.banned}
              className={cn(
                "rounded-md p-1.5 text-muted hover:text-ink disabled:opacity-50",
                value.banned && "text-[#c0392b] hover:text-[#c0392b] dark:text-[#e39ba3] dark:hover:text-[#e39ba3]",
              )}
            >
              <Ban className="size-4" aria-hidden="true" />
            </button>
            <button
              type="button"
              onClick={() => onChange(values.filter((_, i) => i !== index))}
              disabled={disabled}
              aria-label={`Remove ${value.name || "type"}`}
              className="rounded-md p-1.5 text-muted hover:text-ink disabled:opacity-50"
            >
              <X className="size-4" aria-hidden="true" />
            </button>
          </div>
        </div>
      ))}
      <Button
        type="button"
        variant="outline"
        onClick={() => onChange([...values, { name: "", description: "", pinned: false, banned: false }])}
        disabled={disabled}
        className="self-start text-sm"
      >
        <Plus className="size-4" aria-hidden="true" />
        Add type
      </Button>
    </div>
  );
}
