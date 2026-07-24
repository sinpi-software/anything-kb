import { useEffect, useRef, useState } from "react";
import { Link } from "react-router";

import { AuthCard } from "~/components/auth-card";
import { Alert } from "~/components/ui/alert";
import { ApiError, verifyEmail } from "~/lib/api";
import type { Route } from "./+types/verify-email";

export function meta({}: Route.MetaArgs) {
  return [{ title: "Verify your email — anything/kb" }];
}

type Status = "pending" | "success" | "error";

export default function VerifyEmail({ params }: Route.ComponentProps) {
  const [status, setStatus] = useState<Status>("pending");
  const [error, setError] = useState<string | null>(null);
  const requested = useRef(false);

  useEffect(() => {
    if (requested.current) return;
    requested.current = true;
    verifyEmail(params.token)
      .then(() => setStatus("success"))
      .catch((err) => {
        setError(err instanceof ApiError ? err.message : "That link is invalid or has expired.");
        setStatus("error");
      });
  }, [params.token]);

  return (
    <AuthCard title="Verify your email">
      {status === "pending" ? <p className="text-muted">Verifying your email…</p> : null}
      {status === "success" ? (
        <Alert variant="success">
          Your email is verified.{" "}
          <Link to="/app" className="underline">
            Continue to your dashboard
          </Link>
          .
        </Alert>
      ) : null}
      {status === "error" ? <Alert variant="error">{error}</Alert> : null}
    </AuthCard>
  );
}
