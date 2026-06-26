/**
 * Tests for S10 Import Wizard — ImportWizardPage, ImportsListPage, ImportReportPage.
 *
 * Covers:
 *   - duplicate-target validation blocks Next in MapStep
 *   - viewer cannot reach wizard (VolunteerRoute redirect)
 *   - imports list renders runs + empty state
 *   - report page loads batch detail + rows, filters by outcome, downloads CSV
 *   - 413 upload error surfaces server detail via toast
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, Route, Routes } from 'react-router-dom';

// ─── Service mocks ────────────────────────────────────────────────────────────

vi.mock('@/services/imports', () => ({
  uploadImport: vi.fn(),
  getColumns: vi.fn(),
  runPreview: vi.fn(),
  runImport: vi.fn(),
  listImportBatches: vi.fn(),
  getImportBatch: vi.fn(),
  listImportRows: vi.fn(),
  listPreviewRows: vi.fn(),
  downloadImportReport: vi.fn(),
  listPresets: vi.fn(),
  savePreset: vi.fn(),
  updatePreset: vi.fn(),
  deletePreset: vi.fn(),
}));

// ─── Layout mocks (avoid BottomNav / authStore side-effects) ─────────────────

vi.mock('@/components/layout/BottomNav', () => ({
  BottomNav: () => null,
}));

// ─── Sonner mock (capture toast calls) ───────────────────────────────────────

vi.mock('sonner', () => ({
  toast: {
    success: vi.fn(),
    error: vi.fn(),
  },
  Toaster: () => null,
}));

// ─── authStore mock (volunteer by default) ────────────────────────────────────

vi.mock('@/store/authStore', () => ({
  useAuthStore: vi.fn((selector: (s: { token: string | null; isVolunteer: boolean; isAdmin: boolean; authReady: boolean }) => unknown) =>
    selector({ token: 'tok', isVolunteer: true, isAdmin: false, authReady: true }),
  ),
}));

import { useAuthStore } from '@/store/authStore';
import {
  listImportBatches,
  getImportBatch,
  listImportRows,
  getColumns,
  downloadImportReport,
} from '@/services/imports';
import { toast } from 'sonner';
import { ImportsListPage } from '@/pages/ImportsListPage';
import { ImportReportPage } from '@/pages/ImportReportPage';
import { ImportWizardPage } from '@/pages/ImportWizardPage';
import { VolunteerRoute } from '@/components/layout/VolunteerRoute';
import { MapStep } from '@/components/imports/MapStep';

// ─── Fixtures ─────────────────────────────────────────────────────────────────

const completedBatch = {
  id: 42,
  source_filename: 'newcomers.csv',
  entity: 'contact',
  mode: 'wizard_run',
  status: 'completed',
  column_map: {},
  options: {},
  total_rows: 50,
  created_count: 40,
  updated_count: 5,
  skipped_count: 5,
  error_count: 0,
  review_count: 0,
  started_at: '2026-06-26T10:00:00Z',
  finished_at: '2026-06-26T10:01:00Z',
  created_by_id: 1,
  pending_review_count: 0,
};

const rowResult = {
  id: 1,
  batch_id: 42,
  row_number: 1,
  external_id: 'EXT-001',
  outcome: 'created' as const,
  entity_id: 101,
  message: null,
  created_at: '2026-06-26T10:00:01Z',
};

function mkRowListResponse(items: typeof rowResult[]) {
  return { items, total: items.length, limit: 25, offset: 0 };
}

// ─── Helpers ─────────────────────────────────────────────────────────────────

function mkClient() {
  return new QueryClient({ defaultOptions: { queries: { retry: false } } });
}

type MockStoreState = { token: string | null; isVolunteer: boolean; isAdmin: boolean; authReady: boolean };

function setAuthState(state: Partial<MockStoreState>) {
  const defaults: MockStoreState = { token: 'tok', isVolunteer: true, isAdmin: false, authReady: true };
  const merged = { ...defaults, ...state };
  (useAuthStore as ReturnType<typeof vi.fn>).mockImplementation(
    (selector: (s: MockStoreState) => unknown) => selector(merged),
  );
}

function renderListPage() {
  const client = mkClient();
  const utils = render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={['/imports']}>
        <Routes>
          <Route path="/imports" element={<ImportsListPage />} />
          <Route path="/imports/:batchId" element={<div>Report</div>} />
          <Route path="/imports/new" element={<div>Wizard</div>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return { ...utils, client };
}

function renderReportPage(batchId = 42) {
  const client = mkClient();
  const utils = render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[`/imports/${batchId}`]}>
        <Routes>
          <Route path="/imports/:batchId" element={<ImportReportPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return { ...utils, client };
}

function renderVolunteerRoute(isVolunteer: boolean, isAdmin: boolean) {
  setAuthState({ isVolunteer, isAdmin, token: isVolunteer ? 'tok' : 'tok' });
  const client = mkClient();
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={['/imports/new']}>
        <Routes>
          <Route
            path="/imports/new"
            element={
              <VolunteerRoute>
                <div>Wizard content</div>
              </VolunteerRoute>
            }
          />
          <Route path="/" element={<div>Home</div>} />
          <Route path="/login" element={<div>Login</div>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

// ─── Tests ───────────────────────────────────────────────────────────────────

beforeEach(() => {
  vi.clearAllMocks();
  setAuthState({ isVolunteer: true, isAdmin: false });
});

// ── VolunteerRoute guard ──────────────────────────────────────────────────────

describe('VolunteerRoute — role guard', () => {
  it('renders children for a volunteer user', () => {
    renderVolunteerRoute(true, false);
    expect(screen.getByText('Wizard content')).toBeInTheDocument();
  });

  it('redirects viewer (isVolunteer=false) to /', () => {
    renderVolunteerRoute(false, false);
    expect(screen.getByText('Home')).toBeInTheDocument();
    expect(screen.queryByText('Wizard content')).toBeNull();
  });

  it('renders children for an admin user (admin is also volunteer)', () => {
    renderVolunteerRoute(true, true);
    expect(screen.getByText('Wizard content')).toBeInTheDocument();
  });
});

// ── ImportsListPage ───────────────────────────────────────────────────────────

describe('ImportsListPage — batch list', () => {
  it('shows the batch file name, entity, and status after loading', async () => {
    (listImportBatches as ReturnType<typeof vi.fn>).mockResolvedValue({
      items: [completedBatch],
      total: 1,
      limit: 25,
      offset: 0,
    });

    renderListPage();

    await waitFor(() => {
      expect(screen.getAllByText('newcomers.csv').length).toBeGreaterThan(0);
    });
    expect(screen.getAllByText('contact').length).toBeGreaterThan(0);
    expect(screen.getAllByText('completed').length).toBeGreaterThan(0);
  });

  it('renders empty state when no batches', async () => {
    (listImportBatches as ReturnType<typeof vi.fn>).mockResolvedValue({
      items: [],
      total: 0,
      limit: 25,
      offset: 0,
    });

    renderListPage();

    await waitFor(() => {
      expect(screen.getByText(/no imports yet/i)).toBeInTheDocument();
    });
  });

  it('shows error state on fetch failure', async () => {
    (listImportBatches as ReturnType<typeof vi.fn>).mockRejectedValue(new Error('network'));

    renderListPage();

    await waitFor(() => {
      expect(screen.getByText(/failed to load import runs/i)).toBeInTheDocument();
    });
  });
});

// ── ImportReportPage ──────────────────────────────────────────────────────────

describe('ImportReportPage — batch detail + rows', () => {
  beforeEach(() => {
    (getImportBatch as ReturnType<typeof vi.fn>).mockResolvedValue(completedBatch);
    (listImportRows as ReturnType<typeof vi.fn>).mockResolvedValue(mkRowListResponse([rowResult]));
  });

  it('renders batch filename and status badge', async () => {
    renderReportPage(42);

    await waitFor(() => {
      expect(screen.getAllByText('newcomers.csv').length).toBeGreaterThan(0);
    });
    expect(screen.getAllByText('completed').length).toBeGreaterThan(0);
  });

  it('renders a row result with outcome badge', async () => {
    renderReportPage(42);

    await waitFor(() => {
      expect(screen.getAllByText('created').length).toBeGreaterThan(0);
    });
  });

  it('shows empty state when no rows match the filter', async () => {
    (listImportRows as ReturnType<typeof vi.fn>).mockResolvedValue(mkRowListResponse([]));

    renderReportPage(42);

    await waitFor(() => {
      expect(screen.getByText(/no rows match this filter/i)).toBeInTheDocument();
    });
  });

  it('calls listImportRows with outcome filter when an outcome tab is clicked', async () => {
    renderReportPage(42);

    await waitFor(() => {
      expect(screen.getAllByText('created').length).toBeGreaterThan(0);
    });

    // Click the "Errors" filter tab
    const errorsTab = screen.getByRole('tab', { name: /errors/i });
    fireEvent.click(errorsTab);

    await waitFor(() => {
      const calls = (listImportRows as ReturnType<typeof vi.fn>).mock.calls;
      const hasOutcomeFilter = calls.some((call: unknown[]) => {
        const params = call[1] as { outcome?: string } | undefined;
        return params?.outcome === 'error';
      });
      expect(hasOutcomeFilter).toBe(true);
    });
  });

  it('Download report CSV button calls downloadImportReport', async () => {
    (downloadImportReport as ReturnType<typeof vi.fn>).mockResolvedValue(undefined);

    renderReportPage(42);

    await waitFor(() => {
      expect(screen.getAllByText('newcomers.csv').length).toBeGreaterThan(0);
    });

    const downloadBtn = screen.getByRole('button', { name: /download report csv/i });
    fireEvent.click(downloadBtn);

    await waitFor(() => {
      expect(downloadImportReport).toHaveBeenCalledWith(42);
    });
  });

  it('shows error state on batch load failure', async () => {
    (getImportBatch as ReturnType<typeof vi.fn>).mockRejectedValue(new Error('network'));

    renderReportPage(42);

    await waitFor(() => {
      expect(screen.getByText(/failed to load import batch/i)).toBeInTheDocument();
    });
  });
});

// ── MapStep — duplicate target validation ─────────────────────────────────────

describe('MapStep — duplicate target validation', () => {
  beforeEach(() => {
    (getColumns as ReturnType<typeof vi.fn>).mockResolvedValue({
      headers: ['First Name', 'Email', 'Also Email'],
      sample_rows: [],
      sheets: [],
      suggested_map: {
        'First Name': { target: 'first_name', data_type: 'string', confidence: 0.9 },
        Email: { target: 'email', data_type: 'string', confidence: 0.95 },
        'Also Email': { target: 'ignore', data_type: 'string', confidence: 0 },
      },
      targets: [
        { key: 'first_name', label: 'First Name', data_type: 'string' },
        { key: 'email', label: 'Email', data_type: 'string' },
      ],
    });
  });

  function renderMapStep(columnMap: Record<string, string>) {
    const client = mkClient();
    return render(
      <QueryClientProvider client={client}>
        <MemoryRouter>
          <MapStep
            batchId={1}
            entity="contact"
            columns={[
              { name: 'First Name', sample: [] },
              { name: 'Email', sample: [] },
              { name: 'Also Email', sample: [] },
            ]}
            selectedSheet={null}
            columnMap={columnMap}
            matchKey="email"
            conflictPolicy="skip"
            targetEventId={null}
            onColumnMapChange={vi.fn()}
            onMatchKeyChange={vi.fn()}
            onConflictPolicyChange={vi.fn()}
            onTargetEventIdChange={vi.fn()}
            onBack={vi.fn()}
            onNext={vi.fn()}
          />
        </MemoryRouter>
      </QueryClientProvider>,
    );
  }

  it('shows duplicate target error when two columns share the same non-ignore target', async () => {
    renderMapStep({ 'First Name': 'email', Email: 'email', 'Also Email': 'ignore' });

    await waitFor(() => {
      const alert = screen.getByRole('alert');
      expect(alert).toBeInTheDocument();
      // Alert banner mentions the duplicated target name
      expect(alert.textContent).toMatch(/email/i);
    });
  });

  it('disables the Next button when there are duplicate targets', async () => {
    renderMapStep({ 'First Name': 'email', Email: 'email', 'Also Email': 'ignore' });

    await waitFor(() => {
      const nextBtn = screen.getByRole('button', { name: /next: preview/i });
      expect(nextBtn).toBeDisabled();
    });
  });

  it('enables the Next button when no duplicates exist', async () => {
    renderMapStep({ 'First Name': 'first_name', Email: 'email', 'Also Email': 'ignore' });

    await waitFor(() => {
      const nextBtn = screen.getByRole('button', { name: /next: preview/i });
      expect(nextBtn).not.toBeDisabled();
    });
  });
});

// ── ImportWizardPage — upload / step 1 ───────────────────────────────────────

describe('ImportWizardPage — step 1 renders entity selector and drop zone', () => {
  it('renders entity radio buttons and the drop zone', () => {
    const client = mkClient();
    render(
      <QueryClientProvider client={client}>
        <MemoryRouter>
          <ImportWizardPage />
        </MemoryRouter>
      </QueryClientProvider>,
    );

    expect(screen.getByLabelText(/contacts/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/participants/i)).toBeInTheDocument();
    expect(
      screen.getByRole('button', { name: /drop a csv or excel file/i }),
    ).toBeInTheDocument();
  });

  it('shows the step 1 indicator as current', () => {
    const client = mkClient();
    render(
      <QueryClientProvider client={client}>
        <MemoryRouter>
          <ImportWizardPage />
        </MemoryRouter>
      </QueryClientProvider>,
    );

    const step1 = screen.getByRole('navigation', { name: /import wizard steps/i });
    expect(step1).toBeInTheDocument();
  });
});

// ── Toast on 413 upload error ─────────────────────────────────────────────────

describe('ImportWizardPage — 413 upload error surfaces toast', () => {
  it('calls toast.error with server detail on a 413 error', async () => {
    const { uploadImport: mockUpload } = await import('@/services/imports');
    (mockUpload as ReturnType<typeof vi.fn>).mockRejectedValue({
      response: {
        status: 413,
        data: { detail: 'File exceeds 15 MB limit.' },
      },
    });

    const client = mkClient();
    render(
      <QueryClientProvider client={client}>
        <MemoryRouter>
          <ImportWizardPage />
        </MemoryRouter>
      </QueryClientProvider>,
    );

    // Simulate file drop
    const dropZone = screen.getByRole('button', { name: /drop a csv or excel file/i });
    const file = new File(['a,b,c\n1,2,3'], 'test.csv', { type: 'text/csv' });
    Object.defineProperty(file, 'size', { value: 1024 });

    await waitFor(() => {
      fireEvent.drop(dropZone, {
        dataTransfer: { files: [file] },
      });
    });

    await waitFor(() => {
      expect(toast.error).toHaveBeenCalledWith(
        expect.stringContaining('File exceeds 15 MB limit.'),
      );
    });
  });
});
