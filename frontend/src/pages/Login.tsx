import { useState, type FormEvent } from "react";
import { Navigate, useLocation } from "react-router-dom";

import { useAuth } from "../auth/AuthContext";
import AuthLayout from "../components/AuthLayout";
import { Alert, PasswordField, SubmitButton, TextField } from "../components/FormParts";
import { ApiError } from "../lib/api";

function loginErrorMessage(err: unknown): string {
  if (err instanceof ApiError) {
    switch (err.kind) {
      case "network":
        return "Cannot reach the server. Check your connection and try again.";
      case "unauthorized":
        // The backend gives one generic answer for unknown account, wrong password, inactive and locked.
        return "Invalid email or password. After several failed attempts an account is locked for a short time.";
      case "forbidden":
        return "The request was blocked because this page is not served from an allowed address. Open the portal from the address your administrator provided.";
      case "validation":
        return "Enter a valid email address and your password.";
      case "server":
        return `The server reported an error (HTTP ${err.status}). Please try again shortly.`;
      default:
        return "Sign-in failed. Please try again.";
    }
  }
  return "Sign-in failed unexpectedly. Please try again.";
}

const AUTH_SCREENS = new Set(["/login", "/change-password"]);

export default function Login() {
  const { state, notice, login } = useAuth();
  const location = useLocation();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (state.status === "authenticated") {
    const from = (location.state as { from?: string } | null)?.from;
    // Never "return" to the auth screens themselves. A password change revokes the session while the
    // user is on /change-password, so that path is recorded as `from`; honouring it would send them
    // back to the change-password page after every login.
    const returnTo = from && !AUTH_SCREENS.has(from.split(/[?#]/)[0]) ? from : "/";
    const target = state.user.must_change_password ? "/change-password" : returnTo;
    return <Navigate to={target} replace />;
  }

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    if (busy) return;
    if (!email.trim() || !password) {
      setError("Enter your email address and password.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await login(email.trim(), password);
      // On success the state becomes "authenticated" and the redirect above takes over.
    } catch (err) {
      setError(loginErrorMessage(err));
      setPassword("");
      setBusy(false);
    }
  }

  const checking = state.status === "loading";

  return (
    <AuthLayout title="Sign in" subtitle="Use the account issued to you by the portal administrator.">
      <form onSubmit={onSubmit} className="space-y-4" noValidate>
        {notice && <Alert tone="info">{notice}</Alert>}
        {state.status === "error" && <Alert tone="error">{state.message}</Alert>}
        {error && <Alert tone="error">{error}</Alert>}
        <TextField
          label="Email"
          type="email"
          name="email"
          autoComplete="username"
          required
          autoFocus
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          disabled={busy}
        />
        <PasswordField
          label="Password"
          name="password"
          autoComplete="current-password"
          required
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          disabled={busy}
        />
        <SubmitButton busy={busy || checking} busyLabel={checking ? "Checking session…" : "Signing in…"}>
          Sign in
        </SubmitButton>
      </form>
      <p className="mt-5 text-xs leading-relaxed text-slate-500">
        There is no self-registration. If you cannot sign in, contact your administrator.
      </p>
    </AuthLayout>
  );
}
