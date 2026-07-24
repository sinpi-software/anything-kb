import { Input as BaseInput, type InputProps } from "@base-ui-components/react/input";

import { cn } from "~/lib/utils";

export function Input({ className, ...props }: InputProps) {
  return (
    <BaseInput
      className={cn(
        "w-full rounded-lg border border-line-strong bg-surface px-3.5 py-2.5 text-ink placeholder:text-muted focus:border-accent-fill focus:outline-none disabled:opacity-50",
        className,
      )}
      {...props}
    />
  );
}
