import { useState } from "react";

import { Alert } from "~/components/ui/alert";
import { Button } from "~/components/ui/button";
import { ApiError, resendVerification } from "~/lib/api";

export function VerifyEmailBanner({ message }: { message: string }) {
  const [sent, setSent] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [sending, setSending] = useState(false);

  async function handleResend() {
    setError(null);
    setSending(true);
    try {
      await resendVerification();
      setSent(true);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Couldn't resend right now.");
    } finally {
      setSending(false);
    }
  }

  return (
    <Alert variant="error" className="items-center justify-between gap-4">
      <div className="flex flex-col gap-1">
        <span className="font-semibold">{message}</span>
        {error ? <span>{error}</span> : null}
        {sent ? <span>Verification email sent — check your inbox.</span> : null}
      </div>
      <Button variant="outline" onClick={handleResend} disabled={sending || sent} className="flex-none">
        {sending ? "Sending…" : sent ? "Sent" : "Resend email"}
      </Button>
    </Alert>
  );
}
