import { useEffect, useState } from "react";

import { fetchHealth, type HealthResponse } from "../lib/api";

type ApiState =
  | { kind: "checking" }
  | { kind: "up"; health: HealthResponse }
  | { kind: "down"; message: string };

export default function Landing() {
  const [api, setApi] = useState<ApiState>({ kind: "checking" });

  useEffect(() => {
    const controller = new AbortController();
    fetchHealth(controller.signal)
      .then((health) => setApi({ kind: "up", health }))
      .catch((err: unknown) => {
        if (controller.signal.aborted) return;
        setApi({ kind: "down", message: err instanceof Error ? err.message : "Unknown error" });
      });
    return () => controller.abort();
  }, []);

  return (
    <div className="max-w-3xl space-y-6">
      <section>
        <h1 className="text-2xl font-semibold">Centralised FDR &amp; Security Deposit Management</h1>
        <p className="mt-2 text-slate-600">
          A platform for institutions and authorised officials to record fixed deposit receipts,
          attach supporting documents, and route discrepancies to human review.
        </p>
      </section>

      <section className="rounded border border-slate-200 bg-white p-4">
        <h2 className="text-sm font-semibold">Backend status</h2>
        <p className="mt-2 text-sm" role="status">
          {api.kind === "checking" && "Checking API…"}
          {api.kind === "up" && (
            <>
              API reachable — {api.health.service} v{api.health.version} ({api.health.environment})
            </>
          )}
          {api.kind === "down" && (
            <span className="text-red-700">
              API not reachable: {api.message}. Start the backend (see README).
            </span>
          )}
        </p>
      </section>

      <section className="rounded border border-amber-200 bg-amber-50 p-4 text-sm text-amber-900">
        <h2 className="font-semibold">Project status: Checkpoint 0 (foundation)</h2>
        <p className="mt-1">
          No FDR, document, verification, or reporting features exist yet. Extracted document values
          will never be treated as bank verification, and a discrepancy will only ever mean
          &ldquo;needs human review&rdquo;.
        </p>
      </section>
    </div>
  );
}
