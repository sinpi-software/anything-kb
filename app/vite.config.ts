import { reactRouter } from "@react-router/dev/vite";
import tailwindcss from "@tailwindcss/vite";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [tailwindcss(), reactRouter()],
  resolve: {
    tsconfigPaths: true,
  },
  optimizeDeps: {
    // `graphiql/setup-workers/vite` imports monaco's workers with a `?worker`
    // suffix. Vite's normal transform pipeline understands that suffix; the
    // dep optimizer (rolldown) does not, and tries to load a file literally
    // named `...worker.js?worker`, which does not exist.
    //
    // Dep optimization is all-or-nothing, so that one failure emitted NO
    // pre-bundled deps at all — and `react-force-graph-2d`, an innocent
    // bystander, 404'd on its dynamic import, leaving Explore's graph blank.
    // Excluding the subpath keeps `graphiql` itself optimized.
    exclude: ["graphiql/setup-workers/vite"],
  },
  server: {
    // `app/lib/api.ts` calls the engine same-origin at `/api` — in the cluster
    // Traefik serves the app and the API on one host and the longer `/api`
    // prefix wins. Locally they are two origins (Vite :5173, engine :8000), so
    // without this every client-side call 404s into Vite's HTML fallback and
    // the login form reports a generic error that looks like a bad password.
    // Dev-server only; `react-router build` output never sees this.
    proxy: {
      "/api": {
        // In Compose the engine resolves as `ingestion-api`; on the host it is
        // localhost. INTERNAL_API_URL is already set for the SSR path.
        target: process.env.INTERNAL_API_URL ?? "http://localhost:8000",
        changeOrigin: false, // preserve Origin: the engine's CSRF check reads it
      },
    },
  },
});
