import { useState, type FormEvent } from "react";
import { Navigate } from "react-router-dom";

import { useAuth } from "../auth/AuthContext";
import AuthLayout from "../components/AuthLayout";
import { Alert, PasswordField, SubmitButton } from "../components/FormParts";
import { ApiError } from "../lib/api";

// Mirrors the backend length policy (backend/app/security/passwords.py) as a convenience only;
// the server remains the authority and its message is shown if it disagrees.
const MIN_LENGTH = 12;
const MAX_LENGTH = 128;

function serverMessage(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.kind === "network") return "Cannot reach the server. Check your connection and try again.";
    if (err.kind === "server") return `The server reported an error (HTTP ${err.status}). Please try again shortly.`;
    if (err.kind === "client" || err.kind === "validation") return err.message;
    if (err.kind === "forbidden") return "The request was blocked by a security check. Reload the page and try again.";
  }
  return "The password could not be changed. Please try again.";
}

export default function ChangePassword() {
  const { state, changePassword, logout } = useAuth();
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (state.status !== "authenticated") return <Navigate to="/login" replace />;
  const { user } = state;

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    if (busy) return;
    if (next.length < MIN_LENGTH || next.length > MAX_LENGTH) {
      setError(`The new password must be between ${MIN_LENGTH} and ${MAX_LENGTH} characters.`);
      return;
    }
    if (next === current) {
      setError("The new password must be different from the current password.");
      return;
    }
    if (next !== confirm) {
      setError("The new password and its confirmation do not match.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await changePassword(current, next);
      // Success signs everyone out; the route guard sends the user to the login page.
    } catch (err) {
      setError(serverMessage(err));
      setBusy(false);
    }
  }

  return (
    <AuthLayout
      title={user.must_change_password ? "Set a new password" : "Change password"}
      subtitle={
        user.must_change_password
          ? "Your account uses a temporary password. Choose a new one to continue."
          : `Signed in as ${user.email}.`
      }
    >
      <form onSubmit={onSubmit} className="space-y-4" noValidate>
        {user.must_change_password && <Alert tone="info">You must change your password before using the portal.</Alert>}
        {error && <Alert tone="error">{error}</Alert>}
        <PasswordField
          label={user.must_change_password ? "Temporary password" : "Current password"}
          name="current_password"
          autoComplete="current-password"
          required
          value={current}
          onChange={(e) => setCurrent(e.target.value)}
          disabled={busy}
        />
        <PasswordField
          label="New password"
          name="new_password"
          autoComplete="new-password"
          required
          hint={`At least ${MIN_LENGTH} characters, different from the current password and your email.`}
          value={next}
          onChange={(e) => setNext(e.target.value)}
          disabled={busy}
        />
        <PasswordField
          label="Confirm new password"
          name="confirm_password"
          autoComplete="new-password"
          required
          value={confirm}
          onChange={(e) => setConfirm(e.target.value)}
          disabled={busy}
        />
        <SubmitButton busy={busy} busyLabel="Updating password…">
          Update password
        </SubmitButton>
      </form>
      <div className="mt-5 flex items-center justify-between text-xs text-slate-500">
        <span>You will be asked to sign in again afterwards.</span>
        <button
          type="button"
          onClick={() => void logout().catch(() => undefined)}
          className="font-medium text-slate-700 underline hover:text-slate-900"
        >
          Sign out
        </button>
      </div>
    </AuthLayout>
  );
}
