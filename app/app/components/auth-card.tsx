import type { ReactNode } from "react";

import { SiteHeader } from "~/components/site-header";
import { Card, CardDescription, CardTitle } from "~/components/ui/card";

export interface AuthCardProps {
  title: string;
  description?: string;
  children: ReactNode;
  footer?: ReactNode;
}

export function AuthCard({ title, description, children, footer }: AuthCardProps) {
  return (
    <div className="flex min-h-svh flex-col">
      <SiteHeader />
      <main className="flex flex-1 items-center justify-center px-6 py-16">
        <div className="w-full max-w-md">
          <Card>
            <CardTitle>{title}</CardTitle>
            {description ? <CardDescription>{description}</CardDescription> : null}
            <div className="mt-6 flex flex-col gap-5">{children}</div>
          </Card>
          {footer ? (
            <p className="mt-6 text-center text-sm text-muted">{footer}</p>
          ) : null}
        </div>
      </main>
    </div>
  );
}
