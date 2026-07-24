import { useState } from "react";
import { useNavigate } from "react-router";

import { AuthCard } from "~/components/auth-card";
import { Alert } from "~/components/ui/alert";
import { Button } from "~/components/ui/button";
import { Field, FieldLabel } from "~/components/ui/field";
import { Input } from "~/components/ui/input";
import { ApiError, resetPassword } from "~/lib/api";
import type { Route } from "./+types/reset-password";

export function meta({}: Route.MetaArgs) {
  return [{ title: "Set a new password — anything/kb" }];
}

export default function ResetPassword({ params }: Route.ComponentProps) {
  const navigate = useNavigate();
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      await resetPassword(params.token, password);
      await navigate("/app");
    } catch (err) {
      setError(
        err instanceof ApiError
          ? err.message
          : "That reset link is invalid or has expired.",
      );
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <AuthCard title="Set a new password">
      <form onSubmit={handleSubmit} className="flex flex-col gap-5">
        {error ? <Alert variant="error">{error}</Alert> : null}
        <Field>
          <FieldLabel>New password</FieldLabel>
          <Input
            type="password"
            required
            minLength={8}
            autoComplete="new-password"
            value={password}
            onValueChange={setPassword}
          />
        </Field>
        <Button type="submit" disabled={submitting} className="justify-center">
          {submitting ? "Saving…" : "Set new password"}
        </Button>
      </form>
    </AuthCard>
  );
}
