import { Link } from "react-router";

import { ThemeToggle } from "./theme-toggle";

export function SiteHeader() {
  return (
    <header className="border-b border-line-strong">
      <div className="mx-auto flex max-w-[var(--maxw)] items-center justify-between px-6 py-5">
        <Link to="/" className="font-display text-2xl font-bold tracking-tight text-ink">
          Longview Local
        </Link>
        <ThemeToggle />
      </div>
    </header>
  );
}
