import type { ReactNode } from "react";
import { Link } from "react-router";

import { ThemeToggle } from "~/components/theme-toggle";

export interface SiteHeaderProps {
  navLinks?: { href: string; label: string }[];
  actions?: ReactNode;
}

export function SiteHeader({ navLinks = [], actions }: SiteHeaderProps) {
  return (
    <header className="mx-auto flex max-w-(--maxw) flex-wrap items-center justify-between gap-x-6 gap-y-3 border-b border-line px-5 py-4 sm:px-7 sm:py-5">
      <Link
        to="/"
        className="flex items-center gap-2.5 font-display text-sm font-semibold tracking-tight text-ink"
      >
        <span
          aria-hidden="true"
          className="size-3 flex-none rounded-full bg-accent-fill shadow-[0_0_0_4px_color-mix(in_srgb,var(--accent-fill)_22%,transparent)]"
        />
        anything<span className="text-muted">/kb</span>
      </Link>
      <nav className="flex flex-wrap items-center gap-4 font-display text-sm sm:gap-6">
        {navLinks.map((link) => (
          <a key={link.href} href={link.href} className="text-muted hover:text-ink">
            {link.label}
          </a>
        ))}
        <ThemeToggle />
        {actions}
      </nav>
    </header>
  );
}
