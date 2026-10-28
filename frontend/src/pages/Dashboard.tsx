import { useAuth } from "../auth/AuthContext";
import DemoNotice from "../components/DemoNotice";
import RecentFdrs from "../dashboard/RecentFdrs";
import { NeedsAttention, QuickActions, RecentActivity } from "../dashboard/SidePanels";
import SummaryCards from "../dashboard/SummaryCards";
import SystemStatus from "../dashboard/SystemStatus";
import Welcome from "../dashboard/Welcome";

export default function Dashboard() {
  const { state } = useAuth();
  // The route is only reachable when signed in; the guard handles every other state.
  if (state.status !== "authenticated") return null;
  const { user } = state;

  return (
    <div className="mx-auto max-w-7xl space-y-6">
      <Welcome user={user} />

      <DemoNotice />

      <SummaryCards role={user.role} />

      <div className="grid gap-6 lg:grid-cols-3">
        <div className="space-y-6 lg:col-span-2">
          <NeedsAttention />
          <RecentFdrs />
        </div>
        <div className="space-y-6">
          <RecentActivity />
          <SystemStatus />
          <QuickActions />
        </div>
      </div>
    </div>
  );
}
