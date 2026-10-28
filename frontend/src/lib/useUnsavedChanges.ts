import { useEffect } from "react";

/**
 * Ask the browser to confirm before the page is closed or reloaded while there is work that has not been saved to
 * the server. Nothing is written anywhere: the form's contents (financial details) are deliberately not kept in
 * browser storage, so the only protection is this prompt and the visible "not saved" state of the form. In-app
 * navigation is not intercepted (the router in use has no blocking API).
 */
export function useUnsavedChanges(dirty: boolean): void {
  useEffect(() => {
    if (!dirty) return;
    const warn = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = ""; // some browsers need this to show their own prompt
    };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty]);
}
