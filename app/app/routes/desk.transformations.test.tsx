import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createRoutesStub } from "react-router";
import TransformationsPage from "./desk.transformations";

const rows = [
  { id: "1", position: 0, type: "summarize", model: null, prompt: "Summarize the article", orgId: "o", params: null, createdAt: "1970-01-01T00:00:00Z", updatedAt: "1970-01-01T00:00:00Z", createdById: null, updatedById: null },
  { id: "2", position: 1, type: "score", model: "openai/gpt-4o", prompt: "Score it", orgId: "o", params: null, createdAt: "1970-01-01T00:00:00Z", updatedAt: "1970-01-01T00:00:00Z", createdById: null, updatedById: null },
];

function renderPage() {
  const Stub = createRoutesStub([
    { path: "/desk/:org_id/transformations", Component: TransformationsPage, loader: () => ({ orgId: "o", transformations: rows }) },
  ]);
  return render(<Stub initialEntries={["/desk/o/transformations"]} />);
}

describe("TransformationsPage", () => {
  it("renders rows collapsed with prompt and params hidden", async () => {
    const { container } = renderPage();
    // createRoutesStub resolves loader data asynchronously; wait for the rows to render.
    await screen.findAllByRole("combobox");
    // Collapsed by default: the prompt textarea is not in the document.
    expect(screen.queryByDisplayValue("Summarize the article")).toBeNull();
    expect(container).toMatchSnapshot();
  });

  it("reveals the prompt and params when a row is expanded", async () => {
    renderPage();
    const expandButtons = await screen.findAllByRole("button", { name: "Expand row" });
    await userEvent.click(expandButtons[0]);
    expect(await screen.findByDisplayValue("Summarize the article")).toBeInTheDocument();
  });
});
