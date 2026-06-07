/**
 * Frontend component tests for the Attendees page "Sync now" button (I4).
 *
 * Acceptance criteria (sprint plan, story I4) mapped to tests:
 *   - Admin-only Sync button is rendered for admins, hidden for non-admins.
 *   - Clicking Sync POSTs to /members/sync (API contract endpoint #1).
 *   - On success: invalidateQueries({ queryKey: ['attendees'] }) is called,
 *     then toast.success fires.
 *   - On failure: toast.error surfaces err.response?.data?.detail
 *     (falls back to 'Sync failed').
 *   - The button is disabled + shows the "Syncing…" label while the request
 *     is in flight (RefreshCw spinner state).
 *   - The button carries aria-label="Sync members from CiviCRM".
 *
 * api / sonner / authStore are mocked so no network or store wiring is needed.
 * A real QueryClientProvider wraps the component so useQuery / useQueryClient
 * behave like production (we spy on invalidateQueries on the real client).
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

// --- Mocks -----------------------------------------------------------------
const mockGet = vi.fn();
const mockPost = vi.fn();
vi.mock('@/services/api', () => ({
  api: {
    get: (...a: any[]) => mockGet(...a),
    post: (...a: any[]) => mockPost(...a),
  },
}));

const toastSuccess = vi.fn();
const toastError = vi.fn();
vi.mock('sonner', () => ({
  toast: {
    success: (...a: any[]) => toastSuccess(...a),
    error: (...a: any[]) => toastError(...a),
  },
}));

// authStore is a selector hook: useAuthStore((s) => s.isAdmin). The mock returns
// the selector applied to a fake state whose isAdmin flag we control per test.
let mockIsAdmin = true;
vi.mock('@/store/authStore', () => ({
  useAuthStore: (selector: any) => selector({ isAdmin: mockIsAdmin }),
}));

import { AttendeesPage } from './AttendeesPage';

// Each test gets its own QueryClient so cache/invalidation state never leaks.
function renderWithClient() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const invalidateSpy = vi.spyOn(queryClient, 'invalidateQueries');
  const utils = render(
    <QueryClientProvider client={queryClient}>
      <AttendeesPage />
    </QueryClientProvider>
  );
  return { ...utils, queryClient, invalidateSpy };
}

beforeEach(() => {
  mockGet.mockReset();
  mockPost.mockReset();
  toastSuccess.mockReset();
  toastError.mockReset();
  mockIsAdmin = true;
  // The attendees list query fires on mount; return an empty list.
  mockGet.mockResolvedValue({ data: [] });
  mockPost.mockResolvedValue({ data: { message: 'Synced 0 members', synced_count: 0 } });
});

describe('AttendeesPage — Sync now (I4)', () => {
  it('renders the admin-only Sync button for admins with the correct aria-label', async () => {
    renderWithClient();
    const btn = await screen.findByRole('button', {
      name: /sync members from civicrm/i,
    });
    expect(btn).toBeInTheDocument();
    expect(btn).toHaveTextContent(/sync/i);
  });

  it('hides the Sync button for non-admin users', async () => {
    mockIsAdmin = false;
    renderWithClient();
    // Let the page settle (attendees query resolves) before asserting absence.
    await screen.findByRole('heading', { name: /attendees/i });
    expect(
      screen.queryByRole('button', { name: /sync members from civicrm/i })
    ).not.toBeInTheDocument();
  });

  it('POSTs to /members/sync when Sync is clicked', async () => {
    renderWithClient();
    const btn = await screen.findByRole('button', {
      name: /sync members from civicrm/i,
    });
    fireEvent.click(btn);
    await waitFor(() => expect(mockPost).toHaveBeenCalledWith('/members/sync'));
  });

  it('invalidates the attendees query and shows a success toast on success', async () => {
    const { invalidateSpy } = renderWithClient();
    const btn = await screen.findByRole('button', {
      name: /sync members from civicrm/i,
    });
    fireEvent.click(btn);

    await waitFor(() =>
      expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['attendees'] })
    );
    await waitFor(() => expect(toastSuccess).toHaveBeenCalledTimes(1));
    expect(toastError).not.toHaveBeenCalled();
  });

  it('surfaces err.response.data.detail via toast.error on failure', async () => {
    mockPost.mockRejectedValueOnce({
      response: { data: { detail: 'CiviCRM not configured: missing base URL' } },
    });
    renderWithClient();
    const btn = await screen.findByRole('button', {
      name: /sync members from civicrm/i,
    });
    fireEvent.click(btn);

    await waitFor(() =>
      expect(toastError).toHaveBeenCalledWith(
        'CiviCRM not configured: missing base URL'
      )
    );
    expect(toastSuccess).not.toHaveBeenCalled();
  });

  it('falls back to "Sync failed" when the error has no detail', async () => {
    mockPost.mockRejectedValueOnce(new Error('network down'));
    renderWithClient();
    const btn = await screen.findByRole('button', {
      name: /sync members from civicrm/i,
    });
    fireEvent.click(btn);

    await waitFor(() => expect(toastError).toHaveBeenCalledWith('Sync failed'));
  });

  it('disables the button and shows "Syncing…" while the request is in flight', async () => {
    // Hold the POST open so we can observe the in-flight state deterministically.
    let resolvePost: (v: unknown) => void = () => {};
    mockPost.mockReturnValueOnce(
      new Promise((res) => {
        resolvePost = res;
      })
    );
    renderWithClient();
    const btn = await screen.findByRole('button', {
      name: /sync members from civicrm/i,
    });
    fireEvent.click(btn);

    // While the promise is pending the button is disabled and shows Syncing…
    await waitFor(() => expect(btn).toBeDisabled());
    expect(btn).toHaveTextContent(/syncing/i);

    // Resolve → button returns to enabled "Sync".
    resolvePost({ data: { message: 'Synced 0 members', synced_count: 0 } });
    await waitFor(() => expect(btn).not.toBeDisabled());
    expect(btn).toHaveTextContent(/^sync$/i);
  });

  it('does not call the sync endpoint on initial render (only the list query)', async () => {
    renderWithClient();
    await screen.findByRole('button', { name: /sync members from civicrm/i });
    // The list query uses api.get; the sync action uses api.post. No auto-POST.
    expect(mockPost).not.toHaveBeenCalled();
    expect(mockGet).toHaveBeenCalledWith('/members/attendees', {
      params: { search: '', limit: 200 },
    });
  });
});
