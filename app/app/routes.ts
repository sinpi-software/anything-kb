import { type RouteConfig, index, route } from "@react-router/dev/routes";

export default [
  index("routes/home.tsx"),
  route("desk/:org_id/transformations", "routes/desk.transformations.tsx"),
  route("api/transformations", "routes/api.transformations.ts"),
  route("api/transformations/:id", "routes/api.transformations.$id.ts"),
] satisfies RouteConfig;
