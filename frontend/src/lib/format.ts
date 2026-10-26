/** Formats an exact decimal string (as the API sends money) as INR with Indian digit grouping,
 * without passing it through a floating-point number. Returns the input unchanged if it is not a
 * plain decimal, so a malformed value is visible rather than silently altered. */
export function formatInr(amount: string): string {
  const match = /^(-?)(\d+)(?:\.(\d+))?$/.exec(amount);
  if (!match) return amount;
  const [, sign, integer, fraction = ""] = match;
  const lastThree = integer.slice(-3);
  const rest = integer.slice(0, -3);
  const grouped = rest ? `${rest.replace(/\B(?=(\d{2})+(?!\d))/g, ",")},${lastThree}` : lastThree;
  return `${sign}₹${grouped}.${(fraction + "00").slice(0, 2)}`;
}

/** "2027-03-31" -> "31 Mar 2027". Dates arrive as ISO calendar dates; no timezone shifting. */
export function formatDate(isoDate: string): string {
  const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(isoDate);
  if (!match) return isoDate;
  const months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  const month = months[Number(match[2]) - 1];
  return month ? `${Number(match[3])} ${month} ${match[1]}` : isoDate;
}

export const FDR_STATUS_LABELS: Record<string, string> = {
  DRAFT: "Draft",
  SUBMITTED: "Submitted",
  CORRECTION_REQUESTED: "Correction requested",
  ACCEPTED: "Accepted",
  REJECTED: "Rejected",
  WITHDRAWN: "Withdrawn",
};

export const fdrStatusLabel = (status: string): string => FDR_STATUS_LABELS[status] ?? status;
