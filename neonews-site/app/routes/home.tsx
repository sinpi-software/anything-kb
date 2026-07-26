import { Link } from "react-router";

import { SiteHeader } from "~/components/site-header";
import { listIssues } from "~/lib/db.server";
import type { Route } from "./+types/home";

export function meta({}: Route.MetaArgs) {
  return [
    { title: "Longview Local — the news" },
    { name: "description", content: "Local news for Longview, drafted daily." },
  ];
}

export async function loader() {
  return { issues: await listIssues() };
}

const DATE = new Intl.DateTimeFormat("en-US", {
  timeZone: "UTC",
  month: "long",
  day: "numeric",
  year: "numeric",
});

export default function Home({ loaderData }: Route.ComponentProps) {
  const { issues } = loaderData;
  return (
    <>
      <SiteHeader />
      <main className="mx-auto max-w-[var(--maxw)] px-6 py-10">
        {issues.length === 0 ? (
          <p className="text-muted">No issues published yet. Check back soon.</p>
        ) : (
          <ul className="divide-y divide-line">
            {issues.map((issue) => (
              <li key={issue.slug} className="py-6">
                <Link to={`/${issue.slug}`} className="group block">
                  <h2 className="font-display text-2xl font-semibold text-ink group-hover:text-accent">
                    {issue.headline}
                  </h2>
                  <p className="mt-1 text-sm text-muted">
                    {DATE.format(new Date(issue.generatedAt))} · {issue.storyCount}{" "}
                    {issue.storyCount === 1 ? "story" : "stories"}
                  </p>
                </Link>
              </li>
            ))}
          </ul>
        )}
      </main>
    </>
  );
}
