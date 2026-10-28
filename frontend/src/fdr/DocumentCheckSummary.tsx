import { Icon } from "../components/icons";
import type { DocumentCheckSummary as Summary } from "../lib/api";

/** Field names as the review page labels them (the server sends names only). */
const FIELD_LABELS: Record<string, string> = {
  bank_name: "bank",
  branch_name: "branch",
  fdr_number: "FDR number",
  depositor_name: "depositor",
  principal_amount: "principal amount",
  currency: "currency",
  issue_date: "issue date",
  maturity_date: "maturity date",
  interest_rate_pct: "interest rate",
};

export const fieldLabel = (name: string): string => FIELD_LABELS[name] ?? name;

const plural = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`;

/** The review queue's "Document check" cell. It reports what stored extraction results show, in neutral
 * words: a mismatch is a difference between a document and the submitted record that a person should
 * look at, never a finding that a document is fake or genuine, and "Not analyzed" is not a discrepancy. */
export default function DocumentCheckCell({ summary }: { summary: Summary }) {
  const other = [
    summary.ambiguous_count > 0 ? `${summary.ambiguous_count} ambiguous` : null,
    summary.not_found_count > 0 ? `${summary.not_found_count} not found` : null,
    summary.not_comparable_count > 0 ? `${summary.not_comparable_count} not comparable` : null,
  ].filter((part): part is string => part !== null);

  let headline: string;
  let tone = "text-slate-700";
  switch (summary.status) {
    case "NO_DOCUMENTS":
      headline = "No documents";
      tone = "text-slate-500";
      break;
    case "NOT_ANALYZED":
      headline = "Not analyzed";
      break;
    case "PENDING":
      headline = "Check in progress";
      tone = "text-sky-900";
      break;
    case "FAILED":
      headline = "Check failed";
      tone = "text-amber-900";
      break;
    case "UNAVAILABLE":
      headline = "Summary unavailable";
      tone = "text-amber-900";
      break;
    default:
      headline = summary.mismatch_count > 0 ? plural(summary.mismatch_count, "mismatch", "mismatches") : "No mismatches found";
      tone = summary.mismatch_count > 0 ? "font-semibold text-red-800" : "text-slate-800";
  }

  const partial = summary.status === "ANALYZED" && (summary.documents_analyzed < summary.documents_total);
  return (
    <div data-status={summary.status} data-mismatches={summary.mismatch_count}>
      <p className={`text-sm ${tone}`}>
        {summary.status === "ANALYZED" && summary.mismatch_count > 0 && <Icon name="close" className="mr-1 inline h-4 w-4" />}
        {headline}
      </p>
      {summary.status === "ANALYZED" && summary.mismatch_fields.length > 0 && (
        <p className="text-xs text-slate-700">{summary.mismatch_fields.map(fieldLabel).join(", ")}</p>
      )}
      {summary.status === "ANALYZED" && other.length > 0 && <p className="text-xs text-slate-600">{other.join(" · ")}</p>}
      {partial && (
        <p className="text-xs text-slate-600">
          {summary.documents_analyzed} of {plural(summary.documents_total, "document", "documents")} analyzed
          {summary.documents_failed > 0 ? `, ${summary.documents_failed} failed` : ""}
        </p>
      )}
      {summary.status === "NOT_ANALYZED" && (
        <p className="text-xs text-slate-500">{plural(summary.documents_total, "document", "documents")} uploaded</p>
      )}
    </div>
  );
}
