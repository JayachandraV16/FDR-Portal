import { useSyncExternalStore } from "react";

/** The user's choice. "system" follows the operating system and is the default. */
export type ThemePreference = "light" | "dark" | "system";
export type ResolvedTheme = "light" | "dark";

/** The only thing stored is this non-sensitive preference (never any record data). */
export const THEME_KEY = "fdr-theme";
const VALUES: readonly ThemePreference[] = ["light", "dark", "system"];

function readStored(): ThemePreference {
  try {
    const value = window.localStorage.getItem(THEME_KEY);
    return VALUES.includes(value as ThemePreference) ? (value as ThemePreference) : "system";
  } catch {
    return "system"; // storage can be blocked (private windows, strict settings)
  }
}

const media = () => (typeof window !== "undefined" && window.matchMedia ? window.matchMedia("(prefers-color-scheme: dark)") : null);

export function resolveTheme(preference: ThemePreference): ResolvedTheme {
  if (preference === "system") return media()?.matches ? "dark" : "light";
  return preference;
}

let preference: ThemePreference = readStored();
const listeners = new Set<() => void>();

function apply() {
  const root = document.documentElement;
  const resolved = resolveTheme(preference);
  root.classList.toggle("dark", resolved === "dark");
  root.style.colorScheme = resolved; // native controls, scrollbars and the autofill colours follow the theme
}

function notify() {
  apply();
  listeners.forEach((listener) => listener());
}

export function getThemePreference(): ThemePreference {
  return preference;
}

export function setThemePreference(next: ThemePreference): void {
  preference = next;
  try {
    window.localStorage.setItem(THEME_KEY, next);
  } catch {
    // the choice still applies for this visit
  }
  notify();
}

/** Read the saved choice and start following operating-system changes. Call once at start-up. */
export function initTheme(): void {
  preference = readStored();
  apply();
  media()?.addEventListener?.("change", () => preference === "system" && notify());
}

export function useThemePreference(): ThemePreference {
  return useSyncExternalStore(
    (listener) => {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    getThemePreference,
    getThemePreference,
  );
}
