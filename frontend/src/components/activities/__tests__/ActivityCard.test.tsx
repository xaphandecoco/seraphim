/**
 * ActivityCard unit tests — S12 acceptance criteria (§8 frontend)
 *
 * Covers:
 *  - Renders subject, type badge, priority indicator
 *  - Shows overdue indicator when due date is past and status is open
 *  - Does NOT show overdue indicator when activity is completed
 *  - Hides Reassign and Delete for non-admin users
 *  - Shows Reassign and Delete for admin users
 *  - Hides ALL mutation controls (including menu button) for viewer role
 *  - Target-contact renders as a link to /contacts/{id}
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { ActivityCard } from '../ActivityCard';
import type { ActivityDetail } from '@/types/activity';

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

const PAST_DUE = new Date(Date.now() - 24 * 60 * 60 * 1000).toISOString();
const FUTURE_DUE = new Date(Date.now() + 7 * 24 * 60 * 60 * 1000).toISOString();

const BASE: ActivityDetail = {
  id: 1,
  activity_type: 'call',
  subject: 'Follow up with Jane',
  details: null,
  activity_date: new Date().toISOString(),
  due_date: FUTURE_DUE,
  status: 'scheduled',
  priority: 'normal',
  assignee_user_id: 2,
  target_contact_id: 42,
  created_by_id: 1,
  completed_at: null,
  reminder_sent_at: null,
  created_at: new Date().toISOString(),
  updated_at: new Date().toISOString(),
  assignee_name: 'Bob Volunteer',
  assignee_email: 'bob@lightnc.org',
  creator_name: 'Alice Admin',
  target_contact_name: 'Jane Contact',
};

function renderCard(overrides: Partial<React.ComponentProps<typeof ActivityCard>> = {}) {
  return render(
    <MemoryRouter>
      <ActivityCard
        activity={BASE}
        isAdmin={false}
        isViewer={false}
        {...overrides}
      />
    </MemoryRouter>,
  );
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

beforeEach(() => {
  vi.clearAllMocks();
});

describe('ActivityCard — content rendering', () => {
  it('renders the activity subject', () => {
    renderCard();
    expect(screen.getByText('Follow up with Jane')).toBeInTheDocument();
  });

  it('renders the type badge with human-readable label', () => {
    renderCard();
    expect(screen.getByText('Call')).toBeInTheDocument();
  });

  it('renders the priority dot with aria-label', () => {
    const { container } = renderCard();
    const dot = container.querySelector('[aria-label="Priority: normal"]');
    expect(dot).toBeInTheDocument();
  });

  it('renders target-contact name as a link to /contacts/{id}', () => {
    renderCard();
    const link = screen.getByText('Jane Contact').closest('a');
    expect(link).toHaveAttribute('href', '/contacts/42');
  });

  it('renders assignee name', () => {
    renderCard();
    expect(screen.getByText('Bob Volunteer')).toBeInTheDocument();
  });

  it('renders status chip', () => {
    renderCard();
    expect(screen.getByText('Scheduled')).toBeInTheDocument();
  });
});

describe('ActivityCard — overdue indicator', () => {
  it('shows the overdue indicator (Clock) when due date is past and status is open', () => {
    renderCard({ activity: { ...BASE, due_date: PAST_DUE, status: 'scheduled' } });
    expect(screen.getByTestId('overdue-indicator')).toBeInTheDocument();
  });

  it('does NOT show the overdue indicator when the activity is completed', () => {
    renderCard({
      activity: { ...BASE, due_date: PAST_DUE, status: 'completed', completed_at: new Date().toISOString() },
    });
    expect(screen.queryByTestId('overdue-indicator')).not.toBeInTheDocument();
  });

  it('does NOT show the overdue indicator when the activity is cancelled', () => {
    renderCard({ activity: { ...BASE, due_date: PAST_DUE, status: 'cancelled' } });
    expect(screen.queryByTestId('overdue-indicator')).not.toBeInTheDocument();
  });

  it('does NOT show the overdue indicator when due date is in the future', () => {
    renderCard({ activity: { ...BASE, due_date: FUTURE_DUE, status: 'scheduled' } });
    expect(screen.queryByTestId('overdue-indicator')).not.toBeInTheDocument();
  });
});

describe('ActivityCard — role-gated actions', () => {
  it('shows the actions menu button for non-viewer volunteers', () => {
    renderCard({ isAdmin: false, isViewer: false });
    expect(screen.getByLabelText('Activity actions')).toBeInTheDocument();
  });

  it('hides Reassign and Delete for non-admin user', () => {
    renderCard({ isAdmin: false, isViewer: false });
    fireEvent.click(screen.getByLabelText('Activity actions'));
    expect(screen.queryByText('Reassign')).not.toBeInTheDocument();
    expect(screen.queryByText('Delete')).not.toBeInTheDocument();
  });

  it('shows Reassign and Delete for admin user', () => {
    renderCard({ isAdmin: true, isViewer: false });
    fireEvent.click(screen.getByLabelText('Activity actions'));
    expect(screen.getByText('Reassign')).toBeInTheDocument();
    expect(screen.getByText('Delete')).toBeInTheDocument();
  });

  it('hides ALL mutation controls (menu button) for viewer role', () => {
    renderCard({ isAdmin: false, isViewer: true });
    expect(screen.queryByLabelText('Activity actions')).not.toBeInTheDocument();
  });

  it('admin who is also viewer should still hide controls (isViewer takes precedence)', () => {
    // Unlikely in practice but defensive: if both flags are set, viewer wins
    renderCard({ isAdmin: true, isViewer: true });
    expect(screen.queryByLabelText('Activity actions')).not.toBeInTheDocument();
  });
});

describe('ActivityCard — action callbacks', () => {
  it('calls onEdit when Edit menu item is clicked', () => {
    const onEdit = vi.fn();
    renderCard({ isAdmin: false, isViewer: false, onEdit });
    fireEvent.click(screen.getByLabelText('Activity actions'));
    fireEvent.click(screen.getByText('Edit'));
    expect(onEdit).toHaveBeenCalledWith(BASE);
  });

  it('calls onStatusChange with "completed" when Complete is clicked', () => {
    const onStatusChange = vi.fn();
    renderCard({ isAdmin: false, isViewer: false, onStatusChange });
    fireEvent.click(screen.getByLabelText('Activity actions'));
    fireEvent.click(screen.getByText('Complete'));
    expect(onStatusChange).toHaveBeenCalledWith(BASE, 'completed');
  });

  it('calls onDelete when Delete is clicked (admin)', () => {
    const onDelete = vi.fn();
    renderCard({ isAdmin: true, isViewer: false, onDelete });
    fireEvent.click(screen.getByLabelText('Activity actions'));
    fireEvent.click(screen.getByText('Delete'));
    expect(onDelete).toHaveBeenCalledWith(BASE);
  });
});
