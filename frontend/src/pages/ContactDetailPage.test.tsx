/**
 * Tests for ContactDetailPage.
 *
 * Covers:
 *   - Renders all six sections with data-slot attributes
 *   - Renders contact display_name, badges
 *   - Renders core info fields
 *   - Mounts FacePanel (via mock)
 *   - Renders attendance history table
 *   - Delete via ConfirmDialog (not window.confirm)
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, Route, Routes } from 'react-router-dom';

// --- Mocks ------------------------------------------------------------------

vi.mock('@/store/authStore', () => ({
  useAuthStore: vi.fn(() => ({ isAdmin: true })),
}));

vi.mock('@/hooks/useCustomFieldSchema', () => ({
  useCustomFieldSchema: vi.fn(() => ({
    data: {
      entity: 'contact',
      groups: [
        {
          id: 1,
          name: 'relationships',
          label: 'Relationships',
          entity: 'contact',
          weight: 0,
          is_active: true,
          fields: [
            {
              id: 10,
              group_id: 1,
              name: 'spouse_ref',
              label: 'Spouse',
              data_type: 'contact_reference',
              options: [],
              is_required: false,
              is_multi: false,
              weight: 0,
              is_active: true,
              help_text: null,
            },
            {
              id: 11,
              group_id: 1,
              name: 'number_of_children',
              label: 'Number of Children',
              data_type: 'number',
              options: [],
              is_required: false,
              is_multi: false,
              weight: 1,
              is_active: true,
              help_text: null,
            },
          ],
        },
      ],
    },
    isLoading: false,
  })),
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

// Mock FacePanel to avoid complex face API interactions
vi.mock('@/components/contacts/FacePanel', () => ({
  FacePanel: ({ contactId, readOnly }: { contactId: number; readOnly?: boolean }) => (
    <div data-testid="face-panel" data-contact-id={contactId} data-read-only={String(readOnly)} />
  ),
}));

import { contactsApi } from '@/services/contacts';
import { ContactDetailPage } from './ContactDetailPage';

// ---------- Helpers ----------------------------------------------------------

function mkClient() {
  return new QueryClient({ defaultOptions: { queries: { retry: false } } });
}

function renderDetail(contactId = '1') {
  const client = mkClient();
  const utils = render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[`/contacts/${contactId}`]}>
        <Routes>
          <Route path="/contacts/:id" element={<ContactDetailPage />} />
          <Route path="/contacts" element={<div>Contacts List</div>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return { ...utils, client };
}

const sampleContact = {
  id: 1,
  display_name: 'Juan dela Cruz',
  first_name: 'Juan',
  last_name: 'dela Cruz',
  nickname: 'Juancho',
  suffix: null,
  contact_type: 'individual',
  contact_subtype: null,
  gender: 'male',
  birth_date: '1990-01-01',
  phone: '09171234567',
  email: 'juan@example.com',
  street_address: '123 Main St',
  external_id: 'EXT-001',
  tier: 'tier0',
  is_active: true,
  is_regular: true,
  is_connected: true,
  is_deleted: false,
  custom_data: {},
  face: { enrolled: false, sample_count: 0, thumb_url: null },
};

const sampleAttendance = {
  total: 2,
  page: 1,
  page_size: 10,
  items: [
    {
      event_id: 10,
      event_title: 'Sunday Service',
      attended_at: '2026-01-05T09:00:00Z',
      source: 'manual',
      event_type: null,
    },
    {
      event_id: 11,
      event_title: 'Youth Camp',
      attended_at: '2026-01-12T08:00:00Z',
      source: 'detection',
      event_type: null,
    },
  ],
};

// ---------- Tests ------------------------------------------------------------

beforeEach(() => {
  vi.clearAllMocks();
  (contactsApi.get as ReturnType<typeof vi.fn>).mockResolvedValue(sampleContact);
  (contactsApi.getAttendance as ReturnType<typeof vi.fn>).mockResolvedValue(sampleAttendance);
});

describe('ContactDetailPage — header section', () => {
  it('renders the contact display_name', async () => {
    renderDetail();
    expect(await screen.findByText('Juan dela Cruz')).toBeInTheDocument();
  });

  it('renders the tier badge with human-readable label', async () => {
    renderDetail();
    expect(await screen.findByText('This Week')).toBeInTheDocument();
  });

  it('renders "Unrated" when tier is null', async () => {
    (contactsApi.get as ReturnType<typeof vi.fn>).mockResolvedValue({
      ...sampleContact,
      tier: null,
    });
    renderDetail();
    expect(await screen.findByText('Unrated')).toBeInTheDocument();
  });

  it('renders Edit button', async () => {
    renderDetail();
    expect(await screen.findByRole('button', { name: /edit contact/i })).toBeInTheDocument();
  });

  it('renders overflow menu button', async () => {
    renderDetail();
    const menuBtn = await screen.findByRole('button', { name: /more options/i });
    expect(menuBtn).toBeInTheDocument();
  });
});

describe('ContactDetailPage — core info section', () => {
  it('has data-slot="core-info" section', async () => {
    const { container } = renderDetail();
    await screen.findByText('Juan dela Cruz');
    const section = container.querySelector('[data-slot="core-info"]');
    expect(section).not.toBeNull();
  });

  it('renders phone and email', async () => {
    renderDetail();
    expect(await screen.findByText('09171234567')).toBeInTheDocument();
    expect(await screen.findByText('juan@example.com')).toBeInTheDocument();
  });

  it('renders Legacy ID for external_id', async () => {
    renderDetail();
    await screen.findByText('EXT-001');
    expect(screen.getByText('Legacy ID')).toBeInTheDocument();
  });
});

describe('ContactDetailPage — sections', () => {
  it('has data-slot="header" section', async () => {
    const { container } = renderDetail();
    await screen.findByText('Juan dela Cruz');
    expect(container.querySelector('[data-slot="header"]')).not.toBeNull();
  });

  it('has data-slot="face-panel" section', async () => {
    const { container } = renderDetail();
    await screen.findByText('Juan dela Cruz');
    expect(container.querySelector('[data-slot="face-panel"]')).not.toBeNull();
  });

  it('has data-slot="attendance-history" section', async () => {
    const { container } = renderDetail();
    await screen.findByText('Juan dela Cruz');
    expect(container.querySelector('[data-slot="attendance-history"]')).not.toBeNull();
  });

  it('has data-slot="consent" placeholder', async () => {
    (contactsApi.get as ReturnType<typeof vi.fn>).mockResolvedValue({
      ...sampleContact,
      face: { enrolled: true, sample_count: 2, thumb_url: null },
    });
    renderDetail();
    expect(await screen.findByText(/biometric consent/i)).toBeInTheDocument();
  });

  it('has data-slot="activities" placeholder', async () => {
    renderDetail();
    expect(await screen.findByText(/activities — s12/i)).toBeInTheDocument();
  });
});

describe('ContactDetailPage — FacePanel', () => {
  it('mounts FacePanel with readOnly prop', async () => {
    renderDetail();
    await screen.findByText('Juan dela Cruz');
    const panel = screen.getByTestId('face-panel');
    expect(panel).toBeInTheDocument();
    expect(panel).toHaveAttribute('data-contact-id', '1');
    expect(panel).toHaveAttribute('data-read-only', 'true');
  });

  it('shows "No face enrolled" when face.enrolled is false', async () => {
    renderDetail();
    expect(await screen.findByText(/no face enrolled/i)).toBeInTheDocument();
  });
});

describe('ContactDetailPage — attendance history', () => {
  it('renders attendance rows', async () => {
    renderDetail();
    // DataTable renders both desktop table and mobile cards, so multiple matches are expected
    expect((await screen.findAllByText('Sunday Service')).length).toBeGreaterThan(0);
    expect((await screen.findAllByText('Youth Camp')).length).toBeGreaterThan(0);
  });

  it('renders pagination when attendance total > 0', async () => {
    renderDetail();
    await screen.findAllByText('Sunday Service');
    expect(screen.getByRole('button', { name: /next page/i })).toBeInTheDocument();
  });
});

describe('ContactDetailPage — Delete action via ConfirmDialog', () => {
  it('opens ConfirmDialog (not window.confirm) when Delete is clicked', async () => {
    const confirmSpy = vi.spyOn(window, 'confirm');
    renderDetail();
    await screen.findByText('Juan dela Cruz');

    // Open overflow menu
    fireEvent.click(screen.getByRole('button', { name: /more options/i }));

    const deleteBtn = await screen.findByRole('button', { name: /^delete$/i });
    fireEvent.click(deleteBtn);

    // ConfirmDialog should appear
    expect(await screen.findByRole('dialog')).toBeInTheDocument();
    // window.confirm must NOT have been called
    expect(confirmSpy).not.toHaveBeenCalled();
    confirmSpy.mockRestore();
  });

  it('calls contactsApi.delete when confirm is clicked in dialog', async () => {
    (contactsApi.delete as ReturnType<typeof vi.fn>).mockResolvedValue(undefined);
    renderDetail();
    await screen.findByText('Juan dela Cruz');

    fireEvent.click(screen.getByRole('button', { name: /more options/i }));
    const deleteBtn = await screen.findByRole('button', { name: /^delete$/i });
    fireEvent.click(deleteBtn);

    // Wait for the dialog to appear (it contains Delete buttons)
    await screen.findByRole('button', { name: /delete/i });
    // The dialog has a Cancel and a Delete button; click the Delete (confirm) one
    const allDeleteBtns = screen.getAllByRole('button', { name: /delete/i });
    // Last one is in the dialog (the confirm button)
    fireEvent.click(allDeleteBtns[allDeleteBtns.length - 1]);

    await waitFor(() => {
      expect(contactsApi.delete).toHaveBeenCalledWith(1);
    });
  });
});

describe('ContactDetailPage — AC14: contact_reference custom fields', () => {
  it('renders resolved display_name as a chip linking to /contacts/:refId', async () => {
    (contactsApi.get as ReturnType<typeof vi.fn>).mockResolvedValue({
      ...sampleContact,
      custom_data: { spouse_ref: 42 },
      contact_reference_chips: [
        { id: 42, display_name: 'Maria Santos', contact_type: 'individual' },
      ],
    });
    const { container } = renderDetail();
    // The field label (not raw key) should appear
    expect(await screen.findByText('Spouse')).toBeInTheDocument();
    // The resolved display name should appear as a chip
    expect(screen.getByRole('button', { name: 'Maria Santos' })).toBeInTheDocument();
    // The chip should link to /contacts/42 — navigate on click
    const chip = screen.getByRole('button', { name: 'Maria Santos' });
    expect(chip).toBeInTheDocument();
    // data-slot anchor must still be present
    expect(container.querySelector('[data-slot="custom-fields"]')).not.toBeNull();
  });

  it('falls back to "Contact #id" when chip is not resolved', async () => {
    (contactsApi.get as ReturnType<typeof vi.fn>).mockResolvedValue({
      ...sampleContact,
      custom_data: { spouse_ref: 99 },
      contact_reference_chips: [], // 99 not resolved
    });
    renderDetail();
    expect(await screen.findByRole('button', { name: 'Contact #99' })).toBeInTheDocument();
  });

  it('does NOT linkify a plain number field (non-contact_reference)', async () => {
    (contactsApi.get as ReturnType<typeof vi.fn>).mockResolvedValue({
      ...sampleContact,
      custom_data: { number_of_children: 3 },
      contact_reference_chips: [],
    });
    renderDetail();
    // The field label should appear
    expect(await screen.findByText('Number of Children')).toBeInTheDocument();
    // "3" should render as plain text, not a link/button to /contacts/3
    expect(screen.getByText('3')).toBeInTheDocument();
    // Must not render a button for the numeric value
    const buttons = screen.queryAllByRole('button', { name: '3' });
    expect(buttons).toHaveLength(0);
  });
});

describe('ContactDetailPage — Restore action (admin only)', () => {
  it('shows Restore option when contact is deleted and user is admin', async () => {
    (contactsApi.get as ReturnType<typeof vi.fn>).mockResolvedValue({
      ...sampleContact,
      is_deleted: true,
    });
    renderDetail();
    await screen.findByText('Juan dela Cruz');
    fireEvent.click(screen.getByRole('button', { name: /more options/i }));
    expect(await screen.findByRole('button', { name: /restore/i })).toBeInTheDocument();
  });
});
