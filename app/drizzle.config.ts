import "dotenv/config";
import { defineConfig } from "drizzle-kit";

export default defineConfig({
  dialect: "postgresql",
  schema: "./app/db/schema.ts",
  out: "./app/db",
  dbCredentials: { url: process.env.DATABASE_URL as string },
  introspect: { casing: "camel" },
});
