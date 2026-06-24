/**
 * Frontend component tests for the Attendees page.
 *
 * S01 note: the CiviCRM member-sync button (POST /members/sync) has been
 * removed from this page as part of the CiviCRM excision sprint. Tests for
 * that sync functionality have been removed accordingly.
 *
 * Remaining coverage:
 *   - Page renders the attendees list from GET /members/attendees.
 *   - Empty state renders when the list is empty.
 *   - Header uses the bg-card design token (regression guard).
 *   - No "CiviCRM" text appears anywhere on the page.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

// --- Mocks -----------------------------------------------------------------
const mockGet = vi.fn();
vi.mock('@/services/api', () => ({
  api: {
    get: (...a: any[]) => mockGet(...a),
    post: vi.fn(),
  },
}));

import { AttendeesPage } from './AttendeesPage';

function renderWithClient() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const utils = render(
    <QueryClientProvider client={queryClient}>
      <AttendeesPage />
    </QueryClientProvider>
  );
  return { ...utils, queryClient };
}

beforeEach(() => {
  mockGet.mockReset();
  mockGet.mockResolvedValue({ data: [] });
});

describe('AttendeesPage — attendee list', () => {
  it('shows empty state when no attendees are returned', async () => {
    renderWithClient();
    expect(await screen.findByText(/no attendees found/i)).toBeInTheDocument();
  });

  it('renders attendee cards when list is populated', async () => {
    mockGet.mockResolvedValue({
      data: [
        {
          contact_id: 1,
          first_name: 'Juan',
          last_name: 'dela Cruz',
          nickname: null,
          email: null,
          face_thumbnail_path: null,
          sample_count: 2,
        },
      ],
    });
    renderWithClient();
    expect(await screen.findByText('Juan dela Cruz')).toBeInTheDocument();
    expect(screen.getByText('2 samples')).toBeInTheDocument();
  });

  it('calls GET /members/attendees on mount', async () => {
    renderWithClient();
    await waitFor(() =>
      expect(mockGet).toHaveBeenCalledWith('/members/attendees', {
        params: { search: '', limit: 200 },
      })
    );
  });
});

describe('AttendeesPage — CiviCRM excision (S01)', () => {
  it('renders no element with text "CiviCRM"', async () => {
    renderWithClient();
    await screen.findByRole('heading', { name: /attendees/i });
    expect(screen.queryByText(/civicrm/i)).toBeNull();
  });

  it('does not render a sync button', async () => {
    renderWithClient();
    await screen.findByRole('heading', { name: /attendees/i });
    expect(screen.queryByRole('button', { name: /sync/i })).toBeNull();
  });
});

describe('AttendeesPage — header token (bg-card regression)', () => {
  it('header uses bg-card design token, not hardcoded bg-white', async () => {
    const { container } = renderWithClient();
    await screen.findByRole('heading', { name: /attendees/i });

    const header = container.querySelector('header');
    expect(header).not.toBeNull();
    expect(header?.className).toContain('bg-card');
    expect(header?.className).not.toContain('bg-white');
  });
});
