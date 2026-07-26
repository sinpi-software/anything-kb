import { type RouteConfig, index, route } from "@react-router/dev/routes";

export default [
  index("routes/home.tsx"),
  route(":slug", "routes/issue.tsx"),
  route("healthz", "routes/healthz.tsx"),
] satisfies RouteConfig;
