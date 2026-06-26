/**
 * Tests for WelcomePage — public newcomer form.
 *
 * §8.3 acceptance criteria:
 *   - Renders without auth token
 *   - Shows loading state while fetching schema
 *   - Shows success message after submit
 *   - Shows error toast on API failure
 *   - 429 rate limit shows user-friendly message
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';

// ---------- Mocks ------------------------------------------------------------

// Mock the hooks so we can control loading / data states
vi.mock('@/hooks/useProfiles', () => ({
  usePublicNewcomerProfile: vi.fn(),
}));

// Mock ProfileFormRenderer to a simple stub so WelcomePage tests focus on
// page-level behaviour (loading, success, toasts) rather than form internals.
vi.mock('@/components/profiles/ProfileFormRenderer', () => ({
  ProfileFormRenderer: ({
    onSubmit,
    submitLabel,
  }: {
    onSubmit: (values: Record<string, unknown>) => Promise<void>;
    submitLabel?: string;
  }) => (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        onSubmit({ first_name: 'Maria', last_name: 'Santos' });
      }}
      aria-label="Profile form"
    >
      <button type="submit">{submitLabel ?? 'Submit'}</button>
    </form>
  ),
}));

// Mock publicNewcomerApi submit function
vi.mock('@/services/profilesApi', () => ({
  publicNewcomerApi: {
    getProfile: vi.fn(),
    submit: vi.fn(),
  },
  profilesApi: {
    list: vi.fn(),
    get: vi.fn(),
    create: vi.fn(),
    update: vi.fn(),
    delete: vi.fn(),
    render: vi.fn(),
  },
}));

// Mock sonner toast
const mockToastError = vi.fn();
const mockToastInfo = vi.fn();
vi.mock('sonner', () => ({
  toast: {
    error: (...a: unknown[]) => mockToastError(...a),
    info: (...a: unknown[]) => mockToastInfo(...a),
    success: vi.fn(),
  },
}));

// Lazy-load imports AFTER mocks
import { usePublicNewcomerProfile } from '@/hooks/useProfiles';
import { publicNewcomerApi } from '@/services/profilesApi';
import { WelcomePage } from '../WelcomePage';

// ---------- Fixtures ----------------------------------------------------------

const sampleSchema = {
  name: 'New Friend',
  settings: {
    submit_label: 'Register as New Friend',
    success_message: 'Welcome to the family!',
    redirect_after_submit: null,
    notify_google_chat: true,
    notify_gmail: true,
  },
  fields: [],
};

// ---------- Helpers -----------------------------------------------------------

function mkClient() {
  return new QueryClient({ defaultOptions: { queries: { retry: false } } });
}

function renderPage() {
  const client = mkClient();
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <WelcomePage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

// ---------- Tests -------------------------------------------------------------

beforeEach(() => {
  vi.clearAllMocks();
});

describe('WelcomePage — renders without auth token', () => {
  it('renders the page without requiring authentication', () => {
    // No auth store mock — the WelcomePage should not check auth
    (usePublicNewcomerProfile as ReturnType<typeof vi.fn>).mockReturnValue({
      data: sampleSchema,
      isLoading: false,
      isError: false,
      refetch: vi.fn(),
    });

    renderPage();

    // The form profile name is shown
    expect(screen.getByText('New Friend')).toBeInTheDocument();
    // Submit button is rendered (from mock ProfileFormRenderer)
    expect(screen.getByRole('button', { name: /register as new friend/i })).toBeInTheDocument();
  });
});

describe('WelcomePage — loading state', () => {
  it('shows loading state while fetching schema', () => {
    (usePublicNewcomerProfile as ReturnType<typeof vi.fn>).mockReturnValue({
      data: undefined,
      isLoading: true,
      isError: false,
      refetch: vi.fn(),
    });

    renderPage();

    expect(screen.getByText(/loading form/i)).toBeInTheDocument();
  });
});

describe('WelcomePage — success message', () => {
  it('shows success message after successful submit', async () => {
    (usePublicNewcomerProfile as ReturnType<typeof vi.fn>).mockReturnValue({
      data: sampleSchema,
      isLoading: false,
      isError: false,
      refetch: vi.fn(),
    });

    (publicNewcomerApi.submit as ReturnType<typeof vi.fn>).mockResolvedValue({
      contact_id: 1,
      status: 'created',
      message: 'Welcome to the family!',
    });

    renderPage();

    const submitBtn = screen.getByRole('button', { name: /register as new friend/i });
    fireEvent.click(submitBtn);

    await waitFor(() => {
      expect(screen.getByText('Welcome to the family!')).toBeInTheDocument();
    });
  });

  it('shows informational toast for duplicate submission', async () => {
    (usePublicNewcomerProfile as ReturnType<typeof vi.fn>).mockReturnValue({
      data: sampleSchema,
      isLoading: false,
      isError: false,
      refetch: vi.fn(),
    });

    (publicNewcomerApi.submit as ReturnType<typeof vi.fn>).mockResolvedValue({
      contact_id: 1,
      status: 'duplicate',
      message: 'Your information is already recorded.',
    });

    renderPage();

    fireEvent.click(screen.getByRole('button', { name: /register as new friend/i }));

    await waitFor(() => {
      expect(mockToastInfo).toHaveBeenCalledWith('Your information is already recorded.');
    });
  });
});

describe('WelcomePage — error states', () => {
  it('shows error state when schema fetch fails', () => {
    (usePublicNewcomerProfile as ReturnType<typeof vi.fn>).mockReturnValue({
      data: undefined,
      isLoading: false,
      isError: true,
      refetch: vi.fn(),
    });

    renderPage();

    expect(
      screen.getByText(/newcomer form is not available/i),
    ).toBeInTheDocument();
  });

  it('shows error toast on API submission failure', async () => {
    (usePublicNewcomerProfile as ReturnType<typeof vi.fn>).mockReturnValue({
      data: sampleSchema,
      isLoading: false,
      isError: false,
      refetch: vi.fn(),
    });

    (publicNewcomerApi.submit as ReturnType<typeof vi.fn>).mockRejectedValue({
      response: { status: 500, data: { detail: 'Internal server error' } },
    });

    renderPage();

    fireEvent.click(screen.getByRole('button', { name: /register as new friend/i }));

    await waitFor(() => {
      expect(mockToastError).toHaveBeenCalledWith('Internal server error');
    });
  });

  it('shows generic error toast when no detail is provided', async () => {
    (usePublicNewcomerProfile as ReturnType<typeof vi.fn>).mockReturnValue({
      data: sampleSchema,
      isLoading: false,
      isError: false,
      refetch: vi.fn(),
    });

    (publicNewcomerApi.submit as ReturnType<typeof vi.fn>).mockRejectedValue(
      new Error('Network error'),
    );

    renderPage();

    fireEvent.click(screen.getByRole('button', { name: /register as new friend/i }));

    await waitFor(() => {
      expect(mockToastError).toHaveBeenCalledWith('Submission failed');
    });
  });
});

describe('WelcomePage — 429 rate limit', () => {
  it('shows user-friendly rate-limit banner on 429', async () => {
    (usePublicNewcomerProfile as ReturnType<typeof vi.fn>).mockReturnValue({
      data: sampleSchema,
      isLoading: false,
      isError: false,
      refetch: vi.fn(),
    });

    (publicNewcomerApi.submit as ReturnType<typeof vi.fn>).mockRejectedValue({
      response: {
        status: 429,
        data: { detail: 'Rate limit exceeded' },
      },
    });

    renderPage();

    fireEvent.click(screen.getByRole('button', { name: /register as new friend/i }));

    await waitFor(() => {
      expect(
        screen.getByText(/too many submissions/i),
      ).toBeInTheDocument();
    });

    // Also shows toast
    expect(mockToastError).toHaveBeenCalledWith(
      'Too many submissions. Please try again in a minute.',
    );
  });
});
