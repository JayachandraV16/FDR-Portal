import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";

import * as api from "../lib/api";
import type { AuthUser } from "../lib/api";

export type AuthState =
  | { status: "loading" }
  | { status: "authenticated"; user: AuthUser }
  | { status: "unauthenticated" }
  | { status: "error"; message: string }; // session check failed for a reason other than "not signed in"

interface AuthContextValue {
  state: AuthState;
  /** One-off message for the login page (expired session, password changed). */
  notice: string | null;
  login: (email: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
  changePassword: (currentPassword: string, newPassword: string) => Promise<void>;
  retrySession: () => void;
}

const AuthContext = createContext<AuthContextValue | null>(null);

const SESSION_EXPIRED = "Your session has expired. Please sign in again.";
const PASSWORD_CHANGED = "Your password was changed. Please sign in with the new password.";

function describeSessionCheckFailure(err: unknown): string {
  if (err instanceof api.ApiError) {
    if (err.kind === "network") return "Cannot reach the server. Check your connection and try again.";
    return `The server could not check your session (HTTP ${err.status}). Please try again.`;
  }
  return "The session check failed unexpectedly.";
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<AuthState>({ status: "loading" });
  const [notice, setNotice] = useState<string | null>(null);
  const wasAuthenticated = useRef(false);

  const signedOut = useCallback((message: string | null) => {
    api.setCsrfToken(null);
    wasAuthenticated.current = false;
    setNotice(message);
    setState({ status: "unauthenticated" });
  }, []);

  const restore = useCallback(
    async (signal?: AbortSignal) => {
      try {
        const session = await api.fetchSession(signal);
        api.setCsrfToken(session.csrf_token);
        wasAuthenticated.current = true;
        setState({ status: "authenticated", user: session.user });
      } catch (err) {
        if (signal?.aborted) return;
        if (err instanceof api.ApiError && err.kind === "unauthorized") {
          signedOut(null); // simply not signed in
        } else {
          setState({ status: "error", message: describeSessionCheckFailure(err) });
        }
      }
    },
    [signedOut],
  );

  // Restore the session on startup from the HttpOnly cookie, via GET /auth/me.
  useEffect(() => {
    const controller = new AbortController();
    void restore(controller.signal);
    return () => controller.abort();
  }, [restore]);

  // A 401 on any later authenticated request means the session expired or was revoked.
  useEffect(() => {
    api.setUnauthorizedHandler(() => {
      if (wasAuthenticated.current) signedOut(SESSION_EXPIRED);
    });
    return () => api.setUnauthorizedHandler(null);
  }, [signedOut]);

  const login = useCallback(async (email: string, password: string) => {
    const session = await api.login(email, password);
    api.setCsrfToken(session.csrf_token);
    wasAuthenticated.current = true;
    setNotice(null);
    setState({ status: "authenticated", user: session.user });
  }, []);

  const logout = useCallback(async () => {
    try {
      await api.logout();
    } catch (err) {
      // 401 means the session is already gone; anything else leaves it alive, so report it.
      if (!(err instanceof api.ApiError && err.kind === "unauthorized")) throw err;
    }
    signedOut(null);
  }, [signedOut]);

  const changePassword = useCallback(
    async (currentPassword: string, newPassword: string) => {
      await api.changePassword(currentPassword, newPassword);
      // The backend revokes every session of this user, including this one.
      signedOut(PASSWORD_CHANGED);
    },
    [signedOut],
  );

  const retrySession = useCallback(() => {
    setState({ status: "loading" });
    void restore();
  }, [restore]);

  const value = useMemo<AuthContextValue>(
    () => ({ state, notice, login, logout, changePassword, retrySession }),
    [state, notice, login, logout, changePassword, retrySession],
  );
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used inside <AuthProvider>");
  return ctx;
}
