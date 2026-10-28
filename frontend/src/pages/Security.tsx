import { useState } from "react";

import DemoNotice from "../components/DemoNotice";
import { Icon } from "../components/icons";
import { AsyncView, Note, PageHeader } from "../components/ui";
import { Panel } from "../dashboard/Panel";
import { ApiError, getSessions, getSignInActivity, revokeOtherSessions, revokeSession, type SessionItem } from "../lib/api";
import { formatDateTime } from "../lib/format";
import { describeError, useAsync } from "../lib/useAsync";

type Pending = { kind: "one"; handle: string } | { kind: "others" } | null;

function failure(err: unknown): string {
  if (err instanceof ApiError && err.status === 404) return "That session was not found. It may already have ended.";
  if (err instanceof ApiError && err.status === 409) return "That is the session you are using now. Use Sign out to end it.";
  return `The change could not be made. ${describeError(err instanceof Error ? err : new Error("failed"))}. Please try again.`;
}

function Sessions() {
  const { state, reload } = useAsync((signal) => getSessions(signal));
  const [pending, setPending] = useState<Pending>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ tone: "ok" | "error"; text: string } | null>(null);

  async function confirm() {
    if (!pending) return;
    setBusy(true);
    setMessage(null);
    try {
      const result = pending.kind === "one" ? await revokeSession(pending.handle) : await revokeOtherSessions();
      setMessage({ tone: "ok", text: result.revoked === 0 ? "There were no other sessions to end." : `${result.revoked} ${result.revoked === 1 ? "session was" : "sessions were"} ended. ${result.revoked === 1 ? "It" : "They"} can no longer be used.` });
      setPending(null);
      reload();
    } catch (err) {
      setMessage({ tone: "error", text: failure(err) });
      setPending(null);
    } finally {
      setBusy(false);
    }
  }

  const others = (items: SessionItem[]) => items.filter((s) => !s.current).length;
  return (
    <Panel title="Where you are signed in" description="Every active sign-in of your account, read from the server. Ending one stops it working immediately.">
      <AsyncView state={state} reload={reload} what="your sessions">
        {(data) => (
          <>
            <ul className="divide-y divide-slate-100" aria-label="Active sessions">
              {data.items.map((s) => (
                <li key={s.handle} className="flex flex-wrap items-start justify-between gap-3 py-3 text-sm">
                  <div className="min-w-0">
                    <p className="font-medium text-slate-900">
                      {s.browser} on {s.system}
                      {s.current && <span className="ml-2 rounded border border-slate-300 bg-slate-50 px-1.5 py-0.5 text-xs font-semibold text-slate-800">This session</span>}
                    </p>
                    <p className="text-xs text-slate-600">
                      Signed in {formatDateTime(s.created_at)} · last used {formatDateTime(s.last_seen_at)}
                      {s.network && ` · network ${s.network}`}
                    </p>
                    <p className="text-xs text-slate-600">Ends by {formatDateTime(s.expires_at)} at the latest, or after {data.idle_timeout_minutes} minutes without use.</p>
                  </div>
                  {!s.current && (
                    pending?.kind === "one" && pending.handle === s.handle ? (
                      <span className="flex items-center gap-2" role="group" aria-label={`Confirm ending the ${s.browser} on ${s.system} session`}>
                        <button type="button" disabled={busy} onClick={confirm} className="rounded bg-red-800 px-3 py-1 text-xs font-medium text-white hover:bg-red-900 disabled:opacity-50">Confirm: end this session</button>
                        <button type="button" disabled={busy} onClick={() => setPending(null)} className="rounded border border-slate-300 px-3 py-1 text-xs font-medium hover:bg-slate-100">Cancel</button>
                      </span>
                    ) : (
                      <button type="button" onClick={() => setPending({ kind: "one", handle: s.handle })} aria-label={`End the ${s.browser} on ${s.system} session`} className="rounded border border-slate-300 px-3 py-1 text-xs font-medium hover:bg-slate-100">
                        End session
                      </button>
                    )
                  )}
                </li>
              ))}
            </ul>

            <div className="mt-4 border-t border-slate-100 pt-4">
              {pending?.kind === "others" ? (
                <div role="group" aria-label="Confirm signing out all other sessions" className="flex flex-wrap items-center gap-3 text-sm">
                  <span>End all {others(data.items)} other {others(data.items) === 1 ? "session" : "sessions"}? This session stays signed in.</span>
                  <button type="button" disabled={busy} onClick={confirm} className="rounded bg-red-800 px-3 py-1.5 text-xs font-medium text-white hover:bg-red-900 disabled:opacity-50">Confirm: sign out all others</button>
                  <button type="button" disabled={busy} onClick={() => setPending(null)} className="rounded border border-slate-300 px-3 py-1.5 text-xs font-medium hover:bg-slate-100">Cancel</button>
                </div>
              ) : (
                <button type="button" disabled={others(data.items) === 0} onClick={() => setPending({ kind: "others" })} className="rounded border border-slate-300 px-3 py-1.5 text-sm font-medium hover:bg-slate-100 disabled:opacity-50">
                  Sign out all other sessions
                </button>
              )}
              {others(data.items) === 0 && <p className="mt-2 text-xs text-slate-600">You have no other active sessions.</p>}
            </div>
          </>
        )}
      </AsyncView>
      {message && (
        <p role={message.tone === "error" ? "alert" : "status"} className={`mt-3 flex items-center gap-1.5 text-sm ${message.tone === "error" ? "text-red-800" : "text-emerald-900"}`}>
          {message.tone === "ok" && <Icon name="check" className="h-4 w-4" />}
          {message.text}
        </p>
      )}
    </Panel>
  );
}

