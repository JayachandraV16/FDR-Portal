import type { ReactNode } from "react";

import { Wordmark } from "./brand";

/** Shared frame for the public login page and the password-change screen. */
export default function AuthLayout({ title, subtitle, children }: { title: string; subtitle?: string; children: ReactNode }) {
  return (
    <div className="flex min-h-screen flex-col bg-slate-50 lg:flex-row">
      <aside className="bg-brand-900 px-6 py-8 text-on-brand lg:flex lg:w-[42%] lg:flex-col lg:justify-between lg:px-12 lg:py-14">
        <div>
          <Wordmark tone="on-brand" />
          <h1 className="mt-8 text-2xl font-semibold leading-snug lg:text-3xl">
            FDR &amp; Security Deposit Portal
          </h1>
          <p className="mt-3 max-w-md text-sm leading-relaxed text-on-brand-muted">
            Centralised record-keeping of fixed deposit receipts and security deposits held by
            educational institutions, with role-based access and an audit trail.
          </p>
        </div>
        <p className="mt-6 hidden max-w-md text-xs leading-relaxed text-on-brand-muted lg:block">
          Authorised users only. Activity is recorded. Submitted details are reviewed by officials
          and are not a bank verification.
        </p>
      </aside>

      <main className="flex flex-1 items-center justify-center px-4 py-10 sm:px-8">
        <div className="w-full max-w-md">
          <div className="rounded border border-slate-200 bg-white p-6 shadow-sm sm:p-8">
            <h2 className="text-xl font-semibold text-slate-900">{title}</h2>
            {subtitle && <p className="mt-1 text-sm text-slate-600">{subtitle}</p>}
            <div className="mt-6">{children}</div>
          </div>
          <p className="mt-4 text-center text-xs text-slate-500">
            Demonstration data only. Records shown are not verified.
          </p>
        </div>
      </main>
    </div>
  );
}
