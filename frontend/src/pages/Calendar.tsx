import { useState } from "react";
import { Link } from "react-router-dom";

import { useAuth } from "../auth/AuthContext";
import DemoNotice from "../components/DemoNotice";
import { Icon, type IconName } from "../components/icons";
import { AsyncView, Note, PageHeader, SeverityBadge } from "../components/ui";
import { Panel } from "../dashboard/Panel";
import { getAlerts, getCalendar, type CalendarEvent } from "../lib/api";
import { addDays, addMonths, dayLabel, daysBetween, firstOfMonth, monthLabel, shortDayLabel, startOfWeek, todayIso, WEEKDAYS } from "../lib/dates";
import { FDR_STATUS_LABELS, fdrStatusLabel } from "../lib/format";
import { useAsync } from "../lib/useAsync";
import { InstitutionFilter } from "./FdrRegistry";

type View = "month" | "week" | "7" | "30" | "90";
const MAX_PER_CELL = 3;

const MARKERS: Record<CalendarEvent["marker"], { label: string; icon: IconName; style: string }> = {
  OVERDUE: { label: "Past maturity", icon: "exceptions", style: "border-red-300 bg-red-50 text-red-900" },
  SOON: { label: "Due soon", icon: "alerts", style: "border-amber-300 bg-amber-50 text-amber-900" },
  UPCOMING: { label: "Upcoming", icon: "calendar", style: "border-sky-200 bg-sky-50 text-sky-900" },
  NONE: { label: "", icon: "fdrs", style: "border-slate-300 bg-slate-50 text-slate-800" },
};

/** One FDR on its maturity date. The marker is always written out; colour and icon are a second cue. */
function EventChip({ event, compact }: { event: CalendarEvent; compact?: boolean }) {
  const marker = MARKERS[event.marker];
  const words = [marker.label, fdrStatusLabel(event.status)].filter(Boolean).join(" · ");
  return (
    <Link
      to={event.link}
      title={`${event.institution_name}: ${words}`}
      className={`flex items-start gap-1.5 rounded border px-1.5 py-1 text-xs leading-tight hover:underline ${marker.style}`}
    >
      <Icon name={marker.icon} className="mt-0.5 h-3.5 w-3.5" />
      <span className="min-w-0">
        <span className="block break-words font-medium">{event.fdr_number}</span>
        <span className="block break-words">{compact ? marker.label || fdrStatusLabel(event.status) : `${event.institution_code} · ${words}`}</span>
      </span>
    </Link>
  );
}

function rangeFor(view: View, cursor: string, today: string): { start: string; end: string } {
  if (view === "month") {
    const start = startOfWeek(firstOfMonth(cursor));
    return { start, end: addDays(start, 41) };
  }
  if (view === "week") {
    const start = startOfWeek(cursor);
    return { start, end: addDays(start, 6) };
  }
  return { start: today, end: addDays(today, Number(view)) };
}

function MaturityAlerts() {
  const { state, reload } = useAsync((signal) => getAlerts(signal));
  return (
    <Panel title="Maturity alerts" description="The existing alerts about maturity, from the same source as the Alerts page.">
      <AsyncView state={state} reload={reload} what="alerts">
        {(data) => {
          const items = data.items.filter((a) => a.category === "MATURITY_SOON" || a.category === "OVERDUE").slice(0, 4);
          return items.length === 0 ? (
            <p className="text-sm text-slate-700">No maturity alerts right now.</p>
          ) : (
            <ul className="space-y-2">
              {items.map((a, i) => (
                <li key={`${a.fdr_id}-${a.category}-${i}`} className="flex items-start justify-between gap-3 text-sm">
                  <span className="min-w-0">
                    <Link to={a.link} className="font-medium underline">{a.fdr_number}</Link>{" "}
                    <span className="text-xs text-slate-600">{a.message}</span>
                  </span>
                  <SeverityBadge severity={a.severity as "HIGH" | "MEDIUM" | "LOW"} />
                </li>
              ))}
            </ul>
          );
        }}
      </AsyncView>
      <p className="mt-3 text-xs"><Link to="/alerts" className="font-medium underline">All alerts</Link></p>
    </Panel>
  );
}

