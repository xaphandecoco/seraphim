import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

// ---- Auth store mock -------------------------------------------------------
vi.mock('@/store/authStore', () => ({
  useAuthStore: vi.fn(
    (
      selector: (s: {
        isAdmin: boolean;
        user: { id: number; role: string } | null;
      }) => unknown,
    ) => selector({ isAdmin: false, user: { id: 1, role: 'volunteer' } }),
  ),
}));

// ---- Search service mock ---------------------------------------------------
vi.mock('@/services/search', () => ({
  searchApi: {
    getFields: vi.fn().mockResolvedValue({
      fields: [
        {
          key: 'display_name',
          label: 'Display Name',
          kind: 'core',
          type: 'string',
          ops: ['eq', 'contains', 'is_set', 'is_empty'],
          nullable: false,
        },
        {
          key: 'tier',
          label: 'Tier',
          kind: 'derived',
          type: 'enum',
          ops: ['eq', 'is_set'],
          options: [{ value: 'tier0', label: 'Tier 0' }],
          nullable: true,
        },
      ],
    }),
    search: vi.fn().mockResolvedValue({
      total: 1,
      page: 1,
      page_size: 25,
      items: [
        {
          id: 1,
          display_name: 'Alice Test',
          contact_type: 'individual',
          tier: 'tier0',
          is_regular: true,
        },
      ],
    }),
    createSavedSearch: vi.fn().mockResolvedValue({
      id: 1,
      name: 'My Search',
      criteria: { logic: 'and', conditions: [] },
      owner_id: 1,
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
    }),
  },
}));

import { AdvancedSearchPage } from './AdvancedSearchPage';

function makeQC() {
  return new QueryClient({ defaultOptions: { queries: { retry: false } } });
}

function renderPage(initialPath = '/contacts/search') {
  const qc = makeQC();
  return {
    qc,
    ...render(
      <QueryClientProvider client={qc}>
        <MemoryRouter initialEntries={[initialPath]}>
          <AdvancedSearchPage />
        </MemoryRouter>
      </QueryClientProvider>,
    ),
  };
}

beforeEach(() => vi.clearAllMocks());

describe('AdvancedSearchPage — initial render', () => {
  it('shows a loading state before field data resolves', () => {
    renderPage();
    expect(screen.getByRole('status')).toBeInTheDocument();
  });

  it('renders the page header after fields load', async () => {
    renderPage();
    await waitFor(() =>
      expect(screen.getByText('Advanced Search')).toBeInTheDocument(),
    );
  });

  it('shows the empty prompt before any search is run', async () => {
    renderPage();
    await waitFor(() =>
      expect(screen.getByText(/build your query above/i)).toBeInTheDocument(),
    );
  });
});

describe('AdvancedSearchPage — controls', () => {
  it('renders a Search button', async () => {
    renderPage();
    await waitFor(() =>
      expect(screen.getByRole('button', { name: /^search$/i })).toBeInTheDocument(),
    );
  });

  it('renders a Save button for a volunteer user', async () => {
    renderPage();
    await waitFor(() =>
      expect(
        screen.getByRole('button', { name: /save search/i }),
      ).toBeInTheDocument(),
    );
  });

  it('calls searchApi.search when Search button is clicked', async () => {
    const { searchApi } = await import('@/services/search');
    renderPage();
    await waitFor(() =>
      screen.getByRole('button', { name: /^search$/i }),
    );
    fireEvent.click(screen.getByRole('button', { name: /^search$/i }));
    await waitFor(() => expect(searchApi.search).toHaveBeenCalled());
  });

  it('displays results after a successful search', async () => {
    renderPage();
    await waitFor(() => screen.getByRole('button', { name: /^search$/i }));
    fireEvent.click(screen.getByRole('button', { name: /^search$/i }));
    // DataTable renders desktop + mobile views — expect at least one match
    await waitFor(() => {
      const matches = screen.getAllByText('Alice Test');
      expect(matches.length).toBeGreaterThan(0);
    });
  });
});

describe('AdvancedSearchPage — save search dialog', () => {
  it('opens the save dialog when Save is clicked', async () => {
    renderPage();
    await waitFor(() =>
      screen.getByRole('button', { name: /save search/i }),
    );
    fireEvent.click(screen.getByRole('button', { name: /save search/i }));
    expect(screen.getByText('Save Search')).toBeInTheDocument();
  });

  it('closes the save dialog when Cancel is clicked', async () => {
    renderPage();
    await waitFor(() =>
      screen.getByRole('button', { name: /save search/i }),
    );
    fireEvent.click(screen.getByRole('button', { name: /save search/i }));
    fireEvent.click(screen.getByRole('button', { name: /cancel/i }));
    await waitFor(() =>
      expect(screen.queryByText('Save Search')).toBeNull(),
    );
  });
});
