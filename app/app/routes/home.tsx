import type { Route } from "./+types/home";
import { Button } from "~/components/ui/button";

export function meta(_: Route.MetaArgs) {
  return [
    { title: "anything handwritten" },
    { name: "description", content: "TypeScript · React Router · Tailwind · shadcn — server-rendered." },
  ];
}

export function loader() {
  return { renderedAt: new Date().toISOString() };
}

export default function Home({ loaderData }: Route.ComponentProps) {
  return (
    <main className="flex min-h-svh flex-col items-center justify-center gap-6 px-6 text-center">
      <h1 className="text-3xl font-semibold tracking-tight sm:text-4xl">
        anything handwritten
      </h1>
      <p className="max-w-prose text-balance text-muted-foreground">
        TypeScript, React Router, Tailwind, and shadcn/ui — rendered on the server and
        hydrated on the client.
      </p>
      <p className="text-sm text-muted-foreground">
        Server-rendered at <time dateTime={loaderData.renderedAt}>{loaderData.renderedAt}</time>
      </p>
      <Button>Get started</Button>
    </main>
  );
}
