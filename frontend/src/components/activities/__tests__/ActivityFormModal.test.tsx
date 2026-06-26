/**
 * ActivityFormModal unit tests — S12 acceptance criteria (§8 frontend)
 *
 * Covers:
 *  - Type select options are populated from mocked meta.types
 *  - Assignee select options are populated from mocked assignees
 *  - Submitting in create mode fires useCreateActivity.mutate with correct payload
 *  - On API error, toast.error receives server detail string
 *  - target_contact_id is pre-filled (locked) when targetContactId prop is provided
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

// ---------------------------------------------------------------------------
// Mocks — must appear before import of the component
// ---------------------------------------------------------------------------

const mockCreateMutate = vi.fn();
const mockUpdateMutate = vi.fn();

vi.mock('@/hooks/useActivities', () => ({
  useActivityMeta: vi.fn(),
  useAssignees: vi.fn(),
  useCreateActivity: vi.fn(),
  useUpdateActivity: vi.fn(),
}));

vi.mock('sonner', () => ({
  toast: {
    success: vi.fn(),
    error: vi.fn(),
  },
}));

import { useActivityMeta, useAssignees, useCreateActivity, useUpdateActivity } from '@/hooks/useActivities';
import { toast } from 'sonner';
import { ActivityFormModal } from '../ActivityFormModal';
import type { ActivityDetail } from '@/types/activity';

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

const META = {
  types: ['call', 'visit', 'follow_up'],
  statuses: ['scheduled', 'in_progress', 'completed', 'cancelled'],
  priorities: ['low', 'normal', 'high', 'urgent'],
};

const ASSIGNEES = [
  { id: 1, name: 'Alice Admin', email: 'alice@lightnc.org', role: 'admin' },
  { id: 2, name: 'Bob Volunteer', email: 'bob@lightnc.org', role: 'volunteer' },
];

const EDIT_ACTIVITY: ActivityDetail = {
  id: 5,
  activity_type: 'visit',
  subject: 'Existing task',
  details: 'Some details',
  activity_date: '2026-06-01T10:00:00',
  due_date: '2026-06-15T10:00:00',
  status: 'scheduled',
  priority: 'high',
  assignee_user_id: 1,
  target_contact_id: 10,
  created_by_id: 1,
  completed_at: null,
  reminder_sent_at: null,
  created_at: '2026-06-01T09:00:00',
  updated_at: '2026-06-01T09:00:00',
  assignee_name: 'Alice Admin',
  assignee_email: 'alice@lightnc.org',
  creator_name: 'Alice Admin',
  target_contact_name: 'Test Contact',
};

// ---------------------------------------------------------------------------
// Setup helpers
// ---------------------------------------------------------------------------

function setupMocks(opts: { createMutate?: ReturnType<typeof vi.fn> } = {}) {
  const createMutate = opts.createMutate ?? mockCreateMutate;

  vi.mocked(useActivityMeta).mockReturnValue({
    data: META,
    isLoading: false,
    isError: false,
  } as any);

  vi.mocked(useAssignees).mockReturnValue({
    data: ASSIGNEES,
    isLoading: false,
    isError: false,
  } as any);

  vi.mocked(useCreateActivity).mockReturnValue({
    mutate: createMutate,
    isPending: false,
  } as any);

  vi.mocked(useUpdateActivity).mockReturnValue({
    mutate: mockUpdateMutate,
    isPending: false,
  } as any);
}

function renderModal(props: Partial<React.ComponentProps<typeof ActivityFormModal>> = {}) {
  return render(
    <MemoryRouter>
      <ActivityFormModal onClose={vi.fn()} {...props} />
    </MemoryRouter>,
  );
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

beforeEach(() => {
  vi.clearAllMocks();
  setupMocks();
});

describe('ActivityFormModal — meta-driven selects', () => {
  it('type select is populated from meta.types', () => {
    renderModal();
    const typeSelect = screen.getByLabelText('Type');
    const options = Array.from(typeSelect.querySelectorAll('option'));
    const optionValues = options.map((o) => o.value).filter(Boolean);
    expect(optionValues).toContain('call');
    expect(optionValues).toContain('visit');
    expect(optionValues).toContain('follow_up');
  });

  it('assignee select is populated from assignees list', () => {
    renderModal();
    const assigneeSelect = screen.getByLabelText('Assignee');
    const options = Array.from(assigneeSelect.querySelectorAll('option'));
    const names = options.map((o) => o.textContent);
    expect(names).toContain('Alice Admin');
    expect(names).toContain('Bob Volunteer');
  });
});

describe('ActivityFormModal — create mode submission', () => {
  it('fires createActivity.mutate with subject and type when form is submitted', async () => {
    renderModal();

    // Select type
    fireEvent.change(screen.getByLabelText('Type'), { target: { value: 'visit' } });

    // Enter subject
    fireEvent.change(screen.getByPlaceholderText('Brief title…'), {
      target: { value: 'My new task' },
    });

    // Submit
    fireEvent.click(screen.getByRole('button', { name: /create/i }));

    await waitFor(() => {
      expect(mockCreateMutate).toHaveBeenCalledWith(
        expect.objectContaining({ subject: 'My new task', activity_type: 'visit' }),
        expect.objectContaining({ onSuccess: expect.any(Function), onError: expect.any(Function) }),
      );
    });
  });

  it('calls toast.error with server detail on API error', async () => {
    const mutateWithError = vi.fn((_payload: unknown, callbacks: { onError?: (err: unknown) => void } = {}) => {
      callbacks.onError?.({ response: { data: { detail: 'Assignee not found' } } });
    });
    setupMocks({ createMutate: mutateWithError });

    renderModal();
    fireEvent.change(screen.getByLabelText('Type'), { target: { value: 'call' } });
    fireEvent.change(screen.getByPlaceholderText('Brief title…'), {
      target: { value: 'Test task' },
    });
    fireEvent.click(screen.getByRole('button', { name: /create/i }));

    await waitFor(() => {
      expect(toast.error).toHaveBeenCalledWith('Assignee not found');
    });
  });
});

describe('ActivityFormModal — target contact pre-fill', () => {
  it('shows locked contact name when targetContactId + targetContactName are provided', () => {
    renderModal({ targetContactId: 42, targetContactName: 'Jane Doe' });
    // The contact field shows a locked <p> instead of a number input
    expect(screen.getByText('Jane Doe')).toBeInTheDocument();
    // And the number input is gone
    expect(screen.queryByPlaceholderText('Contact ID (optional)')).not.toBeInTheDocument();
  });

  it('shows the numeric input when no targetContactId is provided', () => {
    renderModal();
    expect(screen.getByPlaceholderText('Contact ID (optional)')).toBeInTheDocument();
  });
});

describe('ActivityFormModal — edit mode', () => {
  it('shows "Save Changes" button in edit mode', () => {
    renderModal({ activity: EDIT_ACTIVITY });
    expect(screen.getByRole('button', { name: /save changes/i })).toBeInTheDocument();
  });

  it('pre-fills subject from existing activity', () => {
    renderModal({ activity: EDIT_ACTIVITY });
    const input = screen.getByPlaceholderText('Brief title…') as HTMLInputElement;
    expect(input.value).toBe('Existing task');
  });

  it('fires updateActivity.mutate on submit in edit mode', async () => {
    renderModal({ activity: EDIT_ACTIVITY });
    fireEvent.click(screen.getByRole('button', { name: /save changes/i }));

    await waitFor(() => {
      expect(mockUpdateMutate).toHaveBeenCalledWith(
        expect.objectContaining({ id: 5 }),
        expect.objectContaining({ onSuccess: expect.any(Function) }),
      );
    });
  });
});