export default function Calendar() {
  const { state: auth } = useAuth();
  const institutionUser = auth.status === "authenticated" && auth.user.role === "INSTITUTION_USER";
  const [view, setView] = useState<View>("month");
  const [cursor, setCursor] = useState(todayIso());
  const [status, setStatus] = useState("");
  const [institution, setInstitution] = useState("");
  const { start, end } = rangeFor(view, cursor, todayIso());
  const { state, reload } = useAsync(
    (signal) => getCalendar({ start, end, status: status || undefined, institution_id: institutionUser ? undefined : institution || undefined }, signal),
    `${start}|${end}|${status}|${institution}`,
  );

  const listView = view === "7" || view === "30" || view === "90";
  const step = (direction: 1 | -1) => setCursor((c) => (view === "month" ? addMonths(c, direction) : addDays(c, 7 * direction)));
  const title = view === "month" ? monthLabel(cursor) : view === "week" ? `Week of ${shortDayLabel(startOfWeek(cursor))}` : `Next ${view} days`;
  const button = "rounded border border-slate-300 bg-white px-3 py-1.5 text-sm font-medium text-slate-800 hover:bg-slate-100 aria-pressed:bg-slate-900 aria-pressed:text-white";

  return (
    <div className="mx-auto max-w-6xl space-y-6">
      <PageHeader title="Maturity calendar">When each FDR you may see reaches its maturity date.</PageHeader>
      <DemoNotice />

      <div className="flex flex-wrap items-center gap-2" role="group" aria-label="Calendar view">
        <button type="button" className={button} aria-pressed={view === "month"} onClick={() => setView("month")}>Month</button>
        <button type="button" className={button} aria-pressed={view === "week"} onClick={() => setView("week")}>Week</button>
        {(["7", "30", "90"] as const).map((n) => (
          <button key={n} type="button" className={button} aria-pressed={view === n} onClick={() => setView(n)}>Next {n} days</button>
        ))}
      </div>

      <Panel title="Filters">
        <div className="flex flex-wrap items-end gap-4">
          <div>
            <label htmlFor="cal-status" className="block text-xs font-medium text-slate-700">Status</label>
            <select id="cal-status" value={status} onChange={(e) => setStatus(e.target.value)} className="mt-1 rounded border border-slate-300 bg-white px-3 py-1.5 text-sm">
              <option value="">All statuses</option>
              {Object.entries(FDR_STATUS_LABELS).map(([code, label]) => <option key={code} value={code}>{label}</option>)}
            </select>
          </div>
          {!institutionUser && <InstitutionFilter value={institution} onChange={setInstitution} />}
        </div>
      </Panel>

      <AsyncView state={state} reload={reload} what="the calendar">
        {(data) => {
          const byDay = new Map<string, CalendarEvent[]>();
          for (const e of data.events) byDay.set(e.maturity_date, [...(byDay.get(e.maturity_date) ?? []), e]);
          const days = daysBetween(data.start, data.end);
          const inMonth = (day: string) => view !== "month" || day.slice(0, 7) === cursor.slice(0, 7);
          return (
            <>
              <section aria-label="Upcoming maturities" className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
                {([
                  ["Past maturity", data.summary.overdue, null],
                  ["Next 7 days", data.summary.next_7, "7"],
                  ["Next 30 days", data.summary.next_30, "30"],
                  ["Next 90 days", data.summary.next_90, "90"],
                ] as const).map(([label, count, target]) => (
                  <div key={label} className="rounded border border-slate-200 bg-white p-3" data-testid={`summary-${label.toLowerCase().replace(/\s+/g, "-")}`}>
                    <p className="text-xs font-medium text-slate-600">{label}</p>
                    <p className="mt-1 text-2xl font-semibold tabular-nums">{count.toLocaleString("en-IN")}</p>
                    {target && <button type="button" onClick={() => setView(target)} className="mt-1 text-xs font-medium underline">Show list</button>}
                  </div>
                ))}
              </section>
              <p className="-mt-3 text-xs text-slate-600">Live FDRs only (submitted, returned or accepted), counted from today ({data.today}).</p>

              <Panel title={title}>
                {!listView && (
                  <div className="mb-4 flex flex-wrap items-center gap-2">
                    <button type="button" className={button} onClick={() => step(-1)}>
                      <span className="inline-flex items-center gap-1"><Icon name="chevronLeft" className="h-4 w-4" />Previous {view}</span>
                    </button>
                    <button type="button" className={button} onClick={() => setCursor(todayIso())}>Today</button>
                    <button type="button" className={button} onClick={() => step(1)}>
                      <span className="inline-flex items-center gap-1">Next {view}<Icon name="chevronRight" className="h-4 w-4" /></span>
                    </button>
                  </div>
                )}

                {data.total === 0 && <p role="status" className="mb-4 text-sm text-slate-700">No FDR matures in this period{status || institution ? " with these filters" : ""}.</p>}
                {data.truncated && <p role="status" className="mb-3 text-xs text-slate-700">Showing the first {data.events.length} of {data.total.toLocaleString("en-IN")} events. Narrow the range or the filters to see the rest.</p>}

                {listView ? (
                  data.total > 0 && (
                    <ol className="divide-y divide-slate-100" aria-label={`FDRs maturing in the next ${view} days`}>
                      {days.filter((d) => byDay.has(d)).map((day) => (
                        <li key={day} className="py-3">
                          <h3 className="text-sm font-semibold text-slate-900"><time dateTime={day}>{dayLabel(day)}</time></h3>
                          <div className="mt-2 grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
                            {byDay.get(day)!.map((e) => <EventChip key={e.fdr_id} event={e} />)}
                          </div>
                        </li>
                      ))}
                    </ol>
                  )
                ) : (
                  <>
                    <div aria-hidden="true" className="mb-1 hidden grid-cols-7 gap-1 text-center text-xs font-medium text-slate-600 sm:grid">
                      {WEEKDAYS.map((d) => <span key={d}>{d}</span>)}
                    </div>
                    <ol className="grid gap-1 sm:grid-cols-7" aria-label={title}>
                      {days.map((day) => {
                        const events = byDay.get(day) ?? [];
                        const shown = view === "month" ? events.slice(0, MAX_PER_CELL) : events;
                        const isToday = day === data.today;
                        return (
                          <li
                            key={day}
                            aria-current={isToday ? "date" : undefined}
                            className={`min-h-16 rounded border p-1.5 ${events.length === 0 ? "hidden sm:block" : ""} ${isToday ? "border-slate-900 ring-1 ring-slate-900" : "border-slate-200"} ${inMonth(day) ? "bg-white" : "bg-slate-50 text-slate-500"}`}
                          >
                            <p className="mb-1 text-xs font-medium">
                              <time dateTime={day}>
                                <span className="sm:hidden">{dayLabel(day)}</span>
                                <span className="hidden sm:inline" aria-hidden="true">{Number(day.slice(8))}</span>
                                <span className="sr-only hidden sm:block">{dayLabel(day)}</span>
                              </time>
                              {isToday && <span className="ml-1 rounded bg-slate-900 px-1 text-[10px] font-semibold text-white">Today</span>}
                            </p>
                            <div className="space-y-1">
                              {shown.map((e) => <EventChip key={e.fdr_id} event={e} compact />)}
                              {view === "month" && events.length > shown.length && (
                                <button type="button" onClick={() => { setCursor(day); setView("week"); }} className="text-xs font-medium underline">
                                  +{events.length - shown.length} more
                                </button>
                              )}
                            </div>
                          </li>
                        );
                      })}
                    </ol>
                  </>
                )}
              </Panel>

              <Note>{data.note}</Note>
            </>
          );
        }}
      </AsyncView>

      <MaturityAlerts />
    </div>
  );
}
