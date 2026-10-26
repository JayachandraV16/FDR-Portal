import type { ReactNode } from "react";

export function Panel({
  title,
  description,
  action,
  children,
}: {
  title: string;
  description?: string;
  action?: ReactNode;
  children: ReactNode;
}) {
  const headingId = `panel-${title.toLowerCase().replace(/[^a-z0-9]+/g, "-")}`;
  return (
    <section aria-labelledby={headingId} className="rounded border border-slate-200 bg-white">
      <div className="flex items-start justify-between gap-4 border-b border-slate-200 px-4 py-3">
        <div>
          <h2 id={headingId} className="text-sm font-semibold text-slate-900">
            {title}
          </h2>
          {description && <p className="mt-0.5 text-xs text-slate-500">{description}</p>}
        </div>
        {action}
      </div>
      <div className="p-4">{children}</div>
    </section>
  );
}

export function RetryButton({ onClick, label = "Retry" }: { onClick: () => void; label?: string }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="rounded border border-slate-300 px-2 py-1 text-xs font-medium text-slate-700 hover:bg-slate-100 focus:outline-none focus:ring-2 focus:ring-slate-300"
    >
      {label}
    </button>
  );
}
