export interface HealthResponse {
  status: string;
  service: string;
  version: string;
  environment: string;
}

export async function fetchHealth(signal?: AbortSignal): Promise<HealthResponse> {
  const res = await fetch("/api/v1/health", { signal });
  if (!res.ok) throw new Error(`Health check failed with HTTP ${res.status}`);
  return res.json() as Promise<HealthResponse>;
}
