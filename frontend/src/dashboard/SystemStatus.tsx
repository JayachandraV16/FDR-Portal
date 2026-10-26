import { ApiError, fetchHealth, fetchReady } from "../lib/api";
import { useAsync, type AsyncState } from "../lib/useAsync";
import { Panel, RetryButton } from "./Panel";

type Tone = "ok" | "bad" | "neutral";

function Row({ label, tone, children }: { label: string; tone: Tone; children: React.ReactNode }) {
  const dot = tone === "ok" ? "bg-emerald-600" : tone === "bad" ? "bg-red-600" : "bg-slate-400";
  return (
    <div className="flex items-start justify-between gap-4 py-2 text-sm">
      <dt className="font-medium text-slate-700">{label}</dt>
      <dd className="flex items-center gap-2 text-right text-slate-700">
        <span aria-hidden="true" className={`inline-block h-2 w-2 shrink-0 rounded-full ${dot}`} />
        <span>{children}</span>
      </dd>
    </div>
  );
}

function apiRow(state: AsyncState<Awaited<ReturnType<typeof fetchHealth>>>) {
  if (state.status === "loading") return <Row label="API service" tone="neutral">Checking…</Row>;
  if (state.status === "error") {
    const network = state.error instanceof ApiError && state.error.kind === "network";
    return (
      <Row label="API service" tone="bad">
        {network ? "Unavailable — cannot reach the API" : `Unavailable — ${state.error.message}`}
      </Row>
    );
  }
  const { service, version, environment } = state.data;
  return (
    <Row label="API service" tone="ok">
      Reachable — {service} v{version} ({environment})
    </Row>
  );
}

function databaseRow(state: AsyncState<Awaited<ReturnType<typeof fetchReady>>>) {
  if (state.status === "loading") return <Row label="Database" tone="neutral">Checking…</Row>;
  if (state.status === "error") {
    if (state.error instanceof ApiError && state.error.kind === "network") {
      return <Row label="Database" tone="neutral">Unknown — API not reachable</Row>;
    }
    const status = state.error instanceof ApiError ? ` (HTTP ${state.error.status})` : "";
    return <Row label="Database" tone="bad">Not ready{status}</Row>;
  }
  return state.data.database === "ok" ? (
    <Row label="Database" tone="ok">Ready — accepting queries</Row>
  ) : (
    <Row label="Database" tone="bad">Not ready</Row>
  );
}

/** Two separate checks: /health (the API process is up) and /health/ready (the database answers). */
export default function SystemStatus() {
  const health = useAsync(fetchHealth);
  const ready = useAsync(fetchReady);
  const recheck = () => {
    health.reload();
    ready.reload();
  };
  return (
    <Panel
      title="System status"
      description="API liveness and database readiness are checked separately."
      action={<RetryButton onClick={recheck} label="Recheck" />}
    >
      <dl className="divide-y divide-slate-100">
        {apiRow(health.state)}
        {databaseRow(ready.state)}
      </dl>
    </Panel>
  );
}
