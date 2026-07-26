import { type RouteConfig, index, route } from "@react-router/dev/routes";

export default [
  index("routes/home.tsx"),
  route("login", "routes/login.tsx"),
  route("register", "routes/register.tsx"),
  route("forgot-password", "routes/forgot-password.tsx"),
  route("reset-password/:token", "routes/reset-password.tsx"),
  route("verify-email/:token", "routes/verify-email.tsx"),
  route("app", "routes/knowledge-bases.tsx"),
  route("app/:kbId", "routes/dashboard.tsx"),
  route("app/:kbId/ingest", "routes/ingest.tsx"),
  route("app/:kbId/config", "routes/config.tsx"),
  route("app/:kbId/explore", "routes/explore.tsx"),
  route("app/:kbId/entity/:id", "routes/entity.tsx"),
] satisfies RouteConfig;
