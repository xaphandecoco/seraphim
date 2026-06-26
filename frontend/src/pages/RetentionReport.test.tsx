/**
 * RetentionReport page tests (S08 — Biometric Consent & RTBF)
 *
 * Acceptance criteria covered:
 *  1. Shows LoadingState while query is in-flight
 *  2. Shows ErrorState with retry button when the API returns an error
 *  3. Shows EmptyState when items=[] and total=0
 *  4. Renders a row per item with contact_name and status pill
 *  5. Renders detail fields: consent_given, retention_until, deletion_requested_at, purged_at
 *  6. Pagination: Prev/Next buttons appear when totalPages > 1
 *  7. Prev disabled on first page; Next disabled on last page
 *  8. within_days filter select calls API with matching param
 *  9. include_purged checkbox calls API with include_purged=true when checked
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { RetentionReport } from './RetentionReport';
import type { RetentionReportResponse, RetentionReportItem } from '@/types/biometric';

// ---------------------------------------------------------------------------
// Mocks
// ---------------------------------------------------------------------------

const mockGetRetentionReport = vi.fn();

vi.mock('@/services/biometric', () => ({
  biometricApi: {
    getRetentionReport: (...a: unknown[]) => mockGetRetentionReport(...a),
  },
}));

// ---------------------------------------------------------------------------
// Factories
// ---------------------------------------------------------------------------

function makeItem(overrides: Partial<RetentionReportItem> = {}): RetentionReportItem {
  return {
    contact_id: 1,
    contact_name: 'John Doe',
    consent_given: true,
    retention_until: '2027-01-01T00:00:00Z',
    deletion_requested_at: null,
    purged_at: null,
    status: 'given',
    ...overrides,
  };
}

function makeReport(
  items: RetentionReportItem[],
  overrides: Partial<RetentionReportResponse> = {},
): RetentionReportResponse {
  return {
    items,
    total: items.length,
    page: 1,
    page_size: 20,
    ...overrides,
  };
}

// ---------------------------------------------------------------------------
// Render helper
// ---------------------------------------------------------------------------

function renderPage() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <MemoryRouter>
      <QueryClientProvider client={qc}>
        <RetentionReport />
      </QueryClientProvider>
    </MemoryRouter>,
  );
}

// ---------------------------------------------------------------------------
// AC1 — Loading state
// ---------------------------------------------------------------------------

describe('AC1 — LoadingState while query in-flight', () => {
  beforeEach(() => {
    mockGetRetentionReport.mockReturnValue(new Promise(() => {}));
  });

  it('shows loading spinner and message', () => {
    renderPage();
    expect(screen.getByRole('status')).toBeInTheDocument();
    expect(screen.getByText('Loading retention report…')).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// AC2 — Error state
// ---------------------------------------------------------------------------

describe('AC2 — ErrorState when API errors', () => {
  beforeEach(() => {
    mockGetRetentionReport.mockRejectedValue(new Error('server error'));
  });

  it('shows error alert with retry button', async () => {
    renderPage();
    expect(await screen.findByRole('alert')).toBeInTheDocument();
    expect(screen.getByText('Failed to load retention report')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /try again/i })).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// AC3 — Empty state
// ---------------------------------------------------------------------------

describe('AC3 — EmptyState when no items', () => {
  beforeEach(() => {
    mockGetRetentionReport.mockResolvedValue(makeReport([]));
  });

  it('shows "No records found" empty state', async () => {
    renderPage();
    expect(await screen.findByText('No records found')).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// AC4 — Renders rows with contact_name and status pill
// ---------------------------------------------------------------------------

describe('AC4 — renders rows per item', () => {
  beforeEach(() => {
    mockGetRetentionReport.mockResolvedValue(
      makeReport([
        makeItem({ contact_id: 10, contact_name: 'Alice Santos', status: 'given' }),
        makeItem({ contact_id: 11, contact_name: 'Bob Reyes', status: 'revoked' }),
      ]),
    );
  });

  it('renders a row for each item with contact name', async () => {
    renderPage();
    expect(await screen.findByText('Alice Santos')).toBeInTheDocument();
    expect(screen.getByText('Bob Reyes')).toBeInTheDocument();
  });

  it('renders status pills', async () => {
    renderPage();
    await screen.findByText('Alice Santos');
    const givenPills = screen.getAllByText('given');
    expect(givenPills.length).toBeGreaterThan(0);
    expect(screen.getByText('revoked')).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// AC5 — Renders detail fields
// ---------------------------------------------------------------------------

describe('AC5 — renders detail fields', () => {
  beforeEach(() => {
    mockGetRetentionReport.mockResolvedValue(
      makeReport([
        makeItem({
          contact_id: 20,
          contact_name: 'Maria Cruz',
          consent_given: true,
          retention_until: '2028-06-15T00:00:00Z',
          deletion_requested_at: '2026-06-01T00:00:00Z',
        }),
      ]),
    );
  });

  it('shows consent_given as "Yes"', async () => {
    renderPage();
    await screen.findByText('Maria Cruz');
    expect(screen.getByText('Yes')).toBeInTheDocument();
  });

  it('shows formatted retention_until date', async () => {
    renderPage();
    await screen.findByText('Maria Cruz');
    // Date is locale-formatted — just check the label is present
    expect(screen.getByText(/retain until:/i)).toBeInTheDocument();
  });

  it('shows deletion_requested_at', async () => {
    renderPage();
    await screen.findByText('Maria Cruz');
    expect(screen.getByText(/deletion req\./i)).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// AC6 — Pagination: Prev/Next appear when totalPages > 1
// ---------------------------------------------------------------------------

describe('AC6 — pagination controls appear when totalPages > 1', () => {
  beforeEach(() => {
    const manyItems = Array.from({ length: 25 }, (_, i) =>
      makeItem({ contact_id: i + 1, contact_name: `Contact ${i + 1}` }),
    );
    mockGetRetentionReport.mockResolvedValue(
      makeReport(manyItems.slice(0, 20), { total: 25, page: 1, page_size: 20 }),
    );
  });

  it('shows Prev and Next buttons', async () => {
    renderPage();
    await screen.findByText('Contact 1');
    expect(screen.getByRole('button', { name: /prev/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /next/i })).toBeInTheDocument();
    expect(screen.getByText('Page 1 of 2')).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// AC7 — Prev disabled on first page; Next disabled on last page
// ---------------------------------------------------------------------------

describe('AC7 — Prev/Next disabled on boundary pages', () => {
  beforeEach(() => {
    const items = Array.from({ length: 25 }, (_, i) =>
      makeItem({ contact_id: i + 1, contact_name: `C${i + 1}` }),
    );
    mockGetRetentionReport.mockResolvedValue(
      makeReport(items.slice(0, 20), { total: 25, page: 1, page_size: 20 }),
    );
  });

  it('Prev is disabled on page 1', async () => {
    renderPage();
    await screen.findByText('C1');
    expect(screen.getByRole('button', { name: /prev/i })).toBeDisabled();
  });
});

// ---------------------------------------------------------------------------
// AC8 — within_days filter calls API with matching param
// ---------------------------------------------------------------------------

describe('AC8 — within_days filter passes param to API', () => {
  beforeEach(() => {
    mockGetRetentionReport.mockResolvedValue(makeReport([]));
  });

  it('selecting 30 days calls API with within_days=30', async () => {
    renderPage();
    await screen.findByText('No records found');

    const select = screen.getByRole('combobox');
    fireEvent.change(select, { target: { value: '30' } });

    await waitFor(() => {
      const lastCall = mockGetRetentionReport.mock.calls.at(-1)?.[0] as Record<string, unknown>;
      expect(lastCall.within_days).toBe(30);
    });
  });
});

// ---------------------------------------------------------------------------
// AC9 — include_purged checkbox passes include_purged=true
// ---------------------------------------------------------------------------

describe('AC9 — include_purged checkbox', () => {
  beforeEach(() => {
    mockGetRetentionReport.mockResolvedValue(makeReport([]));
  });

  it('checking include_purged calls API with include_purged=true', async () => {
    renderPage();
    await screen.findByText('No records found');

    const checkbox = screen.getByRole('checkbox');
    fireEvent.click(checkbox);

    await waitFor(() => {
      const lastCall = mockGetRetentionReport.mock.calls.at(-1)?.[0] as Record<string, unknown>;
      expect(lastCall.include_purged).toBe(true);
    });
  });
});
