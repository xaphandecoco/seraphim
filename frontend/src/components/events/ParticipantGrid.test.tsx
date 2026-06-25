/**
 * Tests for ParticipantGrid.
 *
 * Covers:
 *   - Add Participant button visible for canEdit=true, hidden for canEdit=false
 *   - Clicking Add Participant opens ContactPickerModal
 *   - Successful add calls addParticipant and shows success toast
 *   - Duplicate add (409) shows 'Contact is already registered for this event.' toast
 *   - Status dropdown calls updateParticipantStatus
 *   - No status dropdown when canEdit=false
 *   - listParticipants called with correct args
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

// --- Mocks ------------------------------------------------------------------

vi.mock('@/services/events', () => ({
  listParticipants: vi.fn(),
  addParticipant: vi.fn(),
  updateParticipantStatus: vi.fn(),
}));

vi.mock('@/components/tasks/ContactPickerModal', () => ({
  ContactPickerModal: ({
    onSelect,
    onClose,
  }: {
    mode: string;
    onSelect: (m: { contact_id: number; first_name: string; last_name: string; display_name: string }) => void;
    onClose: () => void;
  }) => (
    <div data-testid="contact-picker">
      <button
        onClick={() =>
          onSelect({ contact_id: 42, first_name: 'Jane', last_name: 'Doe', display_name: 'Jane Doe' })
        }
      >
        Pick Jane
      </button>
      <button onClick={onClose}>Close</button>
    </div>
  ),
}));

// Mock sonner toast so we can assert calls
vi.mock('sonner', () => ({
  toast: {
    success: vi.fn(),
    error: vi.fn(),
  },
}));

import { toast } from 'sonner';
import { listParticipants, addParticipant, updateParticipantStatus } from '@/services/events';
import { ParticipantGrid } from './ParticipantGrid';

// ---------- Helpers ----------------------------------------------------------

const EVENT_ID = 7;

const makeParticipant = (overrides = {}) => ({
  participant_id: 1,
  contact_id: 10,
  contact_display_name: 'John Smith',
  status: 'registered',
  source: 'manual',
  role: null,
  created_at: '2026-06-01T00:00:00Z',
  ...overrides,
});

const emptyPage = { total: 0, page: 1, page_size: 25, items: [] };
const onePage = {
  total: 1,
  page: 1,
  page_size: 25,
  items: [makeParticipant()],
};

function mkClient() {
  return new QueryClient({ defaultOptions: { queries: { retry: false } } });
}

function renderGrid(props: { eventId?: number; canEdit?: boolean } = {}) {
  const client = mkClient();
  const utils = render(
    <QueryClientProvider client={client}>
      <ParticipantGrid eventId={props.eventId ?? EVENT_ID} canEdit={props.canEdit ?? false} />
    </QueryClientProvider>,
  );
  return { ...utils, client };
}

// ---------- Tests ------------------------------------------------------------

beforeEach(() => {
  vi.clearAllMocks();
  (listParticipants as ReturnType<typeof vi.fn>).mockResolvedValue(emptyPage);
});

describe('ParticipantGrid — Add Participant button visibility', () => {
  it('shows Add Participant button when canEdit=true', async () => {
    renderGrid({ canEdit: true });
    expect(
      await screen.findByRole('button', { name: /add participant/i }),
    ).toBeInTheDocument();
  });

  it('hides Add Participant button when canEdit=false', async () => {
    renderGrid({ canEdit: false });
    // Wait for render to settle (empty state rendered for both desktop + mobile)
    await screen.findAllByText(/no participants yet/i);
    expect(screen.queryByRole('button', { name: /add participant/i })).toBeNull();
  });
});

describe('ParticipantGrid — ContactPickerModal', () => {
  it('opens ContactPickerModal when Add Participant is clicked', async () => {
    renderGrid({ canEdit: true });
    const btn = await screen.findByRole('button', { name: /add participant/i });
    fireEvent.click(btn);
    expect(screen.getByTestId('contact-picker')).toBeInTheDocument();
  });

  it('calls addParticipant and shows success toast on member select', async () => {
    (addParticipant as ReturnType<typeof vi.fn>).mockResolvedValue(makeParticipant());
    renderGrid({ canEdit: true });
    const addBtn = await screen.findByRole('button', { name: /add participant/i });
    fireEvent.click(addBtn);
    const pickBtn = await screen.findByRole('button', { name: /pick jane/i });
    fireEvent.click(pickBtn);
    await waitFor(() => {
      expect(addParticipant).toHaveBeenCalledWith(EVENT_ID, {
        contact_id: 42,
        source: 'manual',
      });
    });
    await waitFor(() => {
      expect(toast.success).toHaveBeenCalledWith('Participant added');
    });
  });

  it('shows 409 duplicate toast on duplicate add', async () => {
    const err = { response: { status: 409 } };
    (addParticipant as ReturnType<typeof vi.fn>).mockRejectedValue(err);
    renderGrid({ canEdit: true });
    const addBtn = await screen.findByRole('button', { name: /add participant/i });
    fireEvent.click(addBtn);
    const pickBtn = await screen.findByRole('button', { name: /pick jane/i });
    fireEvent.click(pickBtn);
    await waitFor(() => {
      expect(toast.error).toHaveBeenCalledWith(
        'Contact is already registered for this event.',
      );
    });
  });
});

describe('ParticipantGrid — status dropdown', () => {
  it('calls updateParticipantStatus when status changes', async () => {
    (listParticipants as ReturnType<typeof vi.fn>).mockResolvedValue(onePage);
    (updateParticipantStatus as ReturnType<typeof vi.fn>).mockResolvedValue(
      makeParticipant({ status: 'attended' }),
    );
    renderGrid({ canEdit: true });
    // Multiple dropdowns appear (desktop + mobile), use first one
    const dropdowns = await screen.findAllByRole('combobox', { name: /participant status/i });
    expect(dropdowns.length).toBeGreaterThan(0);
    fireEvent.change(dropdowns[0], { target: { value: 'attended' } });
    await waitFor(() => {
      expect(updateParticipantStatus).toHaveBeenCalledWith(
        EVENT_ID,
        1, // participant_id
        { status: 'attended' },
      );
    });
  });

  it('renders status as plain text when canEdit=false', async () => {
    (listParticipants as ReturnType<typeof vi.fn>).mockResolvedValue(onePage);
    renderGrid({ canEdit: false });
    // Wait for participant to appear
    await screen.findAllByText('John Smith');
    // No status dropdown
    expect(screen.queryAllByRole('combobox', { name: /participant status/i })).toHaveLength(0);
  });
});

describe('ParticipantGrid — query key / API call', () => {
  it('calls listParticipants with correct eventId, page, and page_size', async () => {
    renderGrid({ eventId: EVENT_ID });
    await waitFor(() => {
      expect(listParticipants).toHaveBeenCalledWith(EVENT_ID, {
        page: 1,
        page_size: 25,
      });
    });
  });
});
