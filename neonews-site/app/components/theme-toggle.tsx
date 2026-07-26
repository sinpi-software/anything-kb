const THEME_ATTR = "data-theme";

function toggleTheme() {
  const root = document.documentElement;
  const current =
    root.getAttribute(THEME_ATTR) ??
    (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
  root.setAttribute(THEME_ATTR, current === "dark" ? "light" : "dark");
}

export function ThemeToggle() {
  return (
    <button
      type="button"
      onClick={toggleTheme}
      aria-label="Toggle color theme"
      className="rounded-full border border-line-strong px-3 py-1.5 font-display text-xs text-muted transition-colors hover:border-accent hover:text-ink"
    >
      theme
    </button>
  );
}
