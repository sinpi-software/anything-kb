import { Button as BaseButton } from "@base-ui-components/react/button";
import { cva, type VariantProps } from "class-variance-authority";
import type { ComponentProps } from "react";

import { cn } from "~/lib/utils";

const buttonVariants = cva(
  "inline-flex items-center justify-center gap-2 rounded-lg font-display text-sm font-semibold transition-[transform,box-shadow,color,border-color] focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent-fill focus-visible:outline-offset-2 disabled:pointer-events-none disabled:opacity-50",
  {
    variants: {
      variant: {
        primary:
          "bg-accent-fill text-accent-ink px-5 py-3 hover:-translate-y-px hover:shadow-[0_8px_22px_-10px_var(--accent-fill)]",
        outline:
          "border border-line-strong text-ink px-4 py-2.5 hover:border-accent-fill hover:text-accent",
        ghost: "text-accent px-0 py-0 hover:underline underline-offset-4",
      },
    },
    defaultVariants: { variant: "primary" },
  },
);

export type ButtonProps = ComponentProps<typeof BaseButton> & VariantProps<typeof buttonVariants>;

export function Button({ className, variant, ...props }: ButtonProps) {
  return <BaseButton className={cn(buttonVariants({ variant }), className)} {...props} />;
}
