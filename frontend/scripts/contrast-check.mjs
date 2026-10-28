// Computes WCAG 2.x contrast ratios for the foreground/background pairs the interface actually uses, in both
// themes. Run with: node scripts/contrast-check.mjs   (exit code 1 if a required pair is below its threshold).
// The dark values are read from src/index.css so this cannot drift from the real tokens; the light values are
// Tailwind's default palette (sRGB approximations of its oklch definitions).
import fs from "node:fs";

const css = fs.readFileSync(new URL("../src/index.css", import.meta.url), "utf8");
const darkBlock = css.slice(css.indexOf(".dark {"));
const dark = Object.fromEntries([...darkBlock.matchAll(/--color-([a-z]+(?:-\d+)?):\s*(#[0-9a-fA-F]{6})/g)].map((m) => [m[1], m[2]]));

const lightBlock = css.slice(css.indexOf(":root {"), css.indexOf(".dark {"));
const lightOverrides = Object.fromEntries([...lightBlock.matchAll(/--color-([a-z]+(?:-\d+)?):\s*(#[0-9a-fA-F]{6})/g)].map((m) => [m[1], m[2]]));

const tailwindLight = {
  white: "#ffffff", "slate-50": "#f8fafc", "slate-100": "#f1f5f9", "slate-200": "#e2e8f0", "slate-300": "#cbd5e1",
  "slate-400": "#94a3b8", "slate-500": "#64748b", "slate-600": "#475569", "slate-700": "#334155", "slate-800": "#1e293b",
  "slate-900": "#0f172a", "red-50": "#fef2f2", "red-600": "#dc2626", "red-700": "#b91c1c", "red-800": "#991b1b", "red-900": "#7f1d1d",
  "amber-50": "#fffbeb", "amber-900": "#78350f", "amber-950": "#451a03", "sky-50": "#f0f9ff", "sky-900": "#0c4a6e",
  "emerald-50": "#ecfdf5", "emerald-900": "#064e3b", "emerald-600": "#059669",
};
const light = { ...tailwindLight, ...lightOverrides };
const brand = { "brand-900": "#14234b", "on-brand": "#f8fafc", "on-brand-muted": "#c7d2e8" };

function lum(hex) {
  const [r, g, b] = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255).map((c) => (c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4));
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}
const ratio = (a, b) => {
  const [hi, lo] = [lum(a), lum(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
};

// [foreground, background, minimum ratio, where it is used]
const PAIRS = [
  ["slate-900", "white", 4.5, "primary text on cards"],
  ["slate-900", "slate-50", 4.5, "primary text on the page"],
  ["slate-800", "white", 4.5, "text"], ["slate-700", "white", 4.5, "body text"], ["slate-700", "slate-50", 4.5, "notes"],
  ["slate-700", "slate-100", 4.5, "hover rows"], ["slate-600", "white", 4.5, "secondary text"], ["slate-600", "slate-50", 4.5, "secondary text on the page"],
  ["slate-500", "white", 4.5, "muted text on cards"], ["slate-500", "slate-50", 4.5, "muted text on the page"],
  ["white", "slate-900", 4.5, "primary button / active nav item"],
  ["red-800", "white", 4.5, "error text"], ["red-800", "red-50", 4.5, "error panel"], ["red-900", "red-50", 4.5, "high severity badge"],
  ["amber-900", "amber-50", 4.5, "medium severity badge"], ["sky-900", "sky-50", 4.5, "info panel"],
  ["emerald-900", "emerald-50", 4.5, "success panel"],
  ["slate-400", "white", 4.5, "placeholders and the faintest text (\"Not provided\")"],
  ["slate-400", "slate-50", 4.5, "faint text on the page"],
  ["slate-300", "white", 3.0, "form control and button borders (non-text contrast)"],
  ["slate-300", "slate-50", 3.0, "control borders on the page background"],
];

let failures = 0;
for (const [theme, palette] of [["light", { ...light, ...brand }], ["dark", { ...light, ...dark, ...brand }]]) {
  console.log(`\n${theme.toUpperCase()} theme`);
  const all = [...PAIRS, ["on-brand", "brand-900", 4.5, "sign-in panel text"], ["on-brand-muted", "brand-900", 4.5, "sign-in panel secondary text"]];
  for (const [fg, bg, min, use] of all) {
    const value = ratio(palette[fg], palette[bg]);
    const ok = value >= min;
    if (!ok) failures += 1;
    console.log(`${ok ? "ok  " : "FAIL"} ${value.toFixed(2).padStart(5)}:1 (needs ${min})  ${fg} on ${bg}  - ${use}`);
  }
}
console.log(failures ? `\n${failures} pair(s) below their threshold` : "\nAll listed pairs meet their thresholds");
process.exit(failures ? 1 : 0);
