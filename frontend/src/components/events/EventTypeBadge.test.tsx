/**
 * EventTypeBadge.test.tsx
 *
 * Asserts label and Tailwind class for all 6 event types per spec §5.2.
 */

import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { EventTypeBadge } from './EventTypeBadge';

// ---------- Test cases -------------------------------------------------------

const CASES = [
  {
    eventType: 'Sunday Celebration',
    expectedLabel: 'Sunday Celebration',
    expectedClass: 'bg-primary/20',
    expectedTextClass: 'text-primary',
  },
  {
    eventType: 'Prayer Meeting',
    expectedLabel: 'Prayer Meeting',
    expectedClass: 'bg-blue-500/20',
    expectedTextClass: undefined,
  },
  {
    eventType: 'Powerhouse',
    expectedLabel: 'Powerhouse',
    expectedClass: 'bg-purple-500/20',
    expectedTextClass: undefined,
  },
  {
    eventType: 'Community Meeting',
    expectedLabel: 'Community Meeting',
    expectedClass: 'bg-green-500/20',
    expectedTextClass: undefined,
  },
  {
    eventType: 'Conference',
    expectedLabel: 'Conference',
    expectedClass: 'bg-muted',
    expectedTextClass: 'text-muted-foreground',
  },
  {
    eventType: 'Event',
    expectedLabel: 'Event',
    expectedClass: 'bg-muted',
    expectedTextClass: 'text-muted-foreground',
  },
] as const;

// ---------- Tests ------------------------------------------------------------

describe('EventTypeBadge', () => {
  it.each(CASES)(
    'renders label "$expectedLabel" with class "$expectedClass" for type "$eventType"',
    ({ eventType, expectedLabel, expectedClass, expectedTextClass }) => {
      render(<EventTypeBadge eventType={eventType} />);

      const badge = screen.getByText(expectedLabel);
      expect(badge).toBeTruthy();

      // Check the element has the expected Tailwind class
      expect(badge.className).toContain(expectedClass);

      if (expectedTextClass) {
        expect(badge.className).toContain(expectedTextClass);
      }
    },
  );

  it('renders the raw event type as label for unknown types', () => {
    const unknownType = 'unknown_type_xyz';
    render(<EventTypeBadge eventType={unknownType} />);

    const badge = screen.getByText(unknownType);
    expect(badge).toBeTruthy();
    // Falls back to muted styling
    expect(badge.className).toContain('bg-muted');
    expect(badge.className).toContain('text-muted-foreground');
  });
});
