/**
 * StatusBadge.test.tsx
 *
 * Tests for the getTierLabel and getTierTone helper functions exported from
 * StatusBadge. These are pure functions so no render is required.
 */

import { describe, it, expect } from 'vitest';
import { getTierLabel, getTierTone } from './StatusBadge';

// ---------- getTierLabel -----------------------------------------------------

describe('getTierLabel', () => {
  it('maps tier0 to "This Week"', () => {
    expect(getTierLabel('tier0')).toBe('This Week');
  });

  it('maps tier1 to "1–4 Weeks Absent"', () => {
    expect(getTierLabel('tier1')).toBe('1–4 Weeks Absent');
  });

  it('maps tier2 to "5–8 Weeks Absent"', () => {
    expect(getTierLabel('tier2')).toBe('5–8 Weeks Absent');
  });

  it('maps tier3 to "9–12 Weeks Absent"', () => {
    expect(getTierLabel('tier3')).toBe('9–12 Weeks Absent');
  });

  it('maps inactive to "Inactive (12+)"', () => {
    expect(getTierLabel('inactive')).toBe('Inactive (12+)');
  });

  it('maps null to "Unrated"', () => {
    expect(getTierLabel(null)).toBe('Unrated');
  });

  it('maps undefined to "Unrated"', () => {
    expect(getTierLabel(undefined)).toBe('Unrated');
  });

  it('maps an unknown string to "Unrated"', () => {
    expect(getTierLabel('something_else')).toBe('Unrated');
  });

  it('is case-insensitive (TIER0 → "This Week")', () => {
    expect(getTierLabel('TIER0')).toBe('This Week');
  });

  it('is case-insensitive (INACTIVE → "Inactive (12+)")', () => {
    expect(getTierLabel('INACTIVE')).toBe('Inactive (12+)');
  });
});

// ---------- getTierTone ------------------------------------------------------

describe('getTierTone', () => {
  it('maps tier0 to "active"', () => {
    expect(getTierTone('tier0')).toBe('active');
  });

  it('maps tier1 to "warning"', () => {
    expect(getTierTone('tier1')).toBe('warning');
  });

  it('maps tier2 to "danger"', () => {
    expect(getTierTone('tier2')).toBe('danger');
  });

  it('maps tier3 to "error"', () => {
    expect(getTierTone('tier3')).toBe('error');
  });

  it('maps null to "muted"', () => {
    expect(getTierTone(null)).toBe('muted');
  });

  it('maps undefined to "muted"', () => {
    expect(getTierTone(undefined)).toBe('muted');
  });

  it('maps inactive to "muted"', () => {
    expect(getTierTone('inactive')).toBe('muted');
  });

  it('maps an unknown string to "muted"', () => {
    expect(getTierTone('something_else')).toBe('muted');
  });
});
