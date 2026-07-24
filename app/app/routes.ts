import { type RouteConfig, index, route } from "@react-router/dev/routes";

export default [
  index("routes/home.tsx"),
  route("login", "routes/login.tsx"),
  route("register", "routes/register.tsx"),
  route("forgot-password", "routes/forgot-password.tsx"),
  route("reset-password/:token", "routes/reset-password.tsx"),
  route("verify-email/:token", "routes/verify-email.tsx"),
  route("app", "routes/dashboard.tsx"),
  route("app/ingest", "routes/ingest.tsx"),
  route("app/config", "routes/config.tsx"),
] satisfies RouteConfig;
