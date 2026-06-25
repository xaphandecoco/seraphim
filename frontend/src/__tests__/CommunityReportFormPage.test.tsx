/**
 * Tests for CommunityReportFormPage.
 *
 * Covers:
 *   - Form renders
 *   - Submit button exists
 *   - Required fields show validation errors when empty on submit
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';

// --- Mocks ------------------------------------------------------------------

vi.mock('@/services/communityReports', () => ({
  createCommunityReport: vi.fn(),
}));

import { createCommunityReport } from '@/services/communityReports';
import { CommunityReportFormPage } from '@/pages/CommunityReportFormPage';

// ---------- Helpers ----------------------------------------------------------

function mkClient() {
  return new QueryClient({ defaultOptions: { queries: { retry: false } } });
}

function renderPage() {
  const client = mkClient();
  const utils = render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <CommunityReportFormPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return { ...utils, client };
}

// ---------- Tests ------------------------------------------------------------

beforeEach(() => {
  vi.clearAllMocks();
});

describe('CommunityReportFormPage — renders', () => {
  it('renders the page heading', () => {
    renderPage();
    expect(screen.getByRole('heading', { name: /new community report/i })).toBeInTheDocument();
  });

  it('renders the Submit Report button', () => {
    renderPage();
    expect(screen.getByRole('button', { name: /submit report/i })).toBeInTheDocument();
  });

  it('renders the date of activity field', () => {
    renderPage();
    expect(screen.getByLabelText(/date of activity/i)).toBeInTheDocument();
  });

  it('renders the event title field', () => {
    renderPage();
    expect(screen.getByLabelText(/event title/i)).toBeInTheDocument();
  });

  it('renders the attendee names textarea', () => {
    renderPage();
    // The textarea is identified by its sr-only label
    expect(screen.getByLabelText(/attendee names/i)).toBeInTheDocument();
  });
});

describe('CommunityReportFormPage — validation', () => {
  it('shows error when date_of_activity is empty on submit', async () => {
    renderPage();
    const submitBtn = screen.getByRole('button', { name: /submit report/i });
    fireEvent.click(submitBtn);
    await waitFor(() => {
      expect(screen.getByText(/date of activity is required/i)).toBeInTheDocument();
    });
    expect(createCommunityReport).not.toHaveBeenCalled();
  });

  it('shows error when both event title and attendee list are empty on submit', async () => {
    renderPage();
    // Fill date but leave event_title and attendee_names empty
    const dateInput = screen.getByLabelText(/date of activity/i);
    fireEvent.change(dateInput, { target: { value: '2026-06-25' } });
    const submitBtn = screen.getByRole('button', { name: /submit report/i });
    fireEvent.click(submitBtn);
    await waitFor(() => {
      expect(screen.getByText(/provide an event title or an attendee list/i)).toBeInTheDocument();
    });
    expect(createCommunityReport).not.toHaveBeenCalled();
  });

  it('does not show validation error when required fields are filled', async () => {
    (createCommunityReport as ReturnType<typeof vi.fn>).mockResolvedValue({
      id: 1,
      event_title: 'Test Event',
      date_of_activity: '2026-06-25',
      status: 'submitted',
      match_status: 'pending',
      matched_count: 0,
      review_count: 0,
      created_at: '2026-06-25T00:00:00Z',
    });
    renderPage();
    const dateInput = screen.getByLabelText(/date of activity/i);
    fireEvent.change(dateInput, { target: { value: '2026-06-25' } });
    const titleInput = screen.getByLabelText(/event title/i);
    fireEvent.change(titleInput, { target: { value: 'Test Event' } });
    const submitBtn = screen.getByRole('button', { name: /submit report/i });
    fireEvent.click(submitBtn);
    await waitFor(() => {
      expect(createCommunityReport).toHaveBeenCalledWith(
        expect.objectContaining({ date_of_activity: '2026-06-25', event_title: 'Test Event' }),
      );
    });
  });
});
