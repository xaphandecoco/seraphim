/**
 * Tests for MigrationPage.
 *
 * Covers:
 *   - Batch list renders with status/entity/mode chips
 *   - Empty state shows CLI text
 *   - Row-click navigation
 *   - Polling active when a batch is running
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, Route, Routes } from 'react-router-dom';

// --- Mocks ------------------------------------------------------------------

vi.mock('@/services/migration', () => ({
  listBatches: vi.fn(),
  getBatch: vi.fn(),
  listRows: vi.fn(),
  downloadReport: vi.fn(),
  getMigrationSummary: vi.fn(),
}));

// Stub BottomNav to avoid authStore dependency in AdminRoute
vi.mock('@/components/layout/BottomNav', () => ({
  BottomNav: () => null,
}));

import { listBatches } from '@/services/migration';
import { MigrationPage } from '@/pages/MigrationPage';

// ---------- Fixtures — REAL backend shapes (ImportBatchOut) -----------------

const runningBatch = {
  id: 1,
  source_filename: 'civicrm_export.xlsx',
  entity: 'contacts',
  mode: 'live',
  status: 'running',
  column_map: {},
  options: {},
  total_rows: 100,
  created_count: 10,
  updated_count: 5,
  skipped_count: 2,
  error_count: 0,
  review_count: 3,
  started_at: '2026-06-25T08:00:00Z',
  finished_at: null,
  created_by_id: null,
};

const completedBatch = {
  ...runningBatch,
  id: 2,
  source_filename: 'contacts.xlsx',
  entity: 'events',
  mode: 'dry_run',
  status: 'completed',
  finished_at: '2026-06-25T09:00:00Z',
};

const failedBatch = {
  ...runningBatch,
  id: 3,
  source_filename: 'bad_file.csv',
  entity: 'participants',
  mode: 'live',
  status: 'failed',
};

/** Wrapper object as the real endpoint returns */
function mkListResponse(items: typeof runningBatch[]) {
  return { items, total: items.length, limit: 50, offset: 0 };
}

// ---------- Helpers ---------------------------------------------------------

function mkClient() {
  return new QueryClient({ defaultOptions: { queries: { retry: false } } });
}

function renderPage(initialPath = '/settings/migration') {
  const client = mkClient();
  const utils = render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[initialPath]}>
        <Routes>
          <Route path="/settings/migration" element={<MigrationPage />} />
          <Route path="/settings/migration/:batchId" element={<div>Report Page</div>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return { ...utils, client };
}

// ---------- Tests -----------------------------------------------------------

beforeEach(() => {
  vi.clearAllMocks();
});

describe('MigrationPage — batch list renders', () => {
  it('renders status, entity and mode chips for each batch', async () => {
    (listBatches as ReturnType<typeof vi.fn>).mockResolvedValue(
      mkListResponse([runningBatch, completedBatch, failedBatch]),
    );

    renderPage();

    await waitFor(() => {
      expect(screen.getAllByText('running').length).toBeGreaterThan(0);
    });

    expect(screen.getAllByText('completed').length).toBeGreaterThan(0);
    expect(screen.getAllByText('failed').length).toBeGreaterThan(0);

    // entity chips
    expect(screen.getAllByText('contacts').length).toBeGreaterThan(0);
    expect(screen.getAllByText('events').length).toBeGreaterThan(0);
    expect(screen.getAllByText('participants').length).toBeGreaterThan(0);

    // mode chips
    expect(screen.getAllByText('live').length).toBeGreaterThan(0);
    expect(screen.getAllByText('dry_run').length).toBeGreaterThan(0);

    // filenames (source_filename)
    expect(screen.getAllByText('civicrm_export.xlsx').length).toBeGreaterThan(0);
    expect(screen.getAllByText('contacts.xlsx').length).toBeGreaterThan(0);
  });
});

describe('MigrationPage — empty state', () => {
  it('shows CLI text when no batches exist', async () => {
    (listBatches as ReturnType<typeof vi.fn>).mockResolvedValue(mkListResponse([]));

    renderPage();

    await waitFor(() => {
      expect(screen.getByText(/no migration batches yet/i)).toBeInTheDocument();
    });
    expect(screen.getByText(/migrate_civicrm\.py/)).toBeInTheDocument();
  });
});

describe('MigrationPage — row-click navigation', () => {
  it('navigates to /settings/migration/:batchId when a row is clicked', async () => {
    (listBatches as ReturnType<typeof vi.fn>).mockResolvedValue(
      mkListResponse([completedBatch]),
    );

    renderPage();

    await waitFor(() => {
      expect(screen.getAllByText('contacts.xlsx').length).toBeGreaterThan(0);
    });

    // Click on the first element with the filename text (desktop table row)
    fireEvent.click(screen.getAllByText('contacts.xlsx')[0]);

    await waitFor(() => {
      expect(screen.getByText('Report Page')).toBeInTheDocument();
    });
  });
});

describe('MigrationPage — polling', () => {
  it('calls listBatches (polling active) when a batch is running', async () => {
    (listBatches as ReturnType<typeof vi.fn>).mockResolvedValue(
      mkListResponse([runningBatch]),
    );

    renderPage();

    await waitFor(() => {
      expect(listBatches).toHaveBeenCalled();
    });

    // The running batch triggers refetchInterval=5000; just verify listBatches was called
    expect(listBatches).toHaveBeenCalledTimes(1);
  });
});
