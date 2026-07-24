import type { ReactNode } from "react";
import { Link } from "react-router";

import { ThemeToggle } from "~/components/theme-toggle";

export interface SiteHeaderProps {
  navLinks?: { href: string; label: string }[];
  actions?: ReactNode;
}

export function SiteHeader({ navLinks = [], actions }: SiteHeaderProps) {
  return (
    <header className="mx-auto flex max-w-(--maxw) items-center justify-between border-b border-line px-7 py-5">
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
      <nav className="flex items-center gap-6 font-display text-sm">
        {navLinks.map((link) => (
          <a
            key={link.href}
            href={link.href}
            className="hidden text-muted hover:text-ink sm:inline"
          >
            {link.label}
          </a>
        ))}
        <ThemeToggle />
        {actions}
      </nav>
    </header>
  );
}
