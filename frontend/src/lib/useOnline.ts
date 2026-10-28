import { useSyncExternalStore } from "react";

/**
 * Whether the browser believes it has a network connection. It is only a hint (a captive portal or a dead
 * server still looks "online"), so failed requests are always reported as failures too. Nothing is stored.
 */
function subscribe(listener: () => void): () => void {
  window.addEventListener("online", listener);
  window.addEventListener("offline", listener);
  return () => {
    window.removeEventListener("online", listener);
    window.removeEventListener("offline", listener);
  };
}

export function useOnline(): boolean {
  return useSyncExternalStore(subscribe, () => navigator.onLine, () => true);
}
