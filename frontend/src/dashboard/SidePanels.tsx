import { Link } from "react-router-dom";

import { Panel } from "./Panel";

/** Only destinations that exist as actions today; sections are reached from the sidebar. */
export function QuickActions() {
  return (
    <Panel title="Quick actions">
      <ul className="space-y-2 text-sm">
        <li>
          <Link to="/change-password" className="font-medium text-slate-900 underline hover:text-slate-700">
            Change your password
          </Link>
        </li>
      </ul>
      <p className="mt-4 text-xs text-slate-500">
        The Institutions and FDR Registry pages are available from the sidebar. Documents, verification,
        alerts, exceptions, release / refund, reports and administration are not built yet.
      </p>
    </Panel>
  );
}

/** There is no endpoint to read audit events or FDR status history, so nothing is listed. */
export function ActivityUnavailable() {
  return (
    <Panel title="Recent activity">
      <p className="text-sm font-medium text-slate-700">Not available</p>
      <p className="mt-1 text-xs text-slate-500">
        The API does not yet provide a way to read audit events or status history, so no activity is shown
        rather than guessing.
      </p>
    </Panel>
  );
}
