import type { ReactNode } from "react";

import { RetryButton } from "../dashboard/Panel";
import type { Severity } from "../lib/api";
import { describeError, type AsyncState } from "../lib/useAsync";

export function PageHeader({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div>
      <h1 className="text-2xl font-semibold text-slate-900">{title}</h1>
      {children && <div className="mt-1 text-sm text-slate-600">{children}</div>}
    </div>
  );
}

/** A plain statement of what a page is and is not (for example "computed, not stored"). */
export function Note({ children }: { children: ReactNode }) {
  return (
    <p role="note" className="rounded border border-slate-200 bg-slate-50 px-3 py-2 text-xs text-slate-700">
      {children}
    </p>
  );
}

/** Loading, error (with retry) and success for one request. Errors are never shown as empty data. */
export function AsyncView<T>({
  state, reload, what, children,
}: { state: AsyncState<T>; reload: () => void; what: string; children: (data: T) => ReactNode }) {
  if (state.status === "loading") {
    return <p role="status" className="text-sm text-slate-500">Loading {what}…</p>;
  }
  if (state.status === "error") {
    return (
      <div role="alert" className="flex flex-wrap items-center justify-between gap-4 rounded border border-slate-200 bg-white p-4">
        <div>
          <p className="text-sm font-medium text-red-800">Could not load {what}.</p>
          <p className="mt-0.5 text-xs text-slate-600">{describeError(state.error)}.</p>
        </div>
        <RetryButton onClick={reload} />
      </div>
    );
  }
  return <>{children(state.data)}</>;
}

const SEVERITY: Record<Severity, string> = {
  HIGH: "border-red-300 bg-red-50 text-red-900",
  MEDIUM: "border-amber-300 bg-amber-50 text-amber-900",
  LOW: "border-slate-300 bg-slate-50 text-slate-800",
};

/** Severity is always written out; colour is only a second cue. */
export function SeverityBadge({ severity }: { severity: Severity }) {
  return (
    <span className={`inline-block rounded border px-2 py-0.5 text-xs font-semibold ${SEVERITY[severity]}`}>
      {severity.charAt(0) + severity.slice(1).toLowerCase()}
    </span>
  );
}

/** Shown instead of a page the signed-in role may not use. The server still refuses the data requests. */
export function RoleNotice({ title, who }: { title: string; who: string }) {
  return (
    <div className="mx-auto max-w-3xl space-y-4">
      <h1 className="text-2xl font-semibold text-slate-900">{title}</h1>
      <div role="alert" className="rounded border border-slate-200 bg-white p-4 text-sm text-slate-700">
        This page is not available to your role. It is for {who}.
      </div>
    </div>
  );
}
