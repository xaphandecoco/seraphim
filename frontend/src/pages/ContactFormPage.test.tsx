/**
 * Tests for ContactFormPage.
 *
 * Covers:
 *   - Create mode: renders all form fields, submits to create, maps 422 errors inline
 *   - Edit mode: seeds form from existing contact, submits update
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, Route, Routes } from 'react-router-dom';

// --- Mocks ------------------------------------------------------------------

vi.mock('@/store/authStore', () => ({
  useAuthStore: vi.fn(() => ({ isAdmin: false })),
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

// Mock CustomFieldsSection to keep tests focused on the form itself
vi.mock('@/components/customFields/CustomFieldsSection', () => ({
  CustomFieldsSection: () => <div data-testid="custom-fields-section" />,
}));

// Mock useCustomFieldSchema to return no groups
vi.mock('@/hooks/useCustomFieldSchema', () => ({
  useCustomFieldSchema: vi.fn(() => ({ data: { entity: 'contact', groups: [] } })),
}));

import { contactsApi } from '@/services/contacts';
import { ContactFormPage } from './ContactFormPage';

// ---------- Helpers ----------------------------------------------------------

function mkClient() {
  return new QueryClient({ defaultOptions: { queries: { retry: false } } });
}

function renderCreate() {
  const client = mkClient();
  const utils = render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={['/contacts/new']}>
        <Routes>
          <Route path="/contacts/new" element={<ContactFormPage mode="create" />} />
          <Route path="/contacts/:id" element={<div>Contact Detail</div>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return { ...utils, client };
}

function renderEdit(contactId = '1') {
  const client = mkClient();
  const utils = render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[`/contacts/${contactId}/edit`]}>
        <Routes>
          <Route path="/contacts/:id/edit" element={<ContactFormPage mode="edit" />} />
          <Route path="/contacts/:id" element={<div>Contact Detail</div>} />
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
  external_id: null,
  tier: 'tier0',
  is_active: true,
  is_regular: true,
  is_connected: false,
  is_deleted: false,
  custom_data: {},
  face: null,
};

// ---------- Tests ------------------------------------------------------------

beforeEach(() => {
  vi.clearAllMocks();
  (contactsApi.get as ReturnType<typeof vi.fn>).mockResolvedValue(sampleContact);
});

describe('ContactFormPage — create mode', () => {
  it('renders "New Contact" heading', async () => {
    renderCreate();
    expect(await screen.findByRole('heading', { name: /new contact/i })).toBeInTheDocument();
  });

  it('renders required name fields', async () => {
    renderCreate();
    await screen.findByRole('heading', { name: /new contact/i });
    expect(screen.getByLabelText(/first name/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/last name/i)).toBeInTheDocument();
  });

  it('renders the contact type select with individual default', async () => {
    renderCreate();
    await screen.findByRole('heading', { name: /new contact/i });
    const select = screen.getByLabelText(/contact type/i) as HTMLSelectElement;
    expect(select.value).toBe('individual');
  });

  it('renders Cancel and Create Contact buttons', async () => {
    renderCreate();
    await screen.findByRole('heading', { name: /new contact/i });
    // Multiple "Cancel"-related buttons may exist (back arrow has aria-label "Cancel")
    expect(screen.getAllByRole('button', { name: /cancel/i }).length).toBeGreaterThan(0);
    expect(screen.getByRole('button', { name: /create contact/i })).toBeInTheDocument();
  });

  it('submits form and calls contactsApi.create', async () => {
    (contactsApi.create as ReturnType<typeof vi.fn>).mockResolvedValue({ ...sampleContact, id: 99 });
    renderCreate();

    await screen.findByRole('heading', { name: /new contact/i });

    fireEvent.change(screen.getByLabelText(/first name/i), {
      target: { value: 'Ana' },
    });
    fireEvent.change(screen.getByLabelText(/last name/i), {
      target: { value: 'Reyes' },
    });

    fireEvent.click(screen.getByRole('button', { name: /create contact/i }));

    await waitFor(() => {
      expect(contactsApi.create).toHaveBeenCalledWith(
        expect.objectContaining({ first_name: 'Ana', last_name: 'Reyes' }),
      );
    });
  });

  it('maps 422 errors inline onto form fields', async () => {
    const axiosErr = {
      response: {
        status: 422,
        data: {
          detail: [
            { field: 'email', error: 'Invalid email format' },
            { field: 'phone', error: 'Phone number too short' },
          ],
        },
      },
      isAxiosError: true,
    };
    (contactsApi.create as ReturnType<typeof vi.fn>).mockRejectedValue(axiosErr);

    renderCreate();
    await screen.findByRole('heading', { name: /new contact/i });

    // Fill minimum required fields and submit
    fireEvent.change(screen.getByLabelText(/first name/i), {
      target: { value: 'Test' },
    });
    fireEvent.change(screen.getByLabelText(/last name/i), {
      target: { value: 'User' },
    });
    fireEvent.change(screen.getByLabelText(/email/i), {
      target: { value: 'bad-email' },
    });

    fireEvent.click(screen.getByRole('button', { name: /create contact/i }));

    // Both inline field errors should appear
    expect(await screen.findByText('Invalid email format')).toBeInTheDocument();
    expect(await screen.findByText('Phone number too short')).toBeInTheDocument();
  });
});

describe('ContactFormPage — edit mode', () => {
  it('renders "Edit Contact" heading', async () => {
    renderEdit();
    expect(await screen.findByRole('heading', { name: /edit contact/i })).toBeInTheDocument();
  });

  it('seeds first name from existing contact', async () => {
    renderEdit();
    await screen.findByRole('heading', { name: /edit contact/i });
    // The form seeds after the contact loads
    await waitFor(() => {
      const input = screen.getByLabelText(/first name/i) as HTMLInputElement;
      expect(input.value).toBe('Juan');
    });
  });

  it('renders "Save Changes" button in edit mode', async () => {
    renderEdit();
    expect(await screen.findByRole('button', { name: /save changes/i })).toBeInTheDocument();
  });

  it('submits PATCH with updated values', async () => {
    (contactsApi.update as ReturnType<typeof vi.fn>).mockResolvedValue({ ...sampleContact });
    renderEdit();
    await screen.findByRole('heading', { name: /edit contact/i });

    await waitFor(() => {
      const input = screen.getByLabelText(/first name/i) as HTMLInputElement;
      expect(input.value).toBe('Juan');
    });

    fireEvent.change(screen.getByLabelText(/first name/i), {
      target: { value: 'Juanito' },
    });

    fireEvent.click(screen.getByRole('button', { name: /save changes/i }));

    await waitFor(() => {
      expect(contactsApi.update).toHaveBeenCalledWith(
        1,
        expect.objectContaining({ first_name: 'Juanito' }),
      );
    });
  });
});
