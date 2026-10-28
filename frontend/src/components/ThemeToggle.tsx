import { setThemePreference, useThemePreference, type ThemePreference } from "../lib/theme";
import { Icon, type IconName } from "./icons";

const OPTIONS: Array<{ value: ThemePreference; label: string; icon: IconName }> = [
  { value: "light", label: "Light", icon: "sun" },
  { value: "dark", label: "Dark", icon: "moon" },
  { value: "system", label: "System", icon: "monitor" },
];

/** A three-way choice. It is a radio group, so it is one tab stop and the arrow keys move between options. */
export default function ThemeToggle() {
  const current = useThemePreference();
  return (
    <div role="radiogroup" aria-label="Colour theme" className="inline-flex overflow-hidden rounded border border-slate-300">
      {OPTIONS.map((o) => (
        <label
          key={o.value}
          className={`flex cursor-pointer items-center px-2 py-1.5 has-[:focus-visible]:outline has-[:focus-visible]:outline-2 has-[:focus-visible]:-outline-offset-2 has-[:focus-visible]:outline-[var(--color-focus)] ${
            current === o.value ? "bg-slate-900 text-white" : "text-slate-700 hover:bg-slate-100"
          }`}
        >
          <input
            type="radio"
            name="theme"
            value={o.value}
            checked={current === o.value}
            onChange={() => setThemePreference(o.value)}
            className="sr-only"
          />
          <Icon name={o.icon} className="h-4 w-4" />
          <span className="sr-only">{o.label}</span>
        </label>
      ))}
    </div>
  );
}
