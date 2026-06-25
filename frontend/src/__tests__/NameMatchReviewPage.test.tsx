/**
 * Tests for NameMatchReviewPage.
 *
 * Covers:
 *   - Items render (raw_name visible)
 *   - Empty state renders when items=[]
 *   - Filter bar exists (status select + source input + Search button)
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';

// --- Mocks ------------------------------------------------------------------

vi.mock('@/services/nameMatch', () => ({
  getReviewQueue: vi.fn(),
  skipQueueItem: vi.fn(),
  unmatchQueueItem: vi.fn(),
}));

// Stub ResolveMatchModal to avoid complexity
vi.mock('@/components/name-match/ResolveMatchModal', () => ({
  ResolveMatchModal: () => null,
}));

import { getReviewQueue } from '@/services/nameMatch';
import { NameMatchReviewPage } from '@/pages/NameMatchReviewPage';

// ---------- Helpers ----------------------------------------------------------

function mkClient() {
  return new QueryClient({ defaultOptions: { queries: { retry: false } } });
}

function renderPage() {
  const client = mkClient();
  const utils = render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <NameMatchReviewPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return { ...utils, client };
}

const emptyResponse = { total: 0, page: 1, page_size: 25, items: [] };

const sampleItems = [
  {
    id: 1,
    raw_name: 'Juan dela Cruz',
    normalized_name: 'juan dela cruz',
    source: 'manual',
    status: 'pending' as const,
    event_id: null,
    event_title: null,
    community_report_id: null,
    candidates: [],
    resolved_contact_id: null,
    resolved_contact_name: null,
    resolved_by_id: null,
    resolve_note: null,
    created_at: '2026-06-25T00:00:00Z',
    updated_at: '2026-06-25T00:00:00Z',
  },
  {
    id: 2,
    raw_name: 'Maria Santos',
    normalized_name: 'maria santos',
    source: 'community_report',
    status: 'pending' as const,
    event_id: 5,
    event_title: 'Sunday Service',
    community_report_id: null,
    candidates: [
      { contact_id: 10, display_name: 'Maria Santos', score: 0.95, method: 'fuzzy_forward' as const },
    ],
    resolved_contact_id: null,
    resolved_contact_name: null,
    resolved_by_id: null,
    resolve_note: null,
    created_at: '2026-06-25T00:00:00Z',
    updated_at: '2026-06-25T00:00:00Z',
  },
];

// ---------- Tests ------------------------------------------------------------

beforeEach(() => {
  vi.clearAllMocks();
  (getReviewQueue as ReturnType<typeof vi.fn>).mockResolvedValue(emptyResponse);
});

describe('NameMatchReviewPage — filter bar', () => {
  it('renders the status dropdown', async () => {
    renderPage();
    expect(await screen.findByRole('combobox', { name: /status/i })).toBeInTheDocument();
  });

  it('renders the source input', async () => {
    renderPage();
    expect(await screen.findByPlaceholderText(/filter by source/i)).toBeInTheDocument();
  });

  it('renders the Search button', async () => {
    renderPage();
    expect(await screen.findByRole('button', { name: /search/i })).toBeInTheDocument();
  });
});

describe('NameMatchReviewPage — empty state', () => {
  it('shows empty state when items=[]', async () => {
    (getReviewQueue as ReturnType<typeof vi.fn>).mockResolvedValue(emptyResponse);
    renderPage();
    expect(await screen.findByText(/no items in review queue/i)).toBeInTheDocument();
  });
});

describe('NameMatchReviewPage — item list', () => {
  it('renders raw_name for each queue item', async () => {
    (getReviewQueue as ReturnType<typeof vi.fn>).mockResolvedValue({
      total: 2,
      page: 1,
      page_size: 25,
      items: sampleItems,
    });
    renderPage();
    expect(await screen.findByText('Juan dela Cruz')).toBeInTheDocument();
    expect(await screen.findByText('Maria Santos')).toBeInTheDocument();
  });

  it('renders Resolve button for each item', async () => {
    (getReviewQueue as ReturnType<typeof vi.fn>).mockResolvedValue({
      total: 1,
      page: 1,
      page_size: 25,
      items: [sampleItems[0]],
    });
    renderPage();
    expect(await screen.findByRole('button', { name: /resolve/i })).toBeInTheDocument();
  });

  it('renders event_title chip when present', async () => {
    (getReviewQueue as ReturnType<typeof vi.fn>).mockResolvedValue({
      total: 1,
      page: 1,
      page_size: 25,
      items: [sampleItems[1]],
    });
    renderPage();
    expect(await screen.findByText('Sunday Service')).toBeInTheDocument();
  });
});
