import { useEffect, useState } from "react";
import { Link, NavLink, Outlet, useLocation } from "react-router-dom";

import { useAuth } from "../auth/AuthContext";
import { roleLabel } from "../lib/roles";
import { useOnline } from "../lib/useOnline";
import ChatWidget from "./ChatWidget";
import { Wordmark } from "./brand";
import { Icon, type IconName } from "./icons";
import ThemeToggle from "./ThemeToggle";

function UserMenu() {
  const { state, logout } = useAuth();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  if (state.status !== "authenticated") return null;
  const { user } = state;

  async function onLogout() {
    setBusy(true);
    setError(null);
    try {
      await logout();
    } catch {
      setError("Sign-out failed. Please try again.");
      setBusy(false);
    }
  }

  return (
    <div className="flex items-center gap-4">
      <div className="text-right leading-tight">
        <p className="text-sm font-medium text-slate-900">{user.full_name}</p>
        <p className="text-xs text-slate-500">
          {roleLabel(user.role)} · {user.email}
        </p>
      </div>
      <Link to="/change-password" className="text-xs text-slate-600 underline hover:text-slate-900">
        Change password
      </Link>
      <button
        type="button"
        onClick={onLogout}
        disabled={busy}
        className="rounded border border-slate-300 px-3 py-1.5 text-xs font-medium text-slate-700 hover:bg-slate-100 disabled:opacity-60"
      >
        {busy ? "Signing out…" : "Sign out"}
      </button>
      {error && (
        <span role="alert" className="text-xs text-red-700">
          {error}
        </span>
      )}
    </div>
  );
}

// Every section is a real page. An item can still be marked unavailable (rendered disabled, never
// faked), and `roles` only decides what to SHOW: the backend enforces access on every request.
interface NavItem {
  label: string;
  icon: IconName;
  to?: string;
  available: boolean;
  /** Shown only to these roles. A display rule: the backend enforces access on its own. */
  roles?: readonly string[];
}

const NAV_ITEMS: NavItem[] = [
  { label: "Overview", icon: "overview", to: "/", available: true },
  { label: "Institutions", icon: "institutions", to: "/institutions", available: true },
  { label: "FDRs", icon: "fdrs", to: "/fdrs", available: true },
  { label: "Bulk import", icon: "import", to: "/import", available: true, roles: ["INSTITUTION_USER"] },
  { label: "Calendar", icon: "calendar", to: "/calendar", available: true },
  { label: "Review queue", icon: "review", to: "/review", available: true, roles: ["AICTE_REVIEWER", "AICTE_APPROVER"] },
  { label: "Workload", icon: "workload", to: "/workload", available: true, roles: ["AICTE_REVIEWER", "AICTE_APPROVER", "ADMIN"] },
  { label: "Documents", icon: "documents", to: "/documents", available: true, roles: ["INSTITUTION_USER", "AICTE_REVIEWER", "AICTE_APPROVER"] },
  { label: "Verification", icon: "verification", to: "/verification", available: true, roles: ["AICTE_REVIEWER", "AICTE_APPROVER"] },
  { label: "Alerts", icon: "alerts", to: "/alerts", available: true },
  { label: "Exceptions", icon: "exceptions", to: "/exceptions", available: true },
  { label: "Release / Refund", icon: "release", to: "/release", available: true },
  { label: "Reports", icon: "reports", to: "/reports", available: true },
  { label: "Security", icon: "security", to: "/security", available: true },
  { label: "Administration", icon: "admin", to: "/admin", available: true, roles: ["ADMIN"] },
];

export default function AppShell() {
  const { state } = useAuth();
  const role = state.status === "authenticated" ? state.user.role : "";
  const { pathname } = useLocation();
  const [menuOpen, setMenuOpen] = useState(false);
  const online = useOnline();

  // On small screens the sidebar is a drawer: it closes when you navigate, press Escape or tap outside it.
  useEffect(() => setMenuOpen(false), [pathname]);
  useEffect(() => {
    if (!menuOpen) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setMenuOpen(false);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [menuOpen]);

  return (
    <div className="flex min-h-screen bg-slate-50">
      <a href="#main-content" className="sr-only focus:not-sr-only focus:absolute focus:left-2 focus:top-2 focus:z-50 focus:rounded focus:bg-white focus:px-3 focus:py-2 focus:text-sm focus:shadow">
        Skip to main content
      </a>
      {menuOpen && <div aria-hidden="true" data-testid="nav-overlay" onClick={() => setMenuOpen(false)} className="fixed inset-0 z-20 bg-slate-900/40 lg:hidden" />}
      <aside
        id="app-sidebar"
        className={`${menuOpen ? "block" : "hidden"} fixed inset-y-0 left-0 z-30 w-64 shrink-0 overflow-y-auto border-r border-slate-200 bg-white shadow-xl lg:static lg:block lg:w-60 lg:shadow-none`}
      >
        <div className="border-b border-slate-200 px-4 py-4">
          <Wordmark />
        </div>
        <nav aria-label="Main" className="p-2">
          <ul className="space-y-1">
            {NAV_ITEMS.filter((item) => !item.roles || item.roles.includes(role)).map((item) => (
              <li key={item.label}>
                {item.available ? (
                  <NavLink
                    to={item.to ?? "/"}
                    end={item.to === "/"}
                    className={({ isActive }) =>
                      `flex items-center gap-3 rounded px-3 py-2 text-sm ${
                        isActive ? "bg-slate-900 font-medium text-white" : "text-slate-700 hover:bg-slate-100"
                      }`
                    }
                  >
                    <Icon name={item.icon} />
                    {item.label}
                  </NavLink>
                ) : (
                  <span
                    aria-disabled="true"
                    className="flex cursor-not-allowed items-center justify-between rounded px-3 py-2 text-sm text-slate-400"
                  >
                    {item.label}
                    <span className="text-[10px] uppercase tracking-wide">Not built</span>
                  </span>
                )}
              </li>
            ))}
          </ul>
        </nav>
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2 border-b border-slate-200 bg-white px-4 py-3 sm:px-6">
          <div className="flex items-center gap-3">
            <button
              type="button"
              onClick={() => setMenuOpen((open) => !open)}
              aria-label={menuOpen ? "Close navigation menu" : "Open navigation menu"}
              aria-expanded={menuOpen}
              aria-controls="app-sidebar"
              className="rounded border border-slate-300 px-2 py-1.5 text-slate-700 hover:bg-slate-100 lg:hidden"
            >
              <Icon name={menuOpen ? "close" : "menu"} />
            </button>
            <span className="text-xs text-slate-600 sm:text-sm">Demonstration data only — records are not verified</span>
          </div>
          <div className="flex flex-wrap items-center gap-3">
            <ThemeToggle />
            <UserMenu />
          </div>
        </header>
        {!online && (
          <p role="status" className="flex items-center gap-2 border-b border-amber-300 bg-amber-50 px-4 py-2 text-sm text-amber-900 sm:px-6">
            <Icon name="offline" className="h-4 w-4" />
            You are offline. Nothing can be saved or sent until the connection returns, and nothing you change is stored on this device.
          </p>
        )}
        <main id="main-content" tabIndex={-1} className="flex-1 p-4 focus:outline-none sm:p-6">
          <Outlet />
        </main>
      </div>
      <ChatWidget />
    </div>
  );
}
