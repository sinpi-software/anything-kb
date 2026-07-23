import type { Route } from "./+types/desk.transformations";
import { listTransformations } from "~/services/transformations.server";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "~/components/ui/table";

export async function loader({ params }: Route.LoaderArgs) {
  const orgId = params.org_id;
  return { orgId, transformations: await listTransformations(orgId) };
}

export default function TransformationsPage({ loaderData }: Route.ComponentProps) {
  const { transformations } = loaderData;
  return (
    <main className="mx-auto max-w-4xl px-6 py-10">
      <h1 className="mb-6 text-2xl font-semibold tracking-tight">Transformations</h1>
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead className="w-16">#</TableHead>
            <TableHead>Type</TableHead>
            <TableHead>Model</TableHead>
            <TableHead>Prompt</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {transformations.map((t) => (
            <TableRow key={t.id}>
              <TableCell>{t.position}</TableCell>
              <TableCell>{t.type}</TableCell>
              <TableCell>{t.model ?? "—"}</TableCell>
              <TableCell className="max-w-md truncate">{t.prompt}</TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </main>
  );
}
