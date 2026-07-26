/** The issue's slug: YYYY-MM-DD-HHMM in UTC, matching neonews's issue file stems. */
export function slugOf(generatedAt: Date): string {
  const p = (n: number, w = 2) => String(n).padStart(w, "0");
  return (
    `${generatedAt.getUTCFullYear()}-${p(generatedAt.getUTCMonth() + 1)}-` +
    `${p(generatedAt.getUTCDate())}-${p(generatedAt.getUTCHours())}${p(generatedAt.getUTCMinutes())}`
  );
}

/** The lead headline: the first `## ` heading in the issue body, or a fallback. */
export function headlineOf(body: string): string {
  const match = body.match(/^##\s+(.+?)\s*$/m);
  return match ? match[1].trim() : "Untitled issue";
}
