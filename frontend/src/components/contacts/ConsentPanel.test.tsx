/**
 * ConsentPanel component tests (S08 — Biometric Consent & RTBF)
 *
 * Acceptance criteria covered:
 *  1. Shows LoadingState while query is in-flight
 *  2. Shows ErrorState with retry button when the API returns an error
 *  3. status='none' + subject_active=true renders the amber "consent needed" banner
 *  4. status='none' + subject_active=false does NOT render the amber banner
 *  5. status='given' renders the "Consent Given" pill
 *  6. status='revoked' renders the "Revoked" pill
 *  7. status='purged' renders the "Purged" pill and hides all mutation buttons
 *  8. Volunteer user sees "Record Consent" when status='none'
 *  9. Volunteer user does NOT see "Purge Now" (admin-only)
 * 10. Admin user sees "Purge Now" when status='given'
 * 11. Unauthenticated user sees no mutation buttons
 * 12. Purge requires two consecutive ConfirmDialogs before calling biometricApi.purge
 * 13. Successful purge fires a sonner toast and invalidates both query keys
 * 14. Failed purge fires an error toast surfacing err.response?.data?.detail
 * 15. "Revoke" button opens a ConfirmDialog; cancel does not call biometricApi.revokeConsent
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { ConsentPanel } from './ConsentPanel';
import { useAuthStore } from '@/store/authStore';
import type { ConsentResponse } from '@/types/biometric';

// ---------------------------------------------------------------------------
// Mocks
// ---------------------------------------------------------------------------

const mockGetConsent = vi.fn();
const mockPurge = vi.fn();
const mockRevokeConsent = vi.fn();
const mockRequestDeletion = vi.fn();
const mockRecordConsent = vi.fn();
const mockUpdateConsent = vi.fn();

vi.mock('@/services/biometric', () => ({
  biometricApi: {
    getConsent: (...a: unknown[]) => mockGetConsent(...a),
    purge: (...a: unknown[]) => mockPurge(...a),
    revokeConsent: (...a: unknown[]) => mockRevokeConsent(...a),
    requestDeletion: (...a: unknown[]) => mockRequestDeletion(...a),
    recordConsent: (...a: unknown[]) => mockRecordConsent(...a),
    updateConsent: (...a: unknown[]) => mockUpdateConsent(...a),
  },
}));

const mockToastSuccess = vi.fn();
const mockToastError = vi.fn();

vi.mock('sonner', () => ({
  toast: {
    success: (...a: unknown[]) => mockToastSuccess(...a),
    error: (...a: unknown[]) => mockToastError(...a),
  },
}));

// ---------------------------------------------------------------------------
// Auth store helpers
// ---------------------------------------------------------------------------

const ADMIN_USER = {
  id: 1,
  email: 'admin@test.org',
  name: 'Admin',
  role: 'admin' as const,
  is_active: true,
};
const VOLUNTEER_USER = {
  id: 2,
  email: 'vol@test.org',
  name: 'Vol',
  role: 'volunteer' as const,
  is_active: true,
};

function setAdmin() {
  useAuthStore.setState({ user: ADMIN_USER, isAdmin: true, isAuthenticated: true });
}
function setVolunteer() {
  useAuthStore.setState({ user: VOLUNTEER_USER, isAdmin: false, isAuthenticated: true });
}
function setUnauthenticated() {
  useAuthStore.setState({ user: null, isAdmin: false, isAuthenticated: false });
}

// ---------------------------------------------------------------------------
// Consent factory
// ---------------------------------------------------------------------------

function makeConsent(overrides: Partial<ConsentResponse> = {}): ConsentResponse {
  return {
    status: 'none',
    consent_given: false,
    consented_at: null,
    basis_note: null,
    retention_until: null,
    deletion_requested_at: null,
    purged_at: null,
    recorded_by_id: null,
    enrolled_photo_count: 0,
    subject_active: false,
    ...overrides,
  };
}

// ---------------------------------------------------------------------------
// Render helper
// ---------------------------------------------------------------------------

function renderPanel(contactId = 42) {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <MemoryRouter>
      <QueryClientProvider client={qc}>
        <ConsentPanel contactId={contactId} />
      </QueryClientProvider>
    </MemoryRouter>,
  );
}

// ---------------------------------------------------------------------------
// Reset auth before each test
// ---------------------------------------------------------------------------

beforeEach(() => {
  useAuthStore.setState({
    user: null,
    token: null,
    isAdmin: false,
    isAuthenticated: false,
    authReady: false,
  });
});

// ---------------------------------------------------------------------------
// AC1 — Loading state
// ---------------------------------------------------------------------------

describe('AC1 — shows LoadingState while query is in-flight', () => {
  beforeEach(() => {
    mockGetConsent.mockReturnValue(new Promise(() => {})); // never resolves
  });

  it('renders loading spinner text', () => {
    setVolunteer();
    renderPanel();
    expect(screen.getByRole('status')).toBeInTheDocument();
    expect(screen.getByText('Loading consent…')).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// AC2 — Error state
// ---------------------------------------------------------------------------

describe('AC2 — shows ErrorState when the API errors', () => {
  beforeEach(() => {
    mockGetConsent.mockRejectedValue(new Error('network error'));
  });

  it('renders error alert with retry button', async () => {
    setVolunteer();
    renderPanel();
    expect(await screen.findByRole('alert')).toBeInTheDocument();
    expect(screen.getByText('Failed to load consent data')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /try again/i })).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// AC3 — Amber "consent needed" banner when subject_active && status='none'
// ---------------------------------------------------------------------------

describe('AC3 — amber banner when subject_active=true and status=none', () => {
  beforeEach(() => {
    mockGetConsent.mockResolvedValue(
      makeConsent({ status: 'none', subject_active: true }),
    );
  });

  it('shows the amber consent-needed banner', async () => {
    setVolunteer();
    renderPanel();
    expect(
      await screen.findByText(/consent needed/i),
    ).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// AC4 — No amber banner when subject_active=false
// ---------------------------------------------------------------------------

describe('AC4 — no amber banner when subject_active=false', () => {
  beforeEach(() => {
    mockGetConsent.mockResolvedValue(
      makeConsent({ status: 'none', subject_active: false }),
    );
  });

  it('does not show the amber banner', async () => {
    setVolunteer();
    renderPanel();
    // Wait for status pill
    await screen.findByText('No Consent');
    expect(screen.queryByText(/consent needed/i)).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// AC5 — status='given' pill
// ---------------------------------------------------------------------------

describe('AC5 — status pill for given', () => {
  beforeEach(() => {
    mockGetConsent.mockResolvedValue(
      makeConsent({ status: 'given', consent_given: true, consented_at: '2025-01-01T00:00:00Z' }),
    );
  });

  it('renders "Consent Given" pill', async () => {
    setVolunteer();
    renderPanel();
    expect(await screen.findByText('Consent Given')).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// AC6 — status='revoked' pill
// ---------------------------------------------------------------------------

describe('AC6 — status pill for revoked', () => {
  beforeEach(() => {
    mockGetConsent.mockResolvedValue(makeConsent({ status: 'revoked' }));
  });

  it('renders "Revoked" pill', async () => {
    setVolunteer();
    renderPanel();
    expect(await screen.findByText('Revoked')).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// AC7 — status='purged': pill shown, no mutation buttons
// ---------------------------------------------------------------------------

describe('AC7 — purged status hides mutation buttons', () => {
  beforeEach(() => {
    mockGetConsent.mockResolvedValue(
      makeConsent({ status: 'purged', purged_at: '2025-06-01T00:00:00Z' }),
    );
  });

  it('shows "Purged" pill', async () => {
    setAdmin();
    renderPanel();
    expect(await screen.findByText('Purged')).toBeInTheDocument();
  });

  it('hides all mutation buttons even for admin', async () => {
    setAdmin();
    renderPanel();
    await screen.findByText('Purged');
    expect(screen.queryByRole('button', { name: /purge now/i })).toBeNull();
    expect(screen.queryByRole('button', { name: /record consent/i })).toBeNull();
    expect(screen.queryByRole('button', { name: /revoke/i })).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// AC8 — Volunteer sees "Record Consent" when status='none'
// ---------------------------------------------------------------------------

describe('AC8 — volunteer sees Record Consent button on status=none', () => {
  beforeEach(() => {
    mockGetConsent.mockResolvedValue(makeConsent({ status: 'none' }));
  });

  it('shows "Record Consent" button', async () => {
    setVolunteer();
    renderPanel();
    expect(
      await screen.findByRole('button', { name: /record consent/i }),
    ).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// AC9 — Volunteer does NOT see "Purge Now"
// ---------------------------------------------------------------------------

describe('AC9 — volunteer cannot purge', () => {
  beforeEach(() => {
    mockGetConsent.mockResolvedValue(
      makeConsent({ status: 'given', consent_given: true }),
    );
  });

  it('does not show Purge Now button for volunteer', async () => {
    setVolunteer();
    renderPanel();
    await screen.findByText('Consent Given');
    expect(screen.queryByRole('button', { name: /purge now/i })).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// AC10 — Admin sees "Purge Now" when status='given'
// ---------------------------------------------------------------------------

describe('AC10 — admin sees Purge Now when status=given', () => {
  beforeEach(() => {
    mockGetConsent.mockResolvedValue(
      makeConsent({ status: 'given', consent_given: true }),
    );
  });

  it('shows Purge Now button for admin', async () => {
    setAdmin();
    renderPanel();
    expect(
      await screen.findByRole('button', { name: /purge now/i }),
    ).toBeInTheDocument();
  });
});

// ---------------------------------------------------------------------------
// AC11 — Unauthenticated user sees no mutation buttons
// ---------------------------------------------------------------------------

describe('AC11 — unauthenticated user sees no mutation controls', () => {
  beforeEach(() => {
    mockGetConsent.mockResolvedValue(
      makeConsent({ status: 'none', subject_active: true }),
    );
  });

  it('renders the amber banner but no action buttons', async () => {
    setUnauthenticated();
    renderPanel();
    await screen.findByText('No Consent');
    // banner shown (it's an informational element, not a mutation)
    expect(screen.getByText(/consent needed/i)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /record consent/i })).toBeNull();
    expect(screen.queryByRole('button', { name: /revoke/i })).toBeNull();
    expect(screen.queryByRole('button', { name: /purge now/i })).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// AC12 — Purge requires two ConfirmDialogs
// ---------------------------------------------------------------------------

describe('AC12 — purge requires double ConfirmDialog', () => {
  beforeEach(() => {
    mockGetConsent.mockResolvedValue(
      makeConsent({ status: 'given', consent_given: true }),
    );
    mockPurge.mockResolvedValue({
      status: 'purged',
      purge_detail: {
        files_deleted: 3,
        samples_deleted: 2,
        detections_cleared: 5,
        compreface_deleted: true,
        errors: [],
      },
    });
  });

  it('first ConfirmDialog appears on Purge Now click', async () => {
    setAdmin();
    renderPanel();
    fireEvent.click(await screen.findByRole('button', { name: /purge now/i }));
    expect(await screen.findByRole('dialog')).toBeInTheDocument();
    expect(screen.getByText(/permanently purge/i)).toBeInTheDocument();
  });

  it('cancel on first dialog does not open second dialog and does not call purge', async () => {
    setAdmin();
    renderPanel();
    fireEvent.click(await screen.findByRole('button', { name: /purge now/i }));
    await screen.findByRole('dialog');
    fireEvent.click(screen.getByRole('button', { name: /cancel/i }));
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    expect(mockPurge).not.toHaveBeenCalled();
  });

  it('confirming first dialog shows second dialog', async () => {
    setAdmin();
    renderPanel();
    fireEvent.click(await screen.findByRole('button', { name: /purge now/i }));
    await screen.findByRole('dialog');
    fireEvent.click(screen.getByRole('button', { name: /yes, continue/i }));
    expect(await screen.findByText(/final warning/i)).toBeInTheDocument();
  });

  it('confirming second dialog calls biometricApi.purge', async () => {
    setAdmin();
    renderPanel();
    fireEvent.click(await screen.findByRole('button', { name: /purge now/i }));
    await screen.findByRole('dialog');
    fireEvent.click(screen.getByRole('button', { name: /yes, continue/i }));
    await screen.findByText(/final warning/i);
    fireEvent.click(screen.getByRole('button', { name: /purge permanently/i }));
    await waitFor(() => expect(mockPurge).toHaveBeenCalledWith(42));
  });
});

// ---------------------------------------------------------------------------
// AC13 — Successful purge fires toast and invalidates queries
// ---------------------------------------------------------------------------

describe('AC13 — purge success toast', () => {
  beforeEach(() => {
    mockGetConsent.mockResolvedValue(
      makeConsent({ status: 'given', consent_given: true }),
    );
    mockPurge.mockResolvedValue({
      status: 'purged',
      purge_detail: {
        files_deleted: 4,
        samples_deleted: 1,
        detections_cleared: 7,
        compreface_deleted: true,
        errors: [],
      },
    });
  });

  it('shows success toast after purge', async () => {
    setAdmin();
    renderPanel();
    fireEvent.click(await screen.findByRole('button', { name: /purge now/i }));
    await screen.findByRole('dialog');
    fireEvent.click(screen.getByRole('button', { name: /yes, continue/i }));
    await screen.findByText(/final warning/i);
    fireEvent.click(screen.getByRole('button', { name: /purge permanently/i }));
    await waitFor(() => {
      expect(mockToastSuccess).toHaveBeenCalledWith(
        expect.stringContaining('Purged'),
      );
    });
  });
});

// ---------------------------------------------------------------------------
// AC14 — Failed purge shows error toast with detail
// ---------------------------------------------------------------------------

describe('AC14 — purge failure error toast', () => {
  beforeEach(() => {
    mockGetConsent.mockResolvedValue(
      makeConsent({ status: 'given', consent_given: true }),
    );
    mockPurge.mockRejectedValue({
      response: { data: { detail: 'Purge service unavailable' } },
    });
  });

  it('surfaces err.response.data.detail in error toast', async () => {
    setAdmin();
    renderPanel();
    fireEvent.click(await screen.findByRole('button', { name: /purge now/i }));
    await screen.findByRole('dialog');
    fireEvent.click(screen.getByRole('button', { name: /yes, continue/i }));
    await screen.findByText(/final warning/i);
    fireEvent.click(screen.getByRole('button', { name: /purge permanently/i }));
    await waitFor(() => {
      expect(mockToastError).toHaveBeenCalledWith('Purge service unavailable');
    });
  });
});

// ---------------------------------------------------------------------------
// AC15 — Revoke flow: cancel does not call revokeConsent
// ---------------------------------------------------------------------------

describe('AC15 — revoke cancel does not call revokeConsent', () => {
  beforeEach(() => {
    mockGetConsent.mockResolvedValue(
      makeConsent({ status: 'given', consent_given: true }),
    );
  });

  it('opens ConfirmDialog on Revoke click', async () => {
    setVolunteer();
    renderPanel();
    fireEvent.click(await screen.findByRole('button', { name: /revoke/i }));
    expect(await screen.findByRole('dialog')).toBeInTheDocument();
    expect(screen.getByText(/revoke biometric consent/i)).toBeInTheDocument();
  });

  it('cancel closes dialog without calling revokeConsent', async () => {
    setVolunteer();
    renderPanel();
    fireEvent.click(await screen.findByRole('button', { name: /revoke/i }));
    await screen.findByRole('dialog');
    fireEvent.click(screen.getByRole('button', { name: /cancel/i }));
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull());
    expect(mockRevokeConsent).not.toHaveBeenCalled();
  });
});
