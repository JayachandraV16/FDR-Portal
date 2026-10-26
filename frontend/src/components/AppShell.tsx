import { useState } from "react";
import { Link, NavLink, Outlet } from "react-router-dom";

import { useAuth } from "../auth/AuthContext";
import { roleLabel } from "../lib/roles";

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

// Only the Overview page exists in Checkpoint 0. The rest are listed so the
// intended navigation is visible, but they are disabled rather than faked.
const NAV_ITEMS = [
  { label: "Overview", to: "/", available: true },
  { label: "Institutions", to: "/institutions", available: true },
  { label: "FDRs", to: "/fdrs", available: true },
  { label: "Documents", available: false },
  { label: "Verification", available: false },
  { label: "Alerts", available: false },
  { label: "Exceptions", available: false },
  { label: "Release / Refund", available: false },
  { label: "Reports", available: false },
  { label: "Administration", available: false },
] as const;

export default function AppShell() {
  return (
    <div className="flex min-h-screen">
      <aside className="w-60 shrink-0 border-r border-slate-200 bg-white">
        <div className="border-b border-slate-200 px-4 py-4">
          <p className="text-sm font-semibold leading-tight">FDR &amp; Security Deposit Portal</p>
          <p className="mt-1 text-xs text-slate-500">SIH 2026 · PS 26265</p>
        </div>
        <nav aria-label="Main" className="p-2">
          <ul className="space-y-1">
            {NAV_ITEMS.map((item) => (
              <li key={item.label}>
                {item.available ? (
                  <NavLink
                    to={item.to}
                    end
                    className={({ isActive }) =>
                      `block rounded px-3 py-2 text-sm ${
                        isActive ? "bg-slate-900 text-white" : "text-slate-700 hover:bg-slate-100"
                      }`
                    }
                  >
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
        <header className="flex items-center justify-between border-b border-slate-200 bg-white px-6 py-3">
          <span className="text-sm text-slate-600">Demonstration data only — records are not verified</span>
          <UserMenu />
        </header>
        <main className="flex-1 p-6">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
