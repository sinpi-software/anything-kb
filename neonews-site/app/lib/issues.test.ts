import { describe, expect, it } from "vitest";

import { headlineOf, slugOf } from "./issues";

describe("slugOf", () => {
  it("formats generated_at as YYYY-MM-DD-HHMM in UTC", () => {
    expect(slugOf(new Date("2026-07-26T03:13:00Z"))).toBe("2026-07-26-0313");
  });

  it("zero-pads single-digit month, day, hour, and minute", () => {
    expect(slugOf(new Date("2026-01-05T09:07:00Z"))).toBe("2026-01-05-0907");
  });

  it("uses UTC, not local time", () => {
    // Midnight UTC must not roll back a day regardless of the runner's timezone.
    expect(slugOf(new Date("2026-07-26T00:00:00Z"))).toBe("2026-07-26-0000");
  });
});

describe("headlineOf", () => {
  it("returns the text of the first level-2 heading", () => {
    const body = "# Issue — 2026-07-26\n\n## County approves budget\n\nBody text.";
    expect(headlineOf(body)).toBe("County approves budget");
  });

  it("ignores level-1 and level-3 headings", () => {
    const body = "# Big\n\n### small\n\n## The lead\n\ntext";
    expect(headlineOf(body)).toBe("The lead");
  });

  it("falls back to 'Untitled issue' when there is no level-2 heading", () => {
    expect(headlineOf("# Only an h1\n\njust prose")).toBe("Untitled issue");
  });

  it("trims surrounding whitespace from the heading text", () => {
    expect(headlineOf("##   Spaced out   \n")).toBe("Spaced out");
  });
});
