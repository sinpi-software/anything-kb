import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { createRoutesStub } from "react-router";
import TransformationsPage from "./desk.transformations";

const rows = [
  { id: "1", position: 0, type: "summarize", model: null, prompt: "Summarize the article", orgId: "o", params: null, createdAt: "1970-01-01T00:00:00Z", updatedAt: "1970-01-01T00:00:00Z", createdById: null, updatedById: null },
  { id: "2", position: 1, type: "score", model: "openai/gpt-4o", prompt: "Score it", orgId: "o", params: null, createdAt: "1970-01-01T00:00:00Z", updatedAt: "1970-01-01T00:00:00Z", createdById: null, updatedById: null },
];

describe("TransformationsPage", () => {
  it("renders the transform chain table", async () => {
    const Stub = createRoutesStub([
      { path: "/desk/:org_id/transformations", Component: TransformationsPage, loader: () => ({ orgId: "o", transformations: rows }) },
    ]);
    const { container } = render(<Stub initialEntries={["/desk/o/transformations"]} />);
    // createRoutesStub resolves loader data asynchronously (no HydrateFallback),
    // so the first paint is empty; wait for real content before snapshotting.
    await screen.findByText("Summarize the article");
    expect(container).toMatchSnapshot();
  });
});
