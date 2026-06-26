/**
 * Tests for EventsPage.
 *
 * Covers:
 *   - Renders "Events" heading
 *   - "New Event" button visible for admin; absent for volunteer
 *   - No "Sync" or "CiviCRM" text in the rendered output
 *   - EventTypeBadge renders for each mocked event's event_type
 *   - Event titles are displayed
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';

// --- Mocks ------------------------------------------------------------------

// useAuthStore is called with a selector: useAuthStore((s) => s.isAdmin)
// The mock must apply the selector to the state object.
vi.mock('@/store/authStore', () => ({
  useAuthStore: vi.fn((selector: (s: { isAdmin: boolean }) => unknown) =>
    selector({ isAdmin: false }),
  ),
}));

vi.mock('@/services/events', () => ({
  listEvents: vi.fn(),
}));

// Mock EventFormDrawer to avoid deep dependency tree in tests
vi.mock('@/components/events/EventFormDrawer', () => ({
  EventFormDrawer: () => null,
}));

// Mock api (used for /events/active-event-id and set-active calls)
vi.mock('@/services/api', () => ({
  api: {
    get: vi.fn(() => Promise.resolve({ data: { active_event_id: null } })),
    post: vi.fn(() => Promise.resolve({ data: { message: 'ok' } })),
  },
}));

import { useAuthStore } from '@/store/authStore';
import { listEvents } from '@/services/events';
import { EventsPage } from './EventsPage';

// ---------- Helpers ----------------------------------------------------------

function mkClient() {
  return new QueryClient({ defaultOptions: { queries: { retry: false } } });
}

function renderPage() {
  const client = mkClient();
  const utils = render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <EventsPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return { ...utils, client };
}

const emptyResponse = { total: 0, page: 1, page_size: 20, items: [] };

const sampleEvents = [
  {
    id: 1,
    title: 'Sunday Morning Service',
    event_type: 'Sunday Celebration',
    session_time: '10AM',
    occurrence_date: '2026-06-21',
    location: 'Main Hall',
    is_active: false,
    created_at: '2026-06-01T00:00:00Z',
  },
  {
    id: 2,
    title: 'Powerhouse Youth Night',
    event_type: 'Powerhouse',
    session_time: '3PM',
    occurrence_date: '2026-06-20',
    location: null,
    is_active: false,
    created_at: '2026-06-01T00:00:00Z',
  },
];

// ---------- Tests ------------------------------------------------------------

beforeEach(() => {
  vi.clearAllMocks();
  // Default: non-admin, empty list
  (useAuthStore as ReturnType<typeof vi.fn>).mockImplementation(
    (selector: (s: { isAdmin: boolean }) => unknown) => selector({ isAdmin: false }),
  );
  (listEvents as ReturnType<typeof vi.fn>).mockResolvedValue(emptyResponse);
});

describe('EventsPage — header', () => {
  it('renders the "Events" heading', async () => {
    renderPage();
    expect(await screen.findByRole('heading', { name: /^events$/i })).toBeInTheDocument();
  });
});

describe('EventsPage — no legacy text', () => {
  it('does not render any "Sync" text', async () => {
    renderPage();
    // Wait for the page to settle (heading present)
    await screen.findByRole('heading', { name: /^events$/i });
    expect(screen.queryByText(/sync/i)).toBeNull();
  });

  it('does not render any "CiviCRM" text', async () => {
    renderPage();
    await screen.findByRole('heading', { name: /^events$/i });
    expect(screen.queryByText(/civicrm/i)).toBeNull();
  });
});

describe('EventsPage — admin-gated "New Event" button', () => {
  it('shows "New Event" button when user is admin', async () => {
    (useAuthStore as ReturnType<typeof vi.fn>).mockImplementation(
      (selector: (s: { isAdmin: boolean }) => unknown) => selector({ isAdmin: true }),
    );
    renderPage();
    expect(await screen.findByRole('button', { name: /new event/i })).toBeInTheDocument();
  });

  it('hides "New Event" button for non-admin volunteer', async () => {
    renderPage(); // default: isAdmin = false
    await screen.findByRole('heading', { name: /^events$/i });
    expect(screen.queryByRole('button', { name: /new event/i })).toBeNull();
  });
});

describe('EventsPage — event list with EventTypeBadge', () => {
  it('renders event titles from the mocked list', async () => {
    (listEvents as ReturnType<typeof vi.fn>).mockResolvedValue({
      total: 2,
      page: 1,
      page_size: 20,
      items: sampleEvents,
    });
    renderPage();
    expect(await screen.findByText('Sunday Morning Service')).toBeInTheDocument();
    expect(await screen.findByText('Powerhouse Youth Night')).toBeInTheDocument();
  });

  it('renders an EventTypeBadge for the Sunday Celebration event_type', async () => {
    (listEvents as ReturnType<typeof vi.fn>).mockResolvedValue({
      total: 2,
      page: 1,
      page_size: 20,
      items: sampleEvents,
    });
    renderPage();
    // EventTypeBadge renders a <span data-event-type="Sunday Celebration"> with label text
    await waitFor(() => {
      expect(screen.getByText('Sunday Celebration')).toBeInTheDocument();
    });
  });

  it('renders an EventTypeBadge for the Powerhouse event_type', async () => {
    (listEvents as ReturnType<typeof vi.fn>).mockResolvedValue({
      total: 2,
      page: 1,
      page_size: 20,
      items: sampleEvents,
    });
    renderPage();
    await waitFor(() => {
      expect(screen.getByText('Powerhouse')).toBeInTheDocument();
    });
  });

  it('renders one EventTypeBadge per event type in the mocked list', async () => {
    (listEvents as ReturnType<typeof vi.fn>).mockResolvedValue({
      total: 2,
      page: 1,
      page_size: 20,
      items: sampleEvents,
    });
    renderPage();
    await waitFor(() => {
      // Each EventTypeBadge has data-event-type attribute
      const badges = document.querySelectorAll('[data-event-type]');
      expect(badges.length).toBe(sampleEvents.length);
    });
  });
});

describe('EventsPage — empty state', () => {
  it('shows empty state message when no events returned', async () => {
    (listEvents as ReturnType<typeof vi.fn>).mockResolvedValue(emptyResponse);
    renderPage();
    expect(await screen.findByText(/no events yet/i)).toBeInTheDocument();
  });
});
