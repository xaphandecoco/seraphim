/**
 * Tests for BottomNav — S04-F13 + S10 acceptance criteria.
 *
 * Covers:
 *   - Admin "More" button is visible for admin users
 *   - Admin "More" button is not rendered for non-admin users
 *   - Admin "More" sheet contains an "Event Series" link to /settings/event-series
 *   - The "Event Series" link renders a Lucide icon (via aria-hidden svg)
 *   - Main nav tabs are rendered (Tasks, Audit, Ranking, Events, Contacts)
 *   - S10: Import link is visible for volunteer (non-admin) users
 *   - S10: Import link is hidden for viewer users (isVolunteer=false)
 *   - S10: Import link appears in Admin "More" sheet for admin users
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

type MockStore = { isAdmin: boolean; isVolunteer: boolean };

vi.mock('@/store/authStore', () => ({
  useAuthStore: vi.fn((selector: (s: MockStore) => unknown) =>
    selector({ isAdmin: true, isVolunteer: true }),
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
  // Default: admin user (isAdmin + isVolunteer)
  (useAuthStore as ReturnType<typeof vi.fn>).mockImplementation(
    (selector: (s: MockStore) => unknown) =>
      selector({ isAdmin: true, isVolunteer: true }),
  );
});

// ─── Main tabs ───────────────────────────────────────────────────────────────

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

// ─── Admin More button ────────────────────────────────────────────────────────

describe('BottomNav — admin More button', () => {
  it('renders the "More" admin button for admin users', () => {
    renderNav();
    expect(
      screen.getByRole('button', { name: /admin menu/i }),
    ).toBeInTheDocument();
  });

  it('does not render the "More" admin button for non-admin volunteer users', () => {
    (useAuthStore as ReturnType<typeof vi.fn>).mockImplementation(
      (selector: (s: MockStore) => unknown) =>
        selector({ isAdmin: false, isVolunteer: true }),
    );
    renderNav();
    expect(
      screen.queryByRole('button', { name: /admin menu/i }),
    ).toBeNull();
  });

  it('does not render the "More" admin button for viewer users', () => {
    (useAuthStore as ReturnType<typeof vi.fn>).mockImplementation(
      (selector: (s: MockStore) => unknown) =>
        selector({ isAdmin: false, isVolunteer: false }),
    );
    renderNav();
    expect(
      screen.queryByRole('button', { name: /admin menu/i }),
    ).toBeNull();
  });
});

// ─── Admin More sheet — Event Series ─────────────────────────────────────────

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

// ─── S10: Import nav entry ────────────────────────────────────────────────────

describe('BottomNav — S10 Import nav entry', () => {
  it('shows Import link directly in nav bar for volunteer (non-admin) users', () => {
    (useAuthStore as ReturnType<typeof vi.fn>).mockImplementation(
      (selector: (s: MockStore) => unknown) =>
        selector({ isAdmin: false, isVolunteer: true }),
    );
    renderNav();
    expect(screen.getByRole('link', { name: /import/i })).toBeInTheDocument();
  });

  it('Import link points to /imports for volunteer users', () => {
    (useAuthStore as ReturnType<typeof vi.fn>).mockImplementation(
      (selector: (s: MockStore) => unknown) =>
        selector({ isAdmin: false, isVolunteer: true }),
    );
    renderNav();
    const link = screen.getByRole('link', { name: /import/i });
    expect(link).toHaveAttribute('href', '/imports');
  });

  it('does NOT show Import link directly in nav bar for viewer (isVolunteer=false)', () => {
    (useAuthStore as ReturnType<typeof vi.fn>).mockImplementation(
      (selector: (s: MockStore) => unknown) =>
        selector({ isAdmin: false, isVolunteer: false }),
    );
    renderNav();
    // No Import link and no More button
    expect(screen.queryByRole('link', { name: /^import$/i })).toBeNull();
  });

  it('Import entry appears inside admin More sheet for admin users', () => {
    // Default mock: admin + volunteer
    renderNav();
    fireEvent.click(screen.getByRole('button', { name: /admin menu/i }));
    const importLink = screen.getByRole('link', { name: /^import$/i });
    expect(importLink).toBeInTheDocument();
    expect(importLink).toHaveAttribute('href', '/imports');
  });

  it('Import link in admin sheet is NOT rendered outside the sheet (sheet is closed)', () => {
    // admin — Import is inside "More" sheet, not directly in nav
    renderNav();
    // Sheet is closed: Import link should not be in the document
    expect(screen.queryByRole('link', { name: /^import$/i })).toBeNull();
  });
});
