// Links shown in the header on every authenticated (dashboard) page. The knowledge
// base is part of the path now, so these are a function of it rather than a constant.
export function appNavLinks(kbId: string) {
  return [
    { href: "/app", label: "Knowledge bases" },
    { href: `/app/${kbId}/ingest`, label: "Ingest" },
    { href: `/app/${kbId}/explore`, label: "Explore" },
    { href: `/app/${kbId}/config`, label: "Configure" },
    { href: `/app/${kbId}`, label: "API keys" },
  ];
}