function Activity() {
  const { state, reload } = useAsync((signal) => getSignInActivity(signal));
  return (
    <Panel title="Recent sign-in activity" description="Your own sign-ins, sign-outs and failed attempts, as recorded in the audit trail.">
      <AsyncView state={state} reload={reload} what="your sign-in activity">
        {(data) => (
          <>
            <p className="mb-3 text-sm text-slate-800" data-testid="previous">
              {data.previous_sign_in_at ? `Your previous successful sign-in was ${formatDateTime(data.previous_sign_in_at)}.` : "No earlier successful sign-in is on record."}
            </p>
            {data.items.length === 0 ? (
              <p className="text-sm text-slate-700">No activity is on record yet.</p>
            ) : (
              <table className="min-w-full text-left text-sm">
                <caption className="sr-only">Recent sign-in activity</caption>
                <thead className="border-b border-slate-200 text-xs uppercase tracking-wide text-slate-500">
                  <tr>
                    <th scope="col" className="py-2 pr-4 font-medium">When</th>
                    <th scope="col" className="py-2 pr-4 font-medium">Event</th>
                    <th scope="col" className="py-2 font-medium">Network</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100">
                  {data.items.map((e, i) => (
                    <tr key={i}>
                      <td className="whitespace-nowrap py-2 pr-4 text-slate-700">{formatDateTime(e.occurred_at)}</td>
                      <td className="py-2 pr-4">
                        {e.event}
                        {e.outcome !== "success" && <span className="ml-2 rounded border border-red-300 bg-red-50 px-1.5 py-0.5 text-xs font-semibold text-red-900">Not successful</span>}
                      </td>
                      <td className="py-2 text-slate-700">{e.network ?? "Not recorded"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </>
        )}
      </AsyncView>
    </Panel>
  );
}

export default function Security() {
  return (
    <div className="mx-auto max-w-4xl space-y-6">
      <PageHeader title="Account security">Your sign-in sessions and recent sign-in activity.</PageHeader>
      <DemoNotice />
      <Sessions />
      <Activity />
      <Note>
        If you see a session or a sign-in you do not recognise, end the session, then change your password: changing it signs you out everywhere.
        Browser and system are read from what each browser reports and may be inaccurate; network addresses are shown with their last part hidden.
      </Note>
    </div>
  );
}
