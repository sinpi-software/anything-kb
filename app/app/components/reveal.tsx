import { useEffect, useRef } from "react";
import type { ComponentProps, ElementType } from "react";

import { cn } from "~/lib/utils";

export interface RevealProps extends ComponentProps<"div"> {
  as?: ElementType;
}

export function Reveal({ as: Tag = "div", className, ...props }: RevealProps) {
  const ref = useRef<HTMLElement>(null);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const observer = new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting) {
          el.classList.add("in");
          observer.unobserve(el);
        }
      },
      { threshold: 0.14 },
    );
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  return <Tag ref={ref} className={cn("reveal", className)} {...props} />;
}
