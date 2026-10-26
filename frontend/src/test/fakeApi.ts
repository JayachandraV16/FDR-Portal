import { vi } from "vitest";

export interface RecordedRequest {
  method: string;
  path: string; // without the /api/v1 prefix and without the query string
  query: Record<string, string>;
  url: string;
  headers: Record<string, string>; // lower-cased names
  body: unknown;
  credentials: RequestCredentials | undefined;
}

export type Reply = Response | "network-error" | Promise<Response | "network-error">;
export type Handler = (request: RecordedRequest) => Reply;

export const jsonReply = (status: number, body: unknown): Handler => () =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
export const textReply = (status: number, body = "", contentType = "text/plain"): Handler => () =>
  new Response(body, { status, headers: { "Content-Type": contentType } });
export const networkError: Handler = () => "network-error";

export function deferred<T = void>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((r) => (resolve = r));
  return { promise, resolve };
}

/**
 * Replaces global fetch with a route table keyed "METHOD /path" (path relative to /api/v1).
 * A route may be a list: handlers are used in order and the last one repeats. Any request without
 * a route is recorded in `unexpected`, and the tests assert that list is empty.
 */
// Requests that hit no route in any test; setup.ts fails the test if this is not empty afterwards.
export const unroutedRequests: string[] = [];

export function installFakeApi(
  routes: Record<string, Handler | Handler[]>,
  { dashboardDefaults = true }: { dashboardDefaults?: boolean } = {},
) {
  const calls: RecordedRequest[] = [];
  const unexpected: string[] = [];
  const used = new Map<string, number>();
  // Scaffolding for tests that merely render the signed-in shell. Dashboard tests turn this off and
  // declare every endpoint they expect, so nothing is implied by a default.
  const table: Record<string, Handler | Handler[]> = {
    "GET /health": jsonReply(200, healthBody),
    ...(dashboardDefaults
      ? {
          "GET /health/ready": jsonReply(200, { status: "ready", database: "ok" }),
          "GET /fdrs": jsonReply(200, { items: [], page: 1, page_size: 1, total: 0, total_pages: 0 }),
          "GET /institutions": jsonReply(200, { items: [], total: 0, limit: 1, offset: 0 }),
          "GET /users": jsonReply(200, { items: [], total: 0, limit: 1, offset: 0 }),
        }
      : {}),
    ...routes,
  };

  const fetchMock = vi.fn(async (input: RequestInfo | URL, init: RequestInit = {}) => {
    const url = String(input);
    const method = (init.method ?? "GET").toUpperCase();
    const [path, queryText = ""] = url.replace(/^\/api\/v1/, "").split("?");
    const headers: Record<string, string> = {};
    for (const [name, value] of Object.entries((init.headers ?? {}) as Record<string, string>)) {
      headers[name.toLowerCase()] = value;
    }
    const request: RecordedRequest = {
      method,
      path,
      query: Object.fromEntries(new URLSearchParams(queryText)),
      url,
      headers,
      body: typeof init.body === "string" ? JSON.parse(init.body) : undefined,
      credentials: init.credentials,
    };
    calls.push(request);

    const key = `${method} ${path}`;
    const entry = table[key];
    if (!entry) {
      unexpected.push(key);
      unroutedRequests.push(key);
      return new Response("no route", { status: 599 });
    }
    const index = used.get(key) ?? 0;
    used.set(key, index + 1);
    const handler = Array.isArray(entry) ? entry[Math.min(index, entry.length - 1)] : entry;
    const reply = await handler(request);
    if (reply === "network-error") throw new TypeError("Failed to fetch");
    return reply;
  });
  vi.stubGlobal("fetch", fetchMock);

  return {
    calls,
    unexpected,
    fetchMock,
    callsTo: (key: string) => calls.filter((c) => `${c.method} ${c.path}` === key),
  };
}

const healthBody = { status: "ok", service: "fdr-portal-api", version: "0.0.0-test", environment: "test" };
