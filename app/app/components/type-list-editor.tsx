import { Plus, X } from "lucide-react";

import { Button } from "~/components/ui/button";
import { Input } from "~/components/ui/input";
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
          className="flex flex-col gap-2 rounded-lg border border-line-strong bg-surface p-2.5 sm:flex-row sm:items-center"
        >
          <Input
            className="font-display sm:w-44 sm:flex-none"
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
          <button
            type="button"
            onClick={() => onChange(values.filter((_, i) => i !== index))}
            disabled={disabled}
            aria-label={`Remove ${value.name || "type"}`}
            className="self-end text-muted hover:text-ink disabled:opacity-50 sm:self-center"
          >
            <X className="size-4" aria-hidden="true" />
          </button>
        </div>
      ))}
      <Button
        type="button"
        variant="outline"
        onClick={() => onChange([...values, { name: "", description: "" }])}
        disabled={disabled}
        className="self-start text-sm"
      >
        <Plus className="size-4" aria-hidden="true" />
        Add type
      </Button>
    </div>
  );
}
