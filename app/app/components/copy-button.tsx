import { useState } from "react";

import { cn } from "~/lib/utils";

export interface CopyButtonProps {
  text: string;
  className?: string;
}

export function CopyButton({ text, className }: CopyButtonProps) {
  const [copied, setCopied] = useState(false);

  async function handleClick() {
    await navigator.clipboard.writeText(text);
    setCopied(true);
    setTimeout(() => setCopied(false), 1400);
  }

  return (
    <button
      type="button"
      onClick={handleClick}
      className={cn(
        "flex-none rounded-md border border-panel-line px-2.75 py-1.5 font-display text-xs text-panel-muted hover:border-panel-ink/30 hover:text-panel-ink",
        className,
      )}
    >
      {copied ? "copied" : "copy"}
    </button>
  );
}
