/**
 * The portal's own mark: a record (page with ledger lines) carrying a confirmation tick. It is an original
 * geometric design and deliberately does not resemble any government, AICTE or bank seal.
 */
export function LogoMark({ className = "h-8 w-8" }: { className?: string }) {
  return (
    <svg viewBox="0 0 48 48" className={`${className} shrink-0`} aria-hidden="true" focusable="false">
      <rect width="48" height="48" rx="10" fill="#1e3a8a" />
      <path d="M15 9h14l7 7v21a2 2 0 0 1-2 2H15a2 2 0 0 1-2-2V11a2 2 0 0 1 2-2z" fill="#f8fafc" />
      <path d="M29 9v5a2 2 0 0 0 2 2h5z" fill="#c7d2e8" />
      <path d="M17 22h14M17 27h14M17 32h8" stroke="#1e3a8a" strokeWidth="2" strokeLinecap="round" />
      <circle cx="35" cy="35" r="8" fill="#3b6fd4" stroke="#f8fafc" strokeWidth="2" />
      <path d="m31.5 35 2.5 2.5 4.5-5" fill="none" stroke="#f8fafc" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

/** Mark plus the product name. `tone="on-brand"` is for the fixed navy panel on the sign-in page. */
export function Wordmark({ tone = "default", compact = false }: { tone?: "default" | "on-brand"; compact?: boolean }) {
  const title = tone === "on-brand" ? "text-on-brand" : "text-slate-900";
  const sub = tone === "on-brand" ? "text-on-brand-muted" : "text-slate-500";
  return (
    <span className="flex items-center gap-3">
      <LogoMark className={compact ? "h-8 w-8" : "h-10 w-10"} />
      <span className="leading-tight">
        <span className={`block text-sm font-semibold ${title}`}>FDR &amp; Security Deposit Portal</span>
        {!compact && <span className={`block text-xs ${sub}`}>SIH 2026 · PS 26265</span>}
      </span>
    </span>
  );
}
