import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError } from "./api";

export type AsyncState<T> =
  | { status: "loading" }
  | { status: "success"; data: T }
  | { status: "error"; error: ApiError | Error };

/**
 * Runs `load` on mount, on `reload()`, and whenever `key` changes (for example a page number). Errors stay errors: they are never turned into empty
 * data. Requests are aborted on unmount, and a stale response never overwrites a newer one.
 */
export function useAsync<T>(
  load: (signal: AbortSignal) => Promise<T>,
  key: string | number = 0,
): { state: AsyncState<T>; reload: () => void } {
  const loadRef = useRef(load);
  loadRef.current = load;
  const [state, setState] = useState<AsyncState<T>>({ status: "loading" });
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    setState({ status: "loading" });
    loadRef.current(controller.signal).then(
      (data) => {
        if (!controller.signal.aborted) setState({ status: "success", data });
      },
      (error: unknown) => {
        if (controller.signal.aborted) return;
        setState({ status: "error", error: error instanceof Error ? error : new Error("Request failed") });
      },
    );
    return () => controller.abort();
  }, [attempt, key]);

  const reload = useCallback(() => setAttempt((n) => n + 1), []);
  return { state, reload };
}

/** A short, user-safe description of a failed request. */
export function describeError(error: Error): string {
  if (error instanceof ApiError) {
    if (error.kind === "network") return "Cannot reach the server";
    if (error.kind === "forbidden") return "Not permitted for your role";
    if (error.kind === "unauthorized") return "Session ended";
    return `Request failed (HTTP ${error.status})`;
  }
  return "Request failed";
}
