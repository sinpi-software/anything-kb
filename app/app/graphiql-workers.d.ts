// GraphiQL 5 ships an empty .d.ts for its Vite worker setup (a pure side-effect
// module). Declare it so the client-only dynamic import typechecks.
declare module "graphiql/setup-workers/vite";
