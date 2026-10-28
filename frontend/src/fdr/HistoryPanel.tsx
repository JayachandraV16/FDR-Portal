import StatusBadge from "../components/StatusBadge";
import { RetryButton } from "../dashboard/Panel";
import type { FdrHistory, HistoryEntry } from "../lib/api";
import { formatDateTime } from "../lib/format";
import { roleLabel } from "../lib/roles";
import { describeError, type AsyncState } from "../lib/useAsync";

/** The most recent correction request, which is what an institution user needs to act on. */
export function latestCorrection(history: HistoryEntry[]): HistoryEntry | null {
  for (let i = history.length - 1; i >= 0; i--) {
    if (history[i].to_status === "CORRECTION_REQUESTED") return history[i];
  }
  return null;
}

/** Status changes with the people-free essentials: what changed, by which role, when, and why. */
export default function HistoryPanel({ state, reload }: { state: AsyncState<FdrHistory>; reload: () => void }) {
  return (
    <section aria-labelledby="history-heading" className="rounded border border-slate-200 bg-white">
      <div className="border-b border-slate-200 px-4 py-3">
        <h2 id="history-heading" className="text-sm font-semibold text-slate-900">
          History
        </h2>
        {state.status === "success" && (
          <p className="mt-0.5 text-xs text-slate-500">
            {state.data.revisions.length === 0
              ? "Not submitted yet."
              : `Submitted ${state.data.revisions.length} ${state.data.revisions.length === 1 ? "time" : "times"}; every submission is kept.`}
          </p>
        )}
      </div>
      <div className="p-4">
        {state.status === "loading" && (
          <p role="status" className="text-sm text-slate-500">
            Loading history…
          </p>
        )}
        {state.status === "error" && (
          <div role="alert" className="flex flex-wrap items-center justify-between gap-3">
            <p className="text-sm text-red-800">Could not load the history. {describeError(state.error)}.</p>
            <RetryButton onClick={reload} />
          </div>
        )}
        {state.status === "success" && state.data.history.length === 0 && (
          <p className="text-sm text-slate-600">No history is recorded for this FDR.</p>
        )}
        {state.status === "success" && state.data.history.length > 0 && (
          <ol className="space-y-4">
            {state.data.history.map((entry, index) => (
              <li key={index} className="text-sm">
                <p className="flex flex-wrap items-center gap-2">
                  {entry.from_status && (
                    <>
                      <StatusBadge status={entry.from_status} />
                      <span aria-hidden="true">→</span>
                      <span className="sr-only">changed to</span>
                    </>
                  )}
                  <StatusBadge status={entry.to_status} />
                </p>
                <p className="mt-1 text-xs text-slate-500">
                  {roleLabel(entry.actor_role)} · {formatDateTime(entry.occurred_at)}
                </p>
                {entry.comment && (
                  <blockquote className="mt-1 whitespace-pre-wrap border-l-2 border-slate-300 pl-3 text-slate-800">
                    {entry.comment}
                  </blockquote>
                )}
              </li>
            ))}
          </ol>
        )}
      </div>
    </section>
  );
}
