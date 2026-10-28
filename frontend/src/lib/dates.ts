/**
 * Calendar-date helpers. A date here is a plain "YYYY-MM-DD" string, the same form the API uses, and every
 * calculation is done in UTC so a result never depends on the viewer's timezone (the server's "today" is a UTC
 * date as well). Weeks start on Monday.
 */
const DAY_MS = 86_400_000;

export const parseIso = (iso: string): Date => new Date(`${iso}T00:00:00Z`);
export const toIso = (date: Date): string => date.toISOString().slice(0, 10);
export const todayIso = (): string => toIso(new Date());
export const addDays = (iso: string, days: number): string => toIso(new Date(parseIso(iso).getTime() + days * DAY_MS));

/** Monday of the week containing `iso`. */
export function startOfWeek(iso: string): string {
  const weekday = (parseIso(iso).getUTCDay() + 6) % 7; // Monday = 0
  return addDays(iso, -weekday);
}

export const firstOfMonth = (iso: string): string => `${iso.slice(0, 7)}-01`;

export function addMonths(iso: string, months: number): string {
  const d = parseIso(firstOfMonth(iso));
  d.setUTCMonth(d.getUTCMonth() + months);
  return toIso(d);
}

export const monthLabel = (iso: string): string =>
  parseIso(iso).toLocaleDateString("en-IN", { month: "long", year: "numeric", timeZone: "UTC" });

export const dayLabel = (iso: string): string =>
  parseIso(iso).toLocaleDateString("en-IN", { weekday: "long", day: "numeric", month: "long", year: "numeric", timeZone: "UTC" });

export const shortDayLabel = (iso: string): string =>
  parseIso(iso).toLocaleDateString("en-IN", { weekday: "short", day: "numeric", month: "short", timeZone: "UTC" });

export const WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"] as const;

/** The days of a range, inclusive. */
export function daysBetween(start: string, end: string): string[] {
  const days: string[] = [];
  for (let day = start; day <= end; day = addDays(day, 1)) days.push(day);
  return days;
}
