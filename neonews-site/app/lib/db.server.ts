import { Pool } from "pg";

import { headlineOf, slugOf } from "./issues";

export type IssueSummary = {
  slug: string;
  headline: string;
  generatedAt: string; // ISO
  coversSince: string; // ISO
  storyCount: number;
};

export type IssueDetail = IssueSummary & { body: string };

// One pool per process. NEONEWS_POSTGRES_URL is the same database neonews uses;
// this app only ever reads neonews_issues.
const url = process.env.NEONEWS_POSTGRES_URL;
if (!url) throw new Error("NEONEWS_POSTGRES_URL is not set");
const pool = new Pool({ connectionString: url });

// An idle pooled client can emit 'error' (Postgres restart, idle timeout). With
// no listener, Node treats it as unhandled and crashes the SSR process. Log and
// let the pool recycle the connection on the next query.
pool.on("error", (err) => {
  console.error("pg pool error", err);
});

type Row = {
  generated_at: Date;
  covers_since: Date;
  story_count: number;
  body: string;
};

function toSummary(row: Row): IssueSummary {
  return {
    slug: slugOf(row.generated_at),
    headline: headlineOf(row.body),
    generatedAt: row.generated_at.toISOString(),
    coversSince: row.covers_since.toISOString(),
    storyCount: row.story_count,
  };
}

export async function listIssues(): Promise<IssueSummary[]> {
  const { rows } = await pool.query<Row>(
    `SELECT generated_at, covers_since, story_count, body
       FROM neonews_issues
      WHERE body IS NOT NULL
      ORDER BY generated_at DESC`,
  );
  return rows.map(toSummary);
}

export async function getIssue(slug: string): Promise<IssueDetail | null> {
  // Slug is derived from generated_at, so match by re-deriving it in SQL:
  // to_char in UTC produces the same YYYY-MM-DD-HHMM string slugOf builds.
  const { rows } = await pool.query<Row>(
    `SELECT generated_at, covers_since, story_count, body
       FROM neonews_issues
      WHERE body IS NOT NULL
        AND to_char(generated_at AT TIME ZONE 'UTC', 'YYYY-MM-DD-HH24MI') = $1
      ORDER BY generated_at DESC
      LIMIT 1`,
    [slug],
  );
  if (rows.length === 0) return null;
  return { ...toSummary(rows[0]), body: rows[0].body };
}
