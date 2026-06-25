/**
 * Tests for BottomNav — S04-F13 acceptance criteria.
 *
 * Covers:
 *   - Admin "More" button is visible for admin users
 *   - Admin "More" button is not rendered for non-admin users
 *   - Admin "More" sheet contains an "Event Series" link to /settings/event-series
 *   - The "Event Series" link renders a Lucide CalendarRange icon (via aria-hidden svg)
 *   - Main nav tabs are rendered (Tasks, Audit, Ranking, Events, Contacts)
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

vi.mock('@/store/authStore', () => ({
  useAuthStore: vi.fn((selector: (s: { isAdmin: boolean }) => unknown) =>
    selector({ isAdmin: true }),
  ),
}));

vi.mock('@/store/taskStore', () => ({
  useTaskStore: vi.fn((selector: (s: { pendingCount: number }) => unknown) =>
    selector({ pendingCount: 0 }),
  ),
}));

import { useAuthStore } from '@/store/authStore';
import { BottomNav } from './BottomNav';

function renderNav(pathname = '/') {
  return render(
    <MemoryRouter initialEntries={[pathname]}>
      <BottomNav />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  (useAuthStore as ReturnType<typeof vi.fn>).mockImplementation(
    (selector: (s: { isAdmin: boolean }) => unknown) =>
      selector({ isAdmin: true }),
  );
});

describe('BottomNav — main tabs', () => {
  it('renders the five main navigation tabs', () => {
    renderNav();
    expect(screen.getByRole('link', { name: /tasks/i })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /audit/i })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /ranking/i })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /events/i })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /contacts/i })).toBeInTheDocument();
  });
});

describe('BottomNav — admin More button', () => {
  it('renders the "More" admin button for admin users', () => {
    renderNav();
    expect(
      screen.getByRole('button', { name: /admin menu/i }),
    ).toBeInTheDocument();
  });

  it('does not render the "More" admin button for non-admin users', () => {
    (useAuthStore as ReturnType<typeof vi.fn>).mockImplementation(
      (selector: (s: { isAdmin: boolean }) => unknown) =>
        selector({ isAdmin: false }),
    );
    renderNav();
    expect(
      screen.queryByRole('button', { name: /admin menu/i }),
    ).toBeNull();
  });
});

describe('BottomNav — admin More sheet Event Series link', () => {
  it('opens the admin sheet when More is clicked', () => {
    renderNav();
    const moreBtn = screen.getByRole('button', { name: /admin menu/i });
    fireEvent.click(moreBtn);
    // Sheet should be visible — find the "Event Series" link
    const link = screen.getByRole('link', { name: /event series/i });
    expect(link).toBeInTheDocument();
  });

  it('Event Series link points to /settings/event-series', () => {
    renderNav();
    fireEvent.click(screen.getByRole('button', { name: /admin menu/i }));
    const link = screen.getByRole('link', { name: /event series/i });
    expect(link).toHaveAttribute('href', '/settings/event-series');
  });

  it('Event Series link contains a Lucide icon (svg element)', () => {
    renderNav();
    fireEvent.click(screen.getByRole('button', { name: /admin menu/i }));
    const link = screen.getByRole('link', { name: /event series/i });
    // Lucide icons render as <svg aria-hidden="true"> inside the link
    const svg = link.querySelector('svg[aria-hidden="true"]');
    expect(svg).not.toBeNull();
  });

  it('admin sheet is hidden before More is clicked', () => {
    renderNav();
    // Before opening the sheet, the Event Series link should not be present
    expect(screen.queryByRole('link', { name: /event series/i })).toBeNull();
  });

  it('admin sheet closes when the close button is clicked', () => {
    renderNav();
    fireEvent.click(screen.getByRole('button', { name: /admin menu/i }));
    expect(screen.getByRole('link', { name: /event series/i })).toBeInTheDocument();
    const closeBtn = screen.getByRole('button', { name: /close/i });
    fireEvent.click(closeBtn);
    expect(screen.queryByRole('link', { name: /event series/i })).toBeNull();
  });
});
