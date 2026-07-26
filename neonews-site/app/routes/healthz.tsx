export function loader() {
  return new Response("ok", { headers: { "content-type": "text/plain" } });
}
