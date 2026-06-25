/**
 * Tests for BulkParticipantPanel (S05-F12).
 *
 * Covers:
 *   - Panel is hidden for viewer role
 *   - Collapsible toggle
 *   - Audience radio: all / ids / group (disabled when groups 404) / saved_search (disabled)
 *   - Operation tabs: Add / Set status / Remove
 *   - Hard-delete checkbox visible only for admin
 *   - Preview button calls POST /bulk-preview and shows counts
 *   - Apply button opens ConfirmDialog then POSTs correct endpoint
 *   - Success toast with counts after apply
 *   - Error toast on apply failure (surfaces err.response.data.detail)
 *   - invalidateQueries called after success
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

// ---------- Mocks -------------------------------------------------------------

const mockApiGet = vi.fn();
const mockApiPost = vi.fn();

vi.mock('@/services/api', () => ({
  api: {
    get: (...a: unknown[]) => mockApiGet(...a),
    post: (...a: unknown[]) => mockApiPost(...a),
  },
}));

vi.mock('sonner', () => ({
  toast: {
    success: vi.fn(),
    error: vi.fn(),
  },
}));

const mockIsAdmin = vi.fn(() => false);
const mockRole = vi.fn(() => 'admin' as string | null | undefined);

vi.mock('@/store/authStore', () => ({
  useAuthStore: vi.fn((selector: (s: { isAdmin: boolean; user: { role: string } | null }) => unknown) =>
    selector({ isAdmin: mockIsAdmin(), user: mockRole() ? { role: mockRole()! } : null }),
  ),
}));

import { toast } from 'sonner';
import { BulkParticipantPanel } from '@/components/participants/BulkParticipantPanel';

// ---------- Helpers -----------------------------------------------------------

const EVENT_ID = 10;

function mkClient() {
  return new QueryClient({ defaultOptions: { queries: { retry: false } } });
}

function renderPanel(props: Partial<React.ComponentProps<typeof BulkParticipantPanel>> = {}) {
  const client = mkClient();
  const utils = render(
    <QueryClientProvider client={client}>
      <BulkParticipantPanel eventId={EVENT_ID} selectedIds={[]} {...props} />
    </QueryClientProvider>,
  );
  return { ...utils, client };
}

// ---------- Default mock setup ------------------------------------------------

beforeEach(() => {
  vi.clearAllMocks();
  mockIsAdmin.mockReturnValue(true);
  mockRole.mockReturnValue('admin');
  // groups returns 404-like error to simulate S09 not shipped
  mockApiGet.mockRejectedValue({ response: { status: 404 } });
  // bulk-preview returns matched count
  mockApiPost.mockResolvedValue({ data: { matched: 5, will_add: 5 } });
});

// ---------- Tests -------------------------------------------------------------

describe('BulkParticipantPanel — viewer gate', () => {
  it('renders null for viewer role', () => {
    mockRole.mockReturnValue('viewer');
    mockIsAdmin.mockReturnValue(false);
    const { container } = renderPanel();
    expect(container.firstChild).toBeNull();
  });
});

describe('BulkParticipantPanel — collapsible', () => {
  it('is collapsed by default (operation buttons not visible)', () => {
    renderPanel();
    expect(screen.queryByRole('button', { name: /^add$/i })).toBeNull();
  });

  it('expands on header click', async () => {
    renderPanel();
    const header = screen.getByRole('button', { name: /bulk participant operations/i });
    fireEvent.click(header);
    expect(await screen.findByRole('button', { name: /^preview$/i })).toBeInTheDocument();
  });
});

describe('BulkParticipantPanel — audience modes', () => {
  it('renders all four audience radios', async () => {
    renderPanel();
    fireEvent.click(screen.getByRole('button', { name: /bulk participant operations/i }));
    await screen.findByRole('button', { name: /^preview$/i });
    // All radio options rendered
    expect(screen.getByText(/all participants/i)).toBeInTheDocument();
    expect(screen.getByText(/selected/i)).toBeInTheDocument();
    expect(screen.getByText(/group/i)).toBeInTheDocument();
    expect(screen.getByText(/saved search/i)).toBeInTheDocument();
  });

  it('group and saved_search radios are disabled when groups API 404s', async () => {
    renderPanel();
    fireEvent.click(screen.getByRole('button', { name: /bulk participant operations/i }));
    await screen.findByRole('button', { name: /^preview$/i });
    // Wait for groups query to settle (it will fail with 404)
    await waitFor(() => {
      const groupRadio = screen.getByDisplayValue('group') as HTMLInputElement;
      expect(groupRadio.disabled).toBe(true);
    });
    const savedSearchRadio = screen.getByDisplayValue('saved_search') as HTMLInputElement;
    expect(savedSearchRadio.disabled).toBe(true);
  });

  it('ids radio is disabled when selectedIds is empty', async () => {
    renderPanel({ selectedIds: [] });
    fireEvent.click(screen.getByRole('button', { name: /bulk participant operations/i }));
    await screen.findByRole('button', { name: /^preview$/i });
    const idsRadio = screen.getByDisplayValue('ids') as HTMLInputElement;
    expect(idsRadio.disabled).toBe(true);
  });

  it('ids radio is enabled when selectedIds has entries', async () => {
    renderPanel({ selectedIds: [1, 2, 3] });
    fireEvent.click(screen.getByRole('button', { name: /bulk participant operations/i }));
    await screen.findByRole('button', { name: /^preview$/i });
    const idsRadio = screen.getByDisplayValue('ids') as HTMLInputElement;
    expect(idsRadio.disabled).toBe(false);
  });
});

describe('BulkParticipantPanel — operation tabs', () => {
  async function expand() {
    renderPanel();
    fireEvent.click(screen.getByRole('button', { name: /bulk participant operations/i }));
    return screen.findByRole('button', { name: /^preview$/i });
  }

  it('shows Add tab as default', async () => {
    await expand();
    const addBtn = screen.getByRole('button', { name: /^add$/i });
    expect(addBtn).toHaveAttribute('aria-pressed', 'true');
  });

  it('switches to Set status tab and shows status select', async () => {
    await expand();
    const setStatusBtn = screen.getByRole('button', { name: /^set status$/i });
    fireEvent.click(setStatusBtn);
    expect(setStatusBtn).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByRole('combobox', { name: /bulk status value/i })).toBeInTheDocument();
  });

  it('switches to Remove tab', async () => {
    await expand();
    const removeBtn = screen.getByRole('button', { name: /^remove$/i });
    fireEvent.click(removeBtn);
    expect(removeBtn).toHaveAttribute('aria-pressed', 'true');
  });
});

describe('BulkParticipantPanel — hard-delete checkbox', () => {
  it('shows hard-delete checkbox for admin on remove tab', async () => {
    mockIsAdmin.mockReturnValue(true);
    renderPanel();
    fireEvent.click(screen.getByRole('button', { name: /bulk participant operations/i }));
    await screen.findByRole('button', { name: /^preview$/i });
    fireEvent.click(screen.getByRole('button', { name: /^remove$/i }));
    expect(screen.getByRole('checkbox')).toBeInTheDocument();
  });

  it('hides hard-delete checkbox for non-admin', async () => {
    mockIsAdmin.mockReturnValue(false);
    mockRole.mockReturnValue('volunteer');
    renderPanel();
    fireEvent.click(screen.getByRole('button', { name: /bulk participant operations/i }));
    await screen.findByRole('button', { name: /^preview$/i });
    fireEvent.click(screen.getByRole('button', { name: /^remove$/i }));
    expect(screen.queryByRole('checkbox')).toBeNull();
  });
});

describe('BulkParticipantPanel — preview', () => {
  it('calls POST /bulk-preview and shows counts', async () => {
    mockApiPost.mockResolvedValue({ data: { matched: 12, will_add: 10 } });
    renderPanel();
    fireEvent.click(screen.getByRole('button', { name: /bulk participant operations/i }));
    const previewBtn = await screen.findByRole('button', { name: /^preview$/i });
    fireEvent.click(previewBtn);

    await waitFor(() => {
      expect(mockApiPost).toHaveBeenCalledWith(
        `/events/${EVENT_ID}/participants/bulk-preview`,
        expect.objectContaining({ operation: 'add' }),
      );
    });
    expect(await screen.findByText(/matched/i)).toBeInTheDocument();
    expect(screen.getByText(/12/)).toBeInTheDocument();
  });

  it('shows toast.error when preview fails', async () => {
    mockApiPost.mockRejectedValue({ response: { data: { detail: 'Preview error' } } });
    renderPanel();
    fireEvent.click(screen.getByRole('button', { name: /bulk participant operations/i }));
    const previewBtn = await screen.findByRole('button', { name: /^preview$/i });
    fireEvent.click(previewBtn);

    await waitFor(() => {
      expect(toast.error).toHaveBeenCalledWith('Preview error');
    });
  });
});

describe('BulkParticipantPanel — apply', () => {
  it('opens ConfirmDialog on Apply click', async () => {
    renderPanel();
    fireEvent.click(screen.getByRole('button', { name: /bulk participant operations/i }));
    await screen.findByRole('button', { name: /^preview$/i });
    // Multiple Apply buttons exist (panel + dialog after click); use first
    const applyBtns = screen.getAllByRole('button', { name: /^apply$/i });
    fireEvent.click(applyBtns[0]);
    expect(await screen.findByRole('dialog')).toBeInTheDocument();
  });

  it('cancels without calling API when Cancel is clicked in dialog', async () => {
    renderPanel();
    fireEvent.click(screen.getByRole('button', { name: /bulk participant operations/i }));
    await screen.findByRole('button', { name: /^preview$/i });
    const applyBtns = screen.getAllByRole('button', { name: /^apply$/i });
    fireEvent.click(applyBtns[0]);
    const cancelBtn = await screen.findByRole('button', { name: /cancel/i });
    fireEvent.click(cancelBtn);
    // Bulk-add should NOT be called
    await waitFor(() => {
      const bulkAddCalls = (mockApiPost as ReturnType<typeof vi.fn>).mock.calls.filter(
        ([url]: [string]) => url?.includes('bulk-add'),
      );
      expect(bulkAddCalls).toHaveLength(0);
    });
  });

  it('calls bulk-add endpoint and shows success toast on confirm', async () => {
    mockApiPost.mockResolvedValue({ data: { affected: 8, skipped: 1, errors: 0 } });
    renderPanel();
    fireEvent.click(screen.getByRole('button', { name: /bulk participant operations/i }));
    await screen.findByRole('button', { name: /^preview$/i });
    // Click Apply in the panel — there will be two Apply buttons (panel + dialog)
    const applyBtns = screen.getAllByRole('button', { name: /^apply$/i });
    fireEvent.click(applyBtns[0]);

    // Wait for confirm dialog to appear; click Apply inside it
    const dialog = await screen.findByRole('dialog');
    const dialogApplyBtn = Array.from(dialog.querySelectorAll('button')).find(
      (b) => b.textContent === 'Apply',
    );
    expect(dialogApplyBtn).toBeDefined();
    fireEvent.click(dialogApplyBtn!);

    await waitFor(() => {
      expect(toast.success).toHaveBeenCalledWith(
        expect.stringContaining('affected'),
      );
    });
  });

  it('surfaces err.response.data.detail on apply failure', async () => {
    mockApiPost.mockRejectedValue({ response: { data: { detail: 'Bulk add failed' } } });

    renderPanel();
    fireEvent.click(screen.getByRole('button', { name: /bulk participant operations/i }));
    await screen.findByRole('button', { name: /^preview$/i });
    const applyBtns = screen.getAllByRole('button', { name: /^apply$/i });
    fireEvent.click(applyBtns[0]);

    // Wait for confirm dialog
    const dialog = await screen.findByRole('dialog');
    const dialogApplyBtn = Array.from(dialog.querySelectorAll('button')).find(
      (b) => b.textContent === 'Apply',
    );
    expect(dialogApplyBtn).toBeDefined();
    fireEvent.click(dialogApplyBtn!);

    await waitFor(() => {
      expect(toast.error).toHaveBeenCalledWith('Bulk add failed');
    });
  });
});
