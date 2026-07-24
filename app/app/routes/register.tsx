import { useState } from "react";
import { Link, useNavigate } from "react-router";

import { AuthCard } from "~/components/auth-card";
import { Alert } from "~/components/ui/alert";
import { Button } from "~/components/ui/button";
import { Field, FieldLabel } from "~/components/ui/field";
import { Input } from "~/components/ui/input";
import { ApiError, register } from "~/lib/api";
import type { Route } from "./+types/register";

export function meta({}: Route.MetaArgs) {
  return [{ title: "Sign up — anything/kb" }];
}

export default function Register() {
  const navigate = useNavigate();

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [name, setName] = useState("");
  const [knowledgeBaseName, setKnowledgeBaseName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      await register({
        email,
        password,
        name: name || undefined,
        knowledge_base_name: knowledgeBaseName || undefined,
      });
      await navigate("/app");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Something went wrong. Try again.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <AuthCard
      title="Start free"
      description="One graph, any subject. Free to start."
      footer={
        <>
          Already have an account?{" "}
          <Link to="/login" className="text-accent hover:underline">
            Log in
          </Link>
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
            minLength={8}
            autoComplete="new-password"
            value={password}
            onValueChange={setPassword}
          />
        </Field>
        <Field>
          <FieldLabel>Name (optional)</FieldLabel>
          <Input autoComplete="name" value={name} onValueChange={setName} />
        </Field>
        <Field>
          <FieldLabel>Knowledge base name (optional)</FieldLabel>
          <Input value={knowledgeBaseName} onValueChange={setKnowledgeBaseName} />
        </Field>
        <Button type="submit" disabled={submitting} className="justify-center">
          {submitting ? "Creating your graph…" : "Start free"}
        </Button>
      </form>
    </AuthCard>
  );
}
