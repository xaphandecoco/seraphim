/**
 * Tests for ContactsPage.
 *
 * Covers:
 *   - Renders header "Contacts" + "New Contact" button
 *   - Renders rows from the contacts list API
 *   - Pagination controls render and respond to clicks
 *   - Empty state renders when no contacts returned
 *   - Filter chips render and toggle
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';

// --- Mocks ------------------------------------------------------------------

// useAuthStore is called with a selector: useAuthStore((s) => s.isAdmin)
// The mock must apply the selector to the state object.
vi.mock('@/store/authStore', () => ({
  useAuthStore: vi.fn((selector: (s: { isAdmin: boolean }) => unknown) =>
    selector({ isAdmin: false }),
  ),
}));

vi.mock('@/services/contacts', () => ({
  contactsApi: {
    list: vi.fn(),
    get: vi.fn(),
    create: vi.fn(),
    update: vi.fn(),
    delete: vi.fn(),
    restore: vi.fn(),
    getAttendance: vi.fn(),
  },
}));

import { contactsApi } from '@/services/contacts';
import { ContactsPage } from './ContactsPage';

// ---------- Helpers ----------------------------------------------------------

function mkClient() {
  return new QueryClient({ defaultOptions: { queries: { retry: false } } });
}

function renderPage() {
  const client = mkClient();
  const utils = render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <ContactsPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return { ...utils, client };
}

const emptyResponse = { total: 0, page: 1, page_size: 25, items: [] };

const sampleItems = [
  {
    id: 1,
    display_name: 'Juan dela Cruz',
    nickname: 'Juan',
    contact_type: 'individual',
    contact_subtype: null,
    tier: 'tier0',
    is_regular: true,
    is_connected: false,
    email: 'juan@example.com',
    phone: '09171234567',
    is_deleted: false,
  },
  {
    id: 2,
    display_name: 'Maria Santos',
    nickname: null,
    contact_type: 'individual',
    contact_subtype: null,
    tier: null,
    is_regular: false,
    is_connected: null,
    email: null,
    phone: null,
    is_deleted: false,
  },
];

// ---------- Tests ------------------------------------------------------------

beforeEach(() => {
  vi.clearAllMocks();
  (contactsApi.list as ReturnType<typeof vi.fn>).mockResolvedValue(emptyResponse);
});

describe('ContactsPage — header', () => {
  it('renders the "Contacts" heading', async () => {
    renderPage();
    expect(await screen.findByRole('heading', { name: /contacts/i })).toBeInTheDocument();
  });

  it('renders a "New Contact" button', async () => {
    renderPage();
    expect(await screen.findByRole('button', { name: /new contact/i })).toBeInTheDocument();
  });
});

describe('ContactsPage — list', () => {
  it('shows empty state when no contacts returned', async () => {
    (contactsApi.list as ReturnType<typeof vi.fn>).mockResolvedValue(emptyResponse);
    renderPage();
    expect(await screen.findByText(/no contacts found/i)).toBeInTheDocument();
  });

  it('renders contact rows when list is populated', async () => {
    (contactsApi.list as ReturnType<typeof vi.fn>).mockResolvedValue({
      total: 2,
      page: 1,
      page_size: 25,
      items: sampleItems,
    });
    renderPage();
    // DataTable renders both desktop table and mobile cards, so multiple matches are expected
    expect((await screen.findAllByText('Juan dela Cruz')).length).toBeGreaterThan(0);
    expect((await screen.findAllByText('Maria Santos')).length).toBeGreaterThan(0);
  });

  it('shows email in row', async () => {
    (contactsApi.list as ReturnType<typeof vi.fn>).mockResolvedValue({
      total: 1,
      page: 1,
      page_size: 25,
      items: [sampleItems[0]],
    });
    renderPage();
    expect((await screen.findAllByText('juan@example.com')).length).toBeGreaterThan(0);
  });
});

describe('ContactsPage — pagination', () => {
  it('renders pagination when total > 0', async () => {
    (contactsApi.list as ReturnType<typeof vi.fn>).mockResolvedValue({
      total: 50,
      page: 1,
      page_size: 25,
      items: sampleItems,
    });
    renderPage();
    expect(await screen.findByRole('button', { name: /next page/i })).toBeInTheDocument();
    expect(await screen.findByRole('button', { name: /previous page/i })).toBeInTheDocument();
  });

  it('calls list with page=2 after clicking Next', async () => {
    (contactsApi.list as ReturnType<typeof vi.fn>).mockResolvedValue({
      total: 50,
      page: 1,
      page_size: 25,
      items: sampleItems,
    });
    renderPage();
    const next = await screen.findByRole('button', { name: /next page/i });
    fireEvent.click(next);
    await waitFor(
      () => {
        const calls = (contactsApi.list as ReturnType<typeof vi.fn>).mock.calls;
        const pages = calls.map((c) => (c[0] as { page?: number }).page);
        expect(pages).toContain(2);
      },
      { timeout: 3000 },
    );
  });
});

describe('ContactsPage — filter chips', () => {
  it('renders filter chip buttons', async () => {
    renderPage();
    await screen.findByRole('heading', { name: /contacts/i });
    expect(screen.getByRole('button', { name: /individual/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /regular/i })).toBeInTheDocument();
  });

  it('toggles individual chip aria-pressed state', async () => {
    renderPage();
    const chip = await screen.findByRole('button', { name: /individual/i });
    expect(chip).toHaveAttribute('aria-pressed', 'false');
    fireEvent.click(chip);
    expect(chip).toHaveAttribute('aria-pressed', 'true');
    fireEvent.click(chip);
    expect(chip).toHaveAttribute('aria-pressed', 'false');
  });
});

describe('ContactsPage — admin "Include Deleted" toggle', () => {
  it('does not show Include Deleted for non-admin', async () => {
    renderPage();
    await screen.findByRole('heading', { name: /contacts/i });
    expect(screen.queryByRole('button', { name: /include deleted/i })).toBeNull();
  });
});
