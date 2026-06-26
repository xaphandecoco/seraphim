/**
 * ActivitiesPanel unit tests — S12 acceptance criteria (§8 frontend)
 *
 * Covers:
 *  - Shows LoadingState while data is loading
 *  - Shows EmptyState when there are no activities (byContact query returns empty list)
 *  - Renders ActivityCard for each activity when data is present
 *  - "New Task" button is visible for volunteers and opens the form modal
 *  - "New Task" button is hidden for viewers (isVolunteer=false, isViewer=true)
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

// ---------------------------------------------------------------------------
// Mocks — must appear before component import
// ---------------------------------------------------------------------------

vi.mock('@/hooks/useActivities', () => ({
  useActivitiesByContact: vi.fn(),
  useUpdateActivity: vi.fn(),
  useDeleteActivity: vi.fn(),
}));

vi.mock('@/store/authStore', () => ({
  useAuthStore: vi.fn((selector: (s: { isAdmin: boolean; isViewer: boolean; isVolunteer: boolean }) => unknown) =>
    selector({ isAdmin: false, isViewer: false, isVolunteer: true }),
  ),
}));

vi.mock('sonner', () => ({ toast: { error: vi.fn(), success: vi.fn() } }));

// ActivityFormModal renders the heavy meta/assignees queries — mock it out.
vi.mock('@/components/activities/ActivityFormModal', () => ({
  ActivityFormModal: ({ onClose }: { onClose: () => void }) => (
    <div data-testid="form-modal">
      <button onClick={onClose}>Close modal</button>
    </div>
  ),
}));

import { useAuthStore } from '@/store/authStore';
import { useActivitiesByContact, useUpdateActivity, useDeleteActivity } from '@/hooks/useActivities';
import { ActivitiesPanel } from '../ActivitiesPanel';
import type { ActivityDetail } from '@/types/activity';

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

const CONTACT_ID = 10;

const SAMPLE_ACTIVITY: ActivityDetail = {
  id: 1,
  activity_type: 'call',
  subject: 'Check in with member',
  details: null,
  activity_date: new Date().toISOString(),
  due_date: null,
  status: 'scheduled',
  priority: 'normal',
  assignee_user_id: 1,
  target_contact_id: CONTACT_ID,
  created_by_id: 1,
  completed_at: null,
  reminder_sent_at: null,
  created_at: new Date().toISOString(),
  updated_at: new Date().toISOString(),
  assignee_name: 'Alice Admin',
  assignee_email: 'alice@lightnc.org',
  creator_name: 'Alice Admin',
  target_contact_name: 'Test Contact',
};

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function mockStoreAs(role: 'admin' | 'volunteer' | 'viewer') {
  const isAdmin = role === 'admin';
  const isViewer = role === 'viewer';
  const isVolunteer = role === 'admin' || role === 'volunteer';
  vi.mocked(useAuthStore).mockImplementation(
    (selector: (s: { isAdmin: boolean; isViewer: boolean; isVolunteer: boolean }) => unknown) =>
      selector({ isAdmin, isViewer, isVolunteer }),
  );
}

function renderPanel() {
  return render(
    <MemoryRouter>
      <ActivitiesPanel contactId={CONTACT_ID} />
    </MemoryRouter>,
  );
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

beforeEach(() => {
  vi.clearAllMocks();

  // Default mutation stubs
  vi.mocked(useUpdateActivity).mockReturnValue({ mutate: vi.fn(), isPending: false } as any);
  vi.mocked(useDeleteActivity).mockReturnValue({ mutate: vi.fn(), isPending: false } as any);

  // Default store: volunteer
  mockStoreAs('volunteer');
});

describe('ActivitiesPanel — loading state', () => {
  it('renders LoadingState while query is loading', () => {
    vi.mocked(useActivitiesByContact).mockReturnValue({
      data: undefined,
      isLoading: true,
      isError: false,
      refetch: vi.fn(),
    } as any);

    renderPanel();
    expect(screen.getByText(/loading activities/i)).toBeInTheDocument();
  });
});

describe('ActivitiesPanel — empty state', () => {
  it('renders EmptyState with "No tasks for this contact yet" when list is empty', () => {
    vi.mocked(useActivitiesByContact).mockReturnValue({
      data: { items: [], total: 0, page: 1, page_size: 20 },
      isLoading: false,
      isError: false,
      refetch: vi.fn(),
    } as any);

    renderPanel();
    expect(screen.getByText('No tasks for this contact yet')).toBeInTheDocument();
  });
});

describe('ActivitiesPanel — data rendering', () => {
  it('renders an ActivityCard for each activity in the list', () => {
    vi.mocked(useActivitiesByContact).mockReturnValue({
      data: { items: [SAMPLE_ACTIVITY], total: 1, page: 1, page_size: 20 },
      isLoading: false,
      isError: false,
      refetch: vi.fn(),
    } as any);

    renderPanel();
    expect(screen.getByText('Check in with member')).toBeInTheDocument();
  });
});

describe('ActivitiesPanel — "New Task" button visibility', () => {
  beforeEach(() => {
    vi.mocked(useActivitiesByContact).mockReturnValue({
      data: { items: [], total: 0, page: 1, page_size: 20 },
      isLoading: false,
      isError: false,
      refetch: vi.fn(),
    } as any);
  });

  it('shows "New Task" button for volunteer', () => {
    mockStoreAs('volunteer');
    renderPanel();
    expect(screen.getByLabelText('New Task for this contact')).toBeInTheDocument();
  });

  it('shows "New Task" button for admin', () => {
    mockStoreAs('admin');
    renderPanel();
    expect(screen.getByLabelText('New Task for this contact')).toBeInTheDocument();
  });

  it('hides "New Task" button for viewer', () => {
    mockStoreAs('viewer');
    renderPanel();
    expect(screen.queryByLabelText('New Task for this contact')).not.toBeInTheDocument();
  });

  it('clicking "New Task" opens the ActivityFormModal', () => {
    mockStoreAs('volunteer');
    renderPanel();
    fireEvent.click(screen.getByLabelText('New Task for this contact'));
    expect(screen.getByTestId('form-modal')).toBeInTheDocument();
  });
});

describe('ActivitiesPanel — error state', () => {
  it('renders ErrorState with retry button on query failure', () => {
    const refetch = vi.fn();
    vi.mocked(useActivitiesByContact).mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: true,
      refetch,
    } as any);

    renderPanel();
    expect(screen.getByText('Failed to load activities')).toBeInTheDocument();
    fireEvent.click(screen.getByText('Try again'));
    expect(refetch).toHaveBeenCalled();
  });
});
