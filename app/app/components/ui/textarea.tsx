import type { ComponentProps } from "react";

import { cn } from "~/lib/utils";

export function Textarea({ className, ...props }: ComponentProps<"textarea">) {
  return (
    <textarea
      className={cn(
        "w-full resize-y rounded-lg border border-line-strong bg-surface px-3.5 py-2.5 font-display text-sm leading-relaxed text-ink placeholder:text-muted focus:border-accent-fill focus:outline-none disabled:opacity-50",
        className,
      )}
      {...props}
    />
  );
}
