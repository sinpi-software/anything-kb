import { Field as BaseField } from "@base-ui-components/react/field";
import type { ComponentProps } from "react";

import { cn } from "~/lib/utils";

export function Field({ className, ...props }: ComponentProps<typeof BaseField.Root>) {
  return <BaseField.Root className={cn("flex flex-col gap-1.5", className)} {...props} />;
}

export function FieldLabel({ className, ...props }: ComponentProps<typeof BaseField.Label>) {
  return (
    <BaseField.Label
      className={cn("font-display text-xs font-semibold tracking-wide text-muted", className)}
      {...props}
    />
  );
}
