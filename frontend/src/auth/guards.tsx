import { Navigate, Outlet, useLocation } from "react-router-dom";

import { useAuth } from "./AuthContext";

function Centered({ children }: { children: React.ReactNode }) {
  return <div className="flex min-h-screen items-center justify-center bg-slate-50 p-6">{children}</div>;
}

/** Any signed-in user, including one who still has to change a temporary password. */
export function RequireSession() {
  const { state, retrySession } = useAuth();
  const location = useLocation();

  if (state.status === "loading") {
    return (
      <Centered>
        <p role="status" className="text-sm text-slate-600">
          Checking your session…
        </p>
      </Centered>
    );
  }
  if (state.status === "error") {
    return (
      <Centered>
        <div role="alert" className="w-full max-w-sm rounded border border-red-200 bg-white p-5 text-sm">
          <h1 className="font-semibold text-red-800">Unable to verify your session</h1>
          <p className="mt-2 text-slate-700">{state.message}</p>
          <button
            type="button"
            onClick={retrySession}
            className="mt-4 rounded bg-slate-900 px-3 py-2 text-sm font-medium text-white hover:bg-slate-800"
          >
            Try again
          </button>
        </div>
      </Centered>
    );
  }
  if (state.status === "unauthenticated") {
    return <Navigate to="/login" replace state={{ from: location.pathname + location.search }} />;
  }
  return <Outlet />;
}

/** Signed-in users whose password is current; others are sent to the mandatory change screen. */
export function RequireCurrentPassword() {
  const { state } = useAuth();
  if (state.status === "authenticated" && state.user.must_change_password) {
    return <Navigate to="/change-password" replace />;
  }
  return <Outlet />;
}
