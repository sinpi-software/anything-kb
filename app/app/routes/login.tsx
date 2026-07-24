import { useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router";

import { AuthCard } from "~/components/auth-card";
import { Alert } from "~/components/ui/alert";
import { Button } from "~/components/ui/button";
import { Field, FieldLabel } from "~/components/ui/field";
import { Input } from "~/components/ui/input";
import { ApiError, login } from "~/lib/api";
import type { Route } from "./+types/login";

export function meta({}: Route.MetaArgs) {
  return [{ title: "Log in — anything/kb" }];
}

export default function Login() {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const next = searchParams.get("next") ?? "/app";

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      await login({ email, password });
      await navigate(next);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Something went wrong. Try again.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <AuthCard
      title="Log in"
      description="Welcome back to your knowledge graph."
      footer={
        <>
          No account? <Link to="/register" className="text-accent hover:underline">Sign up</Link>
        </>
      }
    >
      <form onSubmit={handleSubmit} className="flex flex-col gap-5">
        {error ? <Alert variant="error">{error}</Alert> : null}
        <Field>
          <FieldLabel>Email</FieldLabel>
          <Input
            type="email"
            required
            autoComplete="email"
            value={email}
            onValueChange={setEmail}
          />
        </Field>
        <Field>
          <FieldLabel>Password</FieldLabel>
          <Input
            type="password"
            required
            autoComplete="current-password"
            value={password}
            onValueChange={setPassword}
          />
        </Field>
        <Button type="submit" disabled={submitting} className="justify-center">
          {submitting ? "Logging in…" : "Log in"}
        </Button>
        <Link to="/forgot-password" className="text-center text-sm text-muted hover:text-ink">
          Forgot your password?
        </Link>
      </form>
    </AuthCard>
  );
}
