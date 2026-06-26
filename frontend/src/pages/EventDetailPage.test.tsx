/**
 * Tests for EventDetailPage.
 *
 * Covers:
 *   - Renders event title
 *   - Renders EventTypeBadge and SessionTimeBadge
 *   - Renders scalar info card fields
 *   - Renders ParticipantGrid
 *   - Admin edit and delete buttons visible for admin
 *   - Edit/delete buttons hidden for non-admin
 *   - Delete uses ConfirmDialog (not window.confirm) → calls deleteEvent
 *   - 404 / error renders ErrorState with "Event not found"
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, Route, Routes } from 'react-router-dom';

// --- Mocks ------------------------------------------------------------------

vi.mock('@/store/authStore', () => ({
  useAuthStore: vi.fn((selector: (s: { isAdmin: boolean; user: null | { role: string } }) => unknown) =>
    selector({ isAdmin: true, user: { role: 'admin' } }),
  ),
}));

vi.mock('@/services/events', () => ({
  getEvent: vi.fn(),
  deleteEvent: vi.fn(),
}));

// Stub sub-components to keep tests fast
vi.mock('@/components/events/EventFormDrawer', () => ({
  EventFormDrawer: () => null,
}));

vi.mock('@/components/events/ParticipantGrid', () => ({
  ParticipantGrid: () => <div data-testid="participant-grid" />,
}));

import { useAuthStore } from '@/store/authStore';
import { getEvent, deleteEvent } from '@/services/events';
import { EventDetailPage } from './EventDetailPage';

// ---------- Helpers ----------------------------------------------------------

function mkClient() {
  return new QueryClient({ defaultOptions: { queries: { retry: false } } });
}

function renderPage(eventId = '5') {
  const client = mkClient();
  const utils = render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[`/events/${eventId}`]}>
        <Routes>
          <Route path="/events/:id" element={<EventDetailPage />} />
          <Route path="/events" element={<div>Events list</div>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return { ...utils, client };
}

const sampleEvent = {
  id: 5,
  title: 'Sunday Morning Service',
  event_type: 'Sunday Celebration',
  session_time: '10AM',
  occurrence_date: '2026-06-21',
  start_at: '2026-06-21T09:00:00Z',
  end_at: '2026-06-21T11:00:00Z',
  location: 'Main Hall',
  is_active: true,
  external_id: 42,
  created_at: '2026-06-01T00:00:00Z',
  participant_counts: { unique_count: 30, total_count: 32 },
};

// ---------- Tests ------------------------------------------------------------

beforeEach(() => {
  vi.clearAllMocks();
  // Default: admin
  (useAuthStore as ReturnType<typeof vi.fn>).mockImplementation(
    (selector: (s: { isAdmin: boolean; user: null | { role: string } }) => unknown) =>
      selector({ isAdmin: true, user: { role: 'admin' } }),
  );
  (getEvent as ReturnType<typeof vi.fn>).mockResolvedValue(sampleEvent);
});

describe('EventDetailPage — event title', () => {
  it('renders the event title in the page heading (h1)', async () => {
    renderPage();
    // Title appears in both h1 (nav header) and h2 (event card); verify h1 exists
    const h1 = await screen.findByRole('heading', { level: 1, name: /sunday morning service/i });
    expect(h1).toBeInTheDocument();
  });
});

describe('EventDetailPage — badges', () => {
  it('renders EventTypeBadge for the event type', async () => {
    renderPage();
    await screen.findByRole('heading', { level: 1, name: /sunday morning service/i });
    // EventTypeBadge renders text "Sunday Celebration" for "Sunday Celebration"
    const badge = document.querySelector('[data-event-type="Sunday Celebration"]');
    expect(badge).not.toBeNull();
  });

  it('renders SessionTimeBadge for the session time', async () => {
    renderPage();
    await screen.findByRole('heading', { level: 1, name: /sunday morning service/i });
    // SessionTimeBadge renders "10AM" for "10AM"
    const badge = document.querySelector('[data-session-time="10AM"]');
    expect(badge).not.toBeNull();
    expect(badge?.textContent).toBe('10AM');
  });
});

describe('EventDetailPage — info card', () => {
  it('renders location field', async () => {
    renderPage();
    await screen.findByRole('heading', { level: 1, name: /sunday morning service/i });
    expect(screen.getByText('Main Hall')).toBeInTheDocument();
  });

  it('renders participant counts', async () => {
    renderPage();
    await screen.findByRole('heading', { level: 1, name: /sunday morning service/i });
    expect(screen.getByText(/30 unique/)).toBeInTheDocument();
  });

  it('renders external ID', async () => {
    renderPage();
    await screen.findByRole('heading', { level: 1, name: /sunday morning service/i });
    expect(screen.getByText('42')).toBeInTheDocument();
  });
});

describe('EventDetailPage — ParticipantGrid', () => {
  it('renders the ParticipantGrid section', async () => {
    renderPage();
    expect(await screen.findByTestId('participant-grid')).toBeInTheDocument();
  });
});

describe('EventDetailPage — admin edit/delete actions', () => {
  it('shows Edit and Delete buttons for admin', async () => {
    renderPage();
    await screen.findByRole('heading', { level: 1, name: /sunday morning service/i });
    expect(screen.getByRole('button', { name: /edit event/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /delete event/i })).toBeInTheDocument();
  });

  it('hides Edit and Delete buttons for non-admin', async () => {
    (useAuthStore as ReturnType<typeof vi.fn>).mockImplementation(
      (selector: (s: { isAdmin: boolean; user: null | { role: string } }) => unknown) =>
        selector({ isAdmin: false, user: { role: 'volunteer' } }),
    );
    renderPage();
    await screen.findByRole('heading', { level: 1, name: /sunday morning service/i });
    expect(screen.queryByRole('button', { name: /edit event/i })).toBeNull();
    expect(screen.queryByRole('button', { name: /delete event/i })).toBeNull();
  });
});

describe('EventDetailPage — delete via ConfirmDialog', () => {
  it('shows ConfirmDialog on delete click and calls deleteEvent on confirm', async () => {
    (deleteEvent as ReturnType<typeof vi.fn>).mockResolvedValue({ id: 5, is_active: false });
    renderPage();
    await screen.findByRole('heading', { level: 1, name: /sunday morning service/i });
    const deleteBtn = screen.getByRole('button', { name: /delete event/i });
    fireEvent.click(deleteBtn);
    // ConfirmDialog should appear — find the confirm "Delete" button
    const confirmBtn = await screen.findByRole('button', { name: /^delete$/i });
    fireEvent.click(confirmBtn);
    await waitFor(() => {
      expect(deleteEvent).toHaveBeenCalledWith(5);
    });
  });

  it('cancels delete when Cancel is clicked in ConfirmDialog', async () => {
    renderPage();
    await screen.findByRole('heading', { level: 1, name: /sunday morning service/i });
    const deleteBtn = screen.getByRole('button', { name: /delete event/i });
    fireEvent.click(deleteBtn);
    const cancelBtn = await screen.findByRole('button', { name: /cancel/i });
    fireEvent.click(cancelBtn);
    expect(deleteEvent).not.toHaveBeenCalled();
  });
});

describe('EventDetailPage — error / 404 state', () => {
  it('renders ErrorState when getEvent rejects', async () => {
    (getEvent as ReturnType<typeof vi.fn>).mockRejectedValue(new Error('Not found'));
    renderPage();
    expect(await screen.findByText(/event not found/i)).toBeInTheDocument();
  });
});
