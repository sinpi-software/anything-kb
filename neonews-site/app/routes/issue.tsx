import Markdown from "react-markdown";
import { Link } from "react-router";
import remarkGfm from "remark-gfm";

import { SiteHeader } from "~/components/site-header";
import { getIssue } from "~/lib/db.server";
import type { Route } from "./+types/issue";

export function meta({ loaderData }: Route.MetaArgs) {
  return [{ title: loaderData ? `${loaderData.headline} — Longview Local` : "Not found" }];
}

export async function loader({ params }: Route.LoaderArgs) {
  const issue = await getIssue(params.slug);
  if (!issue) throw new Response(null, { status: 404, statusText: "Issue not found" });
  return issue;
}

const DATE = new Intl.DateTimeFormat("en-US", {
  timeZone: "UTC",
  month: "long",
  day: "numeric",
  year: "numeric",
});

export default function Issue({ loaderData }: Route.ComponentProps) {
  const issue = loaderData;
  return (
    <>
      <SiteHeader />
      <main className="mx-auto max-w-[var(--maxw)] px-6 py-10">
        <Link to="/" className="font-display text-sm text-accent hover:underline">
          ← All issues
        </Link>
        <p className="mt-6 text-sm text-muted">{DATE.format(new Date(issue.generatedAt))}</p>
        <article className="article-prose mt-2">
          <Markdown remarkPlugins={[remarkGfm]}>{issue.body}</Markdown>
        </article>
      </main>
    </>
  );
}
