/**
 * Unit tests for the centralized chart color palette (T5).
 *
 * AC (sprint plan, story T5): TIER_COLORS (+ the `|| fallback`) centralized in
 * one module; decision recorded = literal-HSL constants (not raw hex, not CSS
 * vars). These tests pin that single-source-of-truth contract so the palette
 * cannot silently drift back to raw hex or diverge from the bar-chart convention.
 */
import { describe, it, expect } from 'vitest';
import {
  TIER_COLORS,
  TIER_COLOR_FALLBACK,
  BAR_COLOR_ACCENT,
  BAR_COLOR_MUTED,
} from './chartColors';

// "hsl(H S% L%)" — the literal-HSL convention the bars already use.
const HSL = /^hsl\(\d{1,3}\s+\d{1,3}%\s+\d{1,3}%\)$/;

describe('chartColors (T5)', () => {
  it('defines a color for every recognition tier the API can emit', () => {
    for (const tier of ['100', '91-99', 'below90', 'unknown']) {
      expect(TIER_COLORS[tier]).toBeDefined();
    }
  });

  it('expresses every tier color as a literal-HSL string (no raw hex, no CSS var)', () => {
    for (const value of Object.values(TIER_COLORS)) {
      expect(value).toMatch(HSL);
      expect(value).not.toMatch(/#/); // no hex
      expect(value).not.toMatch(/var\(/); // no CSS custom property
    }
  });

  it('exposes a fallback that is itself literal-HSL and matches the unknown tier', () => {
    expect(TIER_COLOR_FALLBACK).toMatch(HSL);
    // Defensive design: unknown-tier rows and unmapped keys render identically.
    expect(TIER_COLOR_FALLBACK).toBe(TIER_COLORS['unknown']);
  });

  it('exposes literal-HSL bar colors that match the existing chart convention', () => {
    expect(BAR_COLOR_ACCENT).toMatch(HSL);
    expect(BAR_COLOR_MUTED).toMatch(HSL);
    expect(BAR_COLOR_ACCENT).not.toBe(BAR_COLOR_MUTED);
  });
});
