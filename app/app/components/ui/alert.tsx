import { CircleAlert, CircleCheck } from "lucide-react";
import type { ComponentProps } from "react";

import { cn } from "~/lib/utils";

const ALERT_STYLE = {
  error: "border-[#c0392b]/30 bg-[#c0392b]/10 text-[#c0392b] dark:text-[#e39ba3]",
  success: "border-t-org/30 bg-t-org/10 text-t-org",
} as const;

const ALERT_ICON = {
  error: CircleAlert,
  success: CircleCheck,
} as const;

export interface AlertProps extends ComponentProps<"div"> {
  variant: keyof typeof ALERT_STYLE;
}

export function Alert({ variant, className, children, ...props }: AlertProps) {
  const Icon = ALERT_ICON[variant];
  return (
    <div
      role="alert"
      className={cn(
        "flex items-start gap-2.5 rounded-lg border px-4 py-3 text-sm",
        ALERT_STYLE[variant],
        className,
      )}
      {...props}
    >
      <Icon className="mt-0.5 size-4 flex-none" aria-hidden="true" />
      <div>{children}</div>
    </div>
  );
}
