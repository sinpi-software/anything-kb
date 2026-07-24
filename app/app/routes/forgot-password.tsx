import { useState } from "react";
import { Link } from "react-router";

import { AuthCard } from "~/components/auth-card";
import { Alert } from "~/components/ui/alert";
import { Button } from "~/components/ui/button";
import { Field, FieldLabel } from "~/components/ui/field";
import { Input } from "~/components/ui/input";
import { ApiError, forgotPassword } from "~/lib/api";
import type { Route } from "./+types/forgot-password";

export function meta({}: Route.MetaArgs) {
  return [{ title: "Forgot password — anything/kb" }];
}

export default function ForgotPassword() {
  const [email, setEmail] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [sent, setSent] = useState(false);
  const [submitting, setSubmitting] = useState(false);

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      await forgotPassword(email);
      setSent(true);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Something went wrong. Try again.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <AuthCard
      title="Reset your password"
      description="We'll email you a link to set a new one."
      footer={
        <Link to="/login" className="text-accent hover:underline">
          Back to log in
        </Link>
      }
    >
      {sent ? (
        <Alert variant="success">
          If an account exists for that email, a reset link is on its way.
        </Alert>
      ) : (
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
          <Button type="submit" disabled={submitting} className="justify-center">
            {submitting ? "Sending…" : "Send reset link"}
          </Button>
        </form>
      )}
    </AuthCard>
  );
}
