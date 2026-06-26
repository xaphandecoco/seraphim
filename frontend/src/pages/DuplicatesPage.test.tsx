// S11 — DuplicatesPage vitest suite.

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { DuplicatesPage } from './DuplicatesPage';
import { useAuthStore } from '@/store/authStore';
import type { CandidatePair } from '@/types/dedupe';

// ─── Mock service layer ───────────────────────────────────────────────────────

vi.mock('@/services/dedupe', () => ({
  listRuleSets: vi.fn().mockResolvedValue([]),
  findCandidates: vi.fn().mockResolvedValue({ items: [], total: 0, page: 1, page_size: 25 }),
  getMergeHistory: vi.fn().mockResolvedValue({ items: [], total: 0, page: 1, page_size: 25 }),
  mergePreview: vi.fn().mockResolvedValue({
    field_conflicts: {},
    custom_field_conflicts: {},
    reassignments: {},
    same_name: false,
    warnings: [],
  }),
  mergeContacts: vi.fn().mockResolvedValue({ survivor_id: 1, reassignments: {}, warnings: [] }),
  deleteRuleSet: vi.fn().mockResolvedValue(undefined),
  createRuleSet: vi.fn(),
  updateRuleSet: vi.fn(),
}));

import * as dedupeService from '@/services/dedupe';

// ─── Test helpers ─────────────────────────────────────────────────────────────

function makeQueryClient() {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });
}

function renderPage() {
  const client = makeQueryClient();
  const result = render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <DuplicatesPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return result;
}

const mockPairSameName: CandidatePair = {
  contact_a: {
    id: 1,
    first_name: 'Juan',
    last_name: 'Dela Cruz',
    email: 'juan@example.com',
    phone: null,
    participant_count: 10,
    face_sample_count: 2,
  },
  contact_b: {
    id: 2,
    first_name: 'Juan',
    last_name: 'Dela Cruz',
    email: 'juan2@example.com',
    phone: null,
    participant_count: 3,
    face_sample_count: 0,
  },
  score: 100,
  matched_fields: ['email', 'last_name'],
  same_name: true,
};

const mockPairDifferentName: CandidatePair = {
  contact_a: {
    id: 3,
    first_name: 'Maria',
    last_name: 'Santos',
    email: 'maria@example.com',
    phone: null,
    participant_count: 5,
    face_sample_count: 1,
  },
  contact_b: {
    id: 4,
    first_name: 'Mary',
    last_name: 'Santos',
    email: 'mary@example.com',
    phone: null,
    participant_count: 2,
    face_sample_count: 0,
  },
  score: 80,
  matched_fields: ['last_name'],
  same_name: false,
};

// ─── Tests ────────────────────────────────────────────────────────────────────

describe('DuplicatesPage', () => {
  beforeEach(() => {
    useAuthStore.setState({
      user: { id: 1, email: 'vol@test.com', role: 'volunteer', name: 'Volunteer', is_active: true },
      token: 'tok',
      isAdmin: false,
      isVolunteer: true,
      isAuthenticated: true,
      authReady: true,
    });
    vi.mocked(dedupeService.listRuleSets).mockResolvedValue([]);
    vi.mocked(dedupeService.findCandidates).mockResolvedValue({
      items: [],
      total: 0,
      page: 1,
      page_size: 25,
    });
    vi.mocked(dedupeService.getMergeHistory).mockResolvedValue({
      items: [],
      total: 0,
      page: 1,
      page_size: 25,
    });
  });

  it('renders the page header', () => {
    renderPage();
    expect(screen.getByText('Find & Merge Duplicates')).toBeInTheDocument();
    expect(screen.getByText('Candidates')).toBeInTheDocument();
  });

  it('shows empty state before running finder', () => {
    renderPage();
    expect(screen.getByText('Run the finder to detect duplicates')).toBeInTheDocument();
  });

  it('hides Rules tab for volunteer role', () => {
    renderPage();
    // Rules tab should not exist for volunteer
    const tabButtons = screen.getAllByRole('button');
    const rulesTab = tabButtons.find((b) => b.textContent?.includes('Rules'));
    expect(rulesTab).toBeUndefined();
  });

  it('shows Rules tab for admin role', () => {
    useAuthStore.setState({
      user: { id: 2, email: 'admin@test.com', role: 'admin', name: 'Admin', is_active: true },
      token: 'tok',
      isAdmin: true,
      isVolunteer: true,
      isAuthenticated: true,
      authReady: true,
    });
    renderPage();
    expect(screen.getByText('Rules')).toBeInTheDocument();
  });

  it('shows same-name chip when same_name is true', async () => {
    vi.mocked(dedupeService.findCandidates).mockResolvedValue({
      items: [mockPairSameName],
      total: 1,
      page: 1,
      page_size: 25,
    });

    renderPage();
    fireEvent.click(screen.getByRole('button', { name: /find duplicates/i }));

    // DataTable renders both desktop and mobile copies; use getAllBy* to handle duplicates
    await waitFor(() => {
      const chips = screen.getAllByText(/same name — verify/i);
      expect(chips.length).toBeGreaterThan(0);
    }, { timeout: 5000 });
  });

  it('renders candidate list with score badge and matched-field chips', async () => {
    vi.mocked(dedupeService.findCandidates).mockResolvedValue({
      items: [mockPairDifferentName],
      total: 1,
      page: 1,
      page_size: 25,
    });

    renderPage();
    fireEvent.click(screen.getByRole('button', { name: /find duplicates/i }));

    // DataTable renders both desktop and mobile copies; use getAllBy* to handle duplicates
    await waitFor(() => {
      const nameEls = screen.getAllByText('Maria Santos');
      expect(nameEls.length).toBeGreaterThan(0);
    }, { timeout: 5000 });

    // Score badge (appears twice — desktop + mobile)
    expect(screen.getAllByText('80').length).toBeGreaterThan(0);
    // Matched field chip
    expect(screen.getAllByText('last name').length).toBeGreaterThan(0);
  });

  it('shows EmptyState when no candidates returned', async () => {
    vi.mocked(dedupeService.findCandidates).mockResolvedValue({
      items: [],
      total: 0,
      page: 1,
      page_size: 25,
    });

    renderPage();
    fireEvent.click(screen.getByRole('button', { name: /find duplicates/i }));

    await waitFor(() => {
      expect(screen.getByText('No likely duplicates found')).toBeInTheDocument();
    }, { timeout: 5000 });
  });

  it('shows History empty state when no merges exist', async () => {
    renderPage();
    fireEvent.click(screen.getByText('History'));

    await waitFor(() => {
      expect(screen.getByText('No merges yet')).toBeInTheDocument();
    }, { timeout: 5000 });
  });
});
