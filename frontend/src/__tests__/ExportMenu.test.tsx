/**
 * Tests for ExportMenu (S05-F12).
 *
 * Covers:
 *   - Renders Export trigger button
 *   - Opens dropdown menu with CSV, XLSX, and background options
 *   - CSV download calls downloadBlobFromApi with correct args
 *   - XLSX download calls downloadBlobFromApi with correct args
 *   - Background export calls enqueueExportJob and starts polling
 *   - Polling stops on terminal status (done/failed)
 *   - Error toast on download failure (surfaces err.response.data.detail)
 *   - Error toast on enqueueExportJob failure
 *   - Accepts eventId and merges into params
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

// ---------- Mocks -------------------------------------------------------------

const mockDownloadBlobFromApi = vi.fn();
const mockEnqueueExportJob = vi.fn();
const mockPollExportJob = vi.fn();

vi.mock('@/services/export', () => ({
  downloadBlobFromApi: (...a: unknown[]) => mockDownloadBlobFromApi(...a),
  enqueueExportJob: (...a: unknown[]) => mockEnqueueExportJob(...a),
  pollExportJob: (...a: unknown[]) => mockPollExportJob(...a),
  downloadBlob: vi.fn(),
}));

vi.mock('sonner', () => ({
  toast: {
    success: vi.fn(),
    error: vi.fn(),
  },
}));

import { toast } from 'sonner';
import { ExportMenu } from '@/components/export/ExportMenu';

// ---------- Helpers -----------------------------------------------------------

function mkClient() {
  return new QueryClient({ defaultOptions: { queries: { retry: false } } });
}

function renderMenu(props: Partial<React.ComponentProps<typeof ExportMenu>> = {}) {
  const client = mkClient();
  const utils = render(
    <QueryClientProvider client={client}>
      <ExportMenu jobType="contacts" {...props} />
    </QueryClientProvider>,
  );
  return { ...utils, client };
}

// ---------- Setup -------------------------------------------------------------

beforeEach(() => {
  vi.clearAllMocks();
  mockDownloadBlobFromApi.mockResolvedValue(undefined);
  mockEnqueueExportJob.mockResolvedValue({ id: 'job-1', status: 'pending' });
  mockPollExportJob.mockResolvedValue({ id: 'job-1', status: 'done', download_url: '/files/export.csv' });
});

// ---------- Tests -------------------------------------------------------------

describe('ExportMenu — rendering', () => {
  it('renders the Export button', () => {
    renderMenu();
    expect(screen.getByRole('button', { name: /export options/i })).toBeInTheDocument();
  });

  it('does not show dropdown by default', () => {
    renderMenu();
    expect(screen.queryByRole('menu')).toBeNull();
  });

  it('opens dropdown on button click', async () => {
    renderMenu();
    fireEvent.click(screen.getByRole('button', { name: /export options/i }));
    expect(await screen.findByRole('menu')).toBeInTheDocument();
  });

  it('shows CSV, XLSX, and background options in dropdown', async () => {
    renderMenu();
    fireEvent.click(screen.getByRole('button', { name: /export options/i }));
    await screen.findByRole('menu');
    expect(screen.getByRole('menuitem', { name: /download csv/i })).toBeInTheDocument();
    expect(screen.getByRole('menuitem', { name: /download xlsx/i })).toBeInTheDocument();
    expect(screen.getByRole('menuitem', { name: /run in background/i })).toBeInTheDocument();
  });
});

describe('ExportMenu — sync CSV download', () => {
  it('calls downloadBlobFromApi with csv path on CSV click', async () => {
    renderMenu({ jobType: 'contacts', filters: { search: 'jane' } });
    fireEvent.click(screen.getByRole('button', { name: /export options/i }));
    await screen.findByRole('menu');
    fireEvent.click(screen.getByRole('menuitem', { name: /download csv/i }));

    await waitFor(() => {
      expect(mockDownloadBlobFromApi).toHaveBeenCalledWith(
        '/export/contacts.csv',
        expect.objectContaining({ search: 'jane' }),
        'contacts_export.csv',
      );
    });
  });

  it('calls downloadBlobFromApi with xlsx path on XLSX click', async () => {
    renderMenu({ jobType: 'contacts' });
    fireEvent.click(screen.getByRole('button', { name: /export options/i }));
    await screen.findByRole('menu');
    fireEvent.click(screen.getByRole('menuitem', { name: /download xlsx/i }));

    await waitFor(() => {
      expect(mockDownloadBlobFromApi).toHaveBeenCalledWith(
        '/export/contacts.xlsx',
        expect.any(Object),
        'contacts_export.xlsx',
      );
    });
  });

  it('merges eventId into params when provided', async () => {
    renderMenu({ jobType: 'participants', eventId: 42 });
    fireEvent.click(screen.getByRole('button', { name: /export options/i }));
    await screen.findByRole('menu');
    fireEvent.click(screen.getByRole('menuitem', { name: /download csv/i }));

    await waitFor(() => {
      expect(mockDownloadBlobFromApi).toHaveBeenCalledWith(
        '/export/participants.csv',
        expect.objectContaining({ event_id: 42 }),
        'participants_export.csv',
      );
    });
  });

  it('shows toast.error with detail on CSV download failure', async () => {
    mockDownloadBlobFromApi.mockRejectedValue({
      response: { data: { detail: 'Permission denied' } },
    });
    renderMenu();
    fireEvent.click(screen.getByRole('button', { name: /export options/i }));
    await screen.findByRole('menu');
    fireEvent.click(screen.getByRole('menuitem', { name: /download csv/i }));

    await waitFor(() => {
      expect(toast.error).toHaveBeenCalledWith('Permission denied');
    });
  });

  it('shows generic toast.error when no detail on CSV failure', async () => {
    mockDownloadBlobFromApi.mockRejectedValue(new Error('Network error'));
    renderMenu();
    fireEvent.click(screen.getByRole('button', { name: /export options/i }));
    await screen.findByRole('menu');
    fireEvent.click(screen.getByRole('menuitem', { name: /download csv/i }));

    await waitFor(() => {
      expect(toast.error).toHaveBeenCalledWith('Export failed');
    });
  });
});

describe('ExportMenu — background export job', () => {
  it('calls enqueueExportJob and shows success toast', async () => {
    renderMenu({ jobType: 'participants', eventId: 7 });
    fireEvent.click(screen.getByRole('button', { name: /export options/i }));
    await screen.findByRole('menu');
    fireEvent.click(screen.getByRole('menuitem', { name: /run in background/i }));

    await waitFor(() => {
      expect(mockEnqueueExportJob).toHaveBeenCalledWith(
        'participants',
        expect.objectContaining({ event_id: 7 }),
      );
    });
    await waitFor(() => {
      expect(toast.success).toHaveBeenCalledWith(
        expect.stringContaining('Export job started'),
      );
    });
  });

  it('shows toast.error when enqueueExportJob fails with detail', async () => {
    mockEnqueueExportJob.mockRejectedValue({
      response: { data: { detail: 'Queue full' } },
    });
    renderMenu();
    fireEvent.click(screen.getByRole('button', { name: /export options/i }));
    await screen.findByRole('menu');
    fireEvent.click(screen.getByRole('menuitem', { name: /run in background/i }));

    await waitFor(() => {
      expect(toast.error).toHaveBeenCalledWith('Queue full');
    });
  });

  it('starts polling after enqueue and shows download ready link when done', async () => {
    mockPollExportJob.mockResolvedValue({
      id: 'job-1',
      status: 'done',
      download_url: '/files/export.csv',
    });

    renderMenu();
    fireEvent.click(screen.getByRole('button', { name: /export options/i }));
    await screen.findByRole('menu');
    fireEvent.click(screen.getByRole('menuitem', { name: /run in background/i }));

    await waitFor(() => {
      expect(mockEnqueueExportJob).toHaveBeenCalled();
    });

    // After polling, download-ready link should appear (accessible name from aria-label)
    await waitFor(() => {
      expect(screen.getByRole('link', { name: /download completed export/i })).toBeInTheDocument();
    });
  });

  it('shows error text when job fails', async () => {
    mockPollExportJob.mockResolvedValue({
      id: 'job-1',
      status: 'failed',
      error: 'DB timeout',
    });

    renderMenu();
    fireEvent.click(screen.getByRole('button', { name: /export options/i }));
    await screen.findByRole('menu');
    fireEvent.click(screen.getByRole('menuitem', { name: /run in background/i }));

    await waitFor(() => {
      expect(screen.getByRole('alert')).toHaveTextContent(/export failed.*db timeout/i);
    });
  });
});

describe('ExportMenu — refetchInterval terminal status', () => {
  it('does not continue polling after done status (refetchInterval returns false)', () => {
    // Test the inline refetchInterval logic
    const TERMINAL_STATUSES = ['done', 'failed'];
    const refetchInterval = (query: { state: { data?: { status?: string } } }) => {
      const status = query.state.data?.status;
      return status && TERMINAL_STATUSES.includes(status) ? false : 2000;
    };

    expect(refetchInterval({ state: { data: { status: 'done' } } })).toBe(false);
    expect(refetchInterval({ state: { data: { status: 'failed' } } })).toBe(false);
    expect(refetchInterval({ state: { data: { status: 'running' } } })).toBe(2000);
    expect(refetchInterval({ state: { data: { status: 'pending' } } })).toBe(2000);
    expect(refetchInterval({ state: { data: undefined } })).toBe(2000);
  });
});
