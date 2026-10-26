import { fdrStatusLabel } from "../lib/format";

// Colour is a secondary cue only: every badge carries its text label as well.
const DOT: Record<string, string> = {
  DRAFT: "bg-slate-400",
  SUBMITTED: "bg-sky-600",
  CORRECTION_REQUESTED: "bg-amber-500",
  ACCEPTED: "bg-emerald-600",
  REJECTED: "bg-red-600",
  WITHDRAWN: "bg-slate-500",
};

const MEANING: Record<string, string> = {
  ACCEPTED: "Accepted after human review of the submitted information. This is not bank verification.",
  CORRECTION_REQUESTED: "A reviewer has asked for corrections. This means human review is needed, nothing more.",
};

export default function StatusBadge({ status }: { status: string }) {
  return (
    <span
      title={MEANING[status]}
      className="inline-flex items-center gap-1.5 rounded border border-slate-300 bg-slate-50 px-2 py-0.5 text-xs text-slate-700"
    >
      <span aria-hidden="true" className={`inline-block h-1.5 w-1.5 rounded-full ${DOT[status] ?? "bg-slate-400"}`} />
      {fdrStatusLabel(status)}
    </span>
  );
}
