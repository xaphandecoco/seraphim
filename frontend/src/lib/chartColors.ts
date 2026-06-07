/**
 * Chart color palette — literal-HSL constants matching the bar-chart convention
 * already used in DashboardPage (fill="hsl(...)"). Tier semantic colors (green/
 * amber/red/gray) are not in the design-token set, so they live here as a single
 * source of truth rather than as CSS custom properties.
 *
 * Decision (T5, sprint plan): literal-HSL centralised here; runtime CSS-var
 * resolution for Recharts `fill` is unjustified for a cosmetic item.
 */

/** Recognition-tier fill colors keyed by tier label. */
export const TIER_COLORS: Record<string, string> = {
  '100':    'hsl(142 71% 45%)',   // green-500 equivalent
  '91-99':  'hsl(38 92% 50%)',    // amber-500 equivalent
  'below90':'hsl(0 84% 60%)',     // red-500 equivalent
  'unknown':'hsl(220 9% 62%)',    // gray-400 equivalent (#9ca3af)
};

/** Fallback fill when a tier key is not found in TIER_COLORS. */
export const TIER_COLOR_FALLBACK = 'hsl(220 9% 62%)';

/** Bar chart accent — attendance and resolved detections. */
export const BAR_COLOR_ACCENT = 'hsl(48 90% 62%)';

/** Bar chart muted — total detections. */
export const BAR_COLOR_MUTED = 'hsl(220 9% 70%)';
