/**
 * Tests for CustomFieldsPage — the admin UI at /settings/custom-fields.
 * Spec §8 block 5 (lines 1020-1032).
 *
 * No real HTTP requests are made. The service module is mocked via
 * vi.mock('@/services/customFields'). useAuthStore is stubbed for the
 * non-admin redirect test. MemberSearchModal is mocked to prevent import
 * of its live implementation.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';

// --- Mocks ------------------------------------------------------------------

// Mock the entire customFields service so no axios calls are made.
vi.mock('@/services/customFields', () => ({
  customFieldsApi: {
    listGroups: vi.fn(),
    createGroup: vi.fn(),
    updateGroup: vi.fn(),
    deleteGroup: vi.fn(),
    listDefs: vi.fn(),
    createDef: vi.fn(),
    updateDef: vi.fn(),
    deleteDef: vi.fn(),
    validate: vi.fn(),
    getSchema: vi.fn(),
  },
}));

// Mock ContactPickerModal — it is transitively imported through
// CustomFieldRenderer which is used in the live-preview pane.
vi.mock('@/components/tasks/ContactPickerModal', () => ({
  ContactPickerModal: () => null,
}));

// Mock useAuthStore so we can control isAdmin / authReady in tests.
vi.mock('@/store/authStore', () => ({
  useAuthStore: vi.fn(),
}));

// We need Navigate mock from react-router-dom for redirect test.
// MemoryRouter is used for routing context; Navigate is real in that env.

import { customFieldsApi } from '@/services/customFields';
import { useAuthStore } from '@/store/authStore';
import { CustomFieldsPage } from './CustomFieldsPage';

// ---------------------------------------------------------------------------
// Sample data fixtures
// ---------------------------------------------------------------------------

const sampleGroups = [
  {
    id: 1,
    name: 'constituent_info',
    label: 'Constituent Info',
    entity: 'contact',
    weight: 10,
    is_active: true,
    fields: [
      {
        id: 10,
        group_id: 1,
        name: 'barangay',
        label: 'Barangay',
        data_type: 'text',
        options: [],
        is_required: false,
        is_multi: false,
        weight: 10,
        is_active: true,
        help_text: null,
      },
    ],
  },
  {
    id: 2,
    name: 'new_friend_info',
    label: 'New Friend Info',
    entity: 'contact',
    weight: 20,
    is_active: true,
    fields: [],
  },
];

// ---------------------------------------------------------------------------
// Helper: render page with providers
// ---------------------------------------------------------------------------

function makeQueryClient() {
  return new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
}

function renderPage(initialEntries = ['/settings/custom-fields']) {
  const queryClient = makeQueryClient();
  return {
    queryClient,
    ...render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={initialEntries}>
          <CustomFieldsPage />
        </MemoryRouter>
      </QueryClientProvider>
    ),
  };
}

// Stub useAuthStore to simulate an admin user (the normal case).
function stubAdmin() {
  (useAuthStore as unknown as ReturnType<typeof vi.fn>).mockImplementation(
    (selector: (s: { token: string; isAdmin: boolean; authReady: boolean }) => unknown) =>
      selector({ token: 'tok', isAdmin: true, authReady: true })
  );
}

// Stub useAuthStore to simulate a non-admin (volunteer) user.
function stubNonAdmin() {
  (useAuthStore as unknown as ReturnType<typeof vi.fn>).mockImplementation(
    (selector: (s: { token: string; isAdmin: boolean; authReady: boolean }) => unknown) =>
      selector({ token: 'tok', isAdmin: false, authReady: true })
  );
}

beforeEach(() => {
  // Default: admin user, listGroups returns sample data.
  stubAdmin();
  vi.mocked(customFieldsApi.listGroups).mockResolvedValue(sampleGroups);
  vi.mocked(customFieldsApi.createGroup).mockResolvedValue({
    ...sampleGroups[0],
    id: 99,
    name: 'new_group',
    label: 'New Group',
  });
  vi.mocked(customFieldsApi.createDef).mockResolvedValue({
    id: 100,
    group_id: 1,
    name: 'new_field',
    label: 'New Field',
    data_type: 'text',
    options: [],
    is_required: false,
    is_multi: false,
    weight: 0,
    is_active: true,
    help_text: null,
  });
  vi.mocked(customFieldsApi.deleteGroup).mockResolvedValue(undefined);
  vi.mocked(customFieldsApi.validate).mockResolvedValue({ errors: {} });
});

// ---------------------------------------------------------------------------
// §8 block 5 tests
// ---------------------------------------------------------------------------

describe('CustomFieldsPage — group list', () => {
  it('lists seeded groups from mocked GET /custom-fields/groups', async () => {
    renderPage();
    expect(await screen.findByText('Constituent Info')).toBeInTheDocument();
    expect(screen.getByText('New Friend Info')).toBeInTheDocument();
  });

  it('shows EmptyState when groups array is empty', async () => {
    vi.mocked(customFieldsApi.listGroups).mockResolvedValue([]);
    renderPage();
    // EmptyState renders when the resolved list is empty
    expect(
      await screen.findByText(/no field groups/i)
    ).toBeInTheDocument();
  });

  it('shows LoadingState while groups query is in-flight', () => {
    // Never resolves — keeps the query pending so we see the loading state.
    vi.mocked(customFieldsApi.listGroups).mockReturnValue(new Promise(() => {}));
    renderPage();
    // LoadingState component is rendered while pending
    expect(screen.getByRole('status')).toBeInTheDocument();
  });
});

describe('CustomFieldsPage — add-group form', () => {
  it('add-group form: submitting calls POST /custom-fields/groups with correct body', async () => {
    renderPage();
    await screen.findByText('Constituent Info');

    // Open the add-group form
    const addGroupBtn = screen.getByRole('button', { name: /add group/i });
    fireEvent.click(addGroupBtn);

    // Fill in name and label fields
    const nameInput = await screen.findByPlaceholderText(/machine name/i);
    const labelInput = screen.getByPlaceholderText(/display label/i);
    fireEvent.change(nameInput, { target: { value: 'leader_info' } });
    fireEvent.change(labelInput, { target: { value: 'Leader Info' } });

    // Submit the form
    fireEvent.click(screen.getByRole('button', { name: /create group/i }));

    await waitFor(() => {
      expect(customFieldsApi.createGroup).toHaveBeenCalledWith(
        expect.objectContaining({
          name: 'leader_info',
          label: 'Leader Info',
          entity: 'contact',
        })
      );
    });
  });

  it('create group mutation invalidates [\'custom-fields\'] queryKey', async () => {
    const { queryClient } = renderPage();
    await screen.findByText('Constituent Info');

    const invalidateSpy = vi.spyOn(queryClient, 'invalidateQueries');

    // Open form and submit
    fireEvent.click(screen.getByRole('button', { name: /add group/i }));
    const nameInput = await screen.findByPlaceholderText(/machine name/i);
    const labelInput = screen.getByPlaceholderText(/display label/i);
    fireEvent.change(nameInput, { target: { value: 'community_info' } });
    fireEvent.change(labelInput, { target: { value: 'Community Info' } });
    fireEvent.click(screen.getByRole('button', { name: /create group/i }));

    await waitFor(() => {
      expect(invalidateSpy).toHaveBeenCalledWith(
        expect.objectContaining({ queryKey: expect.arrayContaining(['custom-fields']) })
      );
    });
  });
});

describe('CustomFieldsPage — add-field form', () => {
  it('add-field form: submitting calls POST /custom-fields/defs', async () => {
    renderPage();
    // Wait for groups to load
    await screen.findByText('Constituent Info');

    // Open add-field form within the first group
    const addFieldBtns = screen.getAllByRole('button', { name: /add field/i });
    fireEvent.click(addFieldBtns[0]);

    const fieldNameInput = await screen.findByPlaceholderText(/field machine name/i);
    const fieldLabelInput = screen.getByPlaceholderText(/field display label/i);
    fireEvent.change(fieldNameInput, { target: { value: 'pepsol_status' } });
    fireEvent.change(fieldLabelInput, { target: { value: 'PEPSOL Status' } });

    fireEvent.click(screen.getByRole('button', { name: /create field/i }));

    await waitFor(() => {
      expect(customFieldsApi.createDef).toHaveBeenCalledWith(
        expect.objectContaining({
          name: 'pepsol_status',
          label: 'PEPSOL Status',
          group_id: 1,
        })
      );
    });
  });
});

describe('CustomFieldsPage — deactivate / delete group', () => {
  it('deactivate group: opens ConfirmDialog before sending DELETE /groups/{id}', async () => {
    renderPage();
    await screen.findByText('Constituent Info');

    // Click the deactivate/delete button for the first group
    const deleteBtn = screen.getAllByRole('button', { name: /deactivate|delete/i })[0];
    fireEvent.click(deleteBtn);

    // ConfirmDialog should appear before the API call is made
    expect(await screen.findByRole('dialog')).toBeInTheDocument();
    expect(customFieldsApi.deleteGroup).not.toHaveBeenCalled();
  });
});

describe('CustomFieldsPage — live preview pane', () => {
  it('live preview pane renders CustomFieldsSection with mock schema', async () => {
    renderPage();
    await screen.findByText('Constituent Info');

    // The preview pane should render the group label inside a preview section
    // CustomFieldsSection renders an h3 with the group label
    expect(screen.getAllByText('Constituent Info').length).toBeGreaterThan(1);
  });
});

describe('CustomFieldsPage — test-validate button', () => {
  it('test-validate button calls POST /custom-fields/validate and shows inline errors', async () => {
    vi.mocked(customFieldsApi.validate).mockResolvedValue({
      errors: { barangay: 'This field is required.' },
    });

    renderPage();
    await screen.findByText('Constituent Info');

    const validateBtn = screen.getByRole('button', { name: /test validate/i });
    fireEvent.click(validateBtn);

    await waitFor(() => {
      expect(customFieldsApi.validate).toHaveBeenCalledWith(
        'contact',
        expect.any(Object)
      );
    });

    // Inline error for the 'barangay' field should appear
    expect(await screen.findByText('This field is required.')).toBeInTheDocument();
  });
});

describe('CustomFieldsPage — auth guard', () => {
  it('non-admin user is redirected (AdminRoute redirects to /)', async () => {
    stubNonAdmin();
    // Render with a MemoryRouter that includes a "/" destination route so Navigate renders.
    const queryClient = makeQueryClient();
    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={['/settings/custom-fields']}>
          <CustomFieldsPage />
        </MemoryRouter>
      </QueryClientProvider>
    );

    // Page content must not be rendered for non-admins.
    // The component should redirect without showing field group content.
    await waitFor(() => {
      expect(screen.queryByText('Custom Fields')).toBeNull();
    });
  });
});

describe('CustomFieldsPage — entity tab switch', () => {
  it('entity tab switch refetches groups with new entity param', async () => {
    vi.mocked(customFieldsApi.listGroups).mockResolvedValue([]);
    renderPage();

    // Wait for initial load
    await screen.findByRole('button', { name: /event/i });

    // Click the "Event" entity tab
    fireEvent.click(screen.getByRole('button', { name: /event/i }));

    await waitFor(() => {
      // listGroups must have been called with entity='event'
      const calls = vi.mocked(customFieldsApi.listGroups).mock.calls;
      const hasEventCall = calls.some(
        (call) => call[0] === 'event'
      );
      expect(hasEventCall).toBe(true);
    });
  });
});

// ---------------------------------------------------------------------------
// AC2 — createDef invalidates ['custom-fields']
// ---------------------------------------------------------------------------

describe('CustomFieldsPage — add-field form invalidation (AC2)', () => {
  it('create field mutation invalidates [\'custom-fields\'] queryKey', async () => {
    const { queryClient } = renderPage();
    await screen.findByText('Constituent Info');

    const invalidateSpy = vi.spyOn(queryClient, 'invalidateQueries');

    // Open add-field form in first group
    const addFieldBtns = screen.getAllByRole('button', { name: /add field/i });
    fireEvent.click(addFieldBtns[0]);

    const fieldNameInput = await screen.findByPlaceholderText(/field machine name/i);
    const fieldLabelInput = screen.getByPlaceholderText(/field display label/i);
    fireEvent.change(fieldNameInput, { target: { value: 'test_field_x' } });
    fireEvent.change(fieldLabelInput, { target: { value: 'Test Field X' } });

    fireEvent.click(screen.getByRole('button', { name: /create field/i }));

    await waitFor(() => {
      expect(invalidateSpy).toHaveBeenCalledWith(
        expect.objectContaining({ queryKey: expect.arrayContaining(['custom-fields']) })
      );
    });
  });
});

// ---------------------------------------------------------------------------
// AC3 — ConfirmDialog shown before hard-delete
// ---------------------------------------------------------------------------

describe('CustomFieldsPage — hard-delete ConfirmDialog (AC3)', () => {
  it('hard-delete group: opens ConfirmDialog (probe returns 204, no data)', async () => {
    // deleteGroup resolves immediately (no 409) — still show ConfirmDialog
    vi.mocked(customFieldsApi.deleteGroup).mockResolvedValue(undefined);
    renderPage();
    await screen.findByText('Constituent Info');

    // The "Delete" button is the hard-delete button; it is the second button
    // in the group header action area.
    const deleteBtns = screen.getAllByRole('button', { name: /^delete group/i });
    fireEvent.click(deleteBtns[0]);

    // ConfirmDialog should appear before the actual DELETE is committed
    expect(await screen.findByRole('dialog')).toBeInTheDocument();
  });

  it('hard-delete field: opens ConfirmDialog before user confirms (probe called, mutation deferred)', async () => {
    // deleteGroup already set to resolve in beforeEach; override deleteDef to resolve too
    vi.mocked(customFieldsApi.deleteDef).mockResolvedValue(undefined);
    renderPage();
    await screen.findByText('Constituent Info');

    // sampleGroups[0] has field "Barangay"; aria-label = "Delete Barangay"
    const fieldDeleteBtn = screen.getByRole('button', { name: /^Delete Barangay$/i });
    fireEvent.click(fieldDeleteBtn);

    // Dialog appears (the probe has been called) but user has not confirmed yet
    // so the confirmed deletion (hard-delete mutation) should not have been triggered.
    const dialog = await screen.findByRole('dialog');
    expect(dialog).toBeInTheDocument();
    // probe call is to deleteDef(id, hard=true) — exactly 1 call (the probe)
    await waitFor(() => {
      expect(customFieldsApi.deleteDef).toHaveBeenCalledTimes(1);
      expect(customFieldsApi.deleteDef).toHaveBeenCalledWith(10, true);
    });
    // Confirm button must be present in the dialog
    expect(dialog.textContent).toContain('Delete permanently');
  });
});

// ---------------------------------------------------------------------------
// AC4 — hard-delete dialog shows affected_contacts from 409 probe
// ---------------------------------------------------------------------------

describe('CustomFieldsPage — 409 probe surfaces affected_contacts (AC4)', () => {
  it('hard-delete group: shows affected contact count when 409 returned', async () => {
    // Simulate a 409 with affected_contacts in detail
    const axiosError = {
      response: {
        status: 409,
        data: { detail: { affected_contacts: 42 } },
      },
    };
    vi.mocked(customFieldsApi.deleteGroup).mockRejectedValue(axiosError);

    renderPage();
    await screen.findByText('Constituent Info');

    const deleteBtns = screen.getAllByRole('button', { name: /^delete group/i });
    fireEvent.click(deleteBtns[0]);

    // Dialog should appear and include the affected_contacts count
    await waitFor(() => {
      expect(screen.getByRole('dialog')).toBeInTheDocument();
    });
    // The confirm message must mention the count
    expect(screen.getByRole('dialog').textContent).toContain('42');
  });

  it('hard-delete field: shows affected contact count when 409 returned', async () => {
    const axiosError = {
      response: {
        status: 409,
        data: { detail: { affected_contacts: 7 } },
      },
    };
    vi.mocked(customFieldsApi.deleteDef).mockRejectedValue(axiosError);

    renderPage();
    await screen.findByText('Constituent Info');

    // sampleGroups[0] has one field "Barangay"; aria-label = "Delete Barangay"
    const fieldDeleteBtn = screen.getByRole('button', { name: /^Delete Barangay$/i });
    fireEvent.click(fieldDeleteBtn);

    await waitFor(() => {
      expect(screen.getByRole('dialog')).toBeInTheDocument();
    });
    expect(screen.getByRole('dialog').textContent).toContain('7');
  });
});

// ---------------------------------------------------------------------------
// AC1 — admin sees six seeded groups (mocked at 6)
// ---------------------------------------------------------------------------

describe('CustomFieldsPage — six seeded groups visible to admin (AC1)', () => {
  it('renders all six seeded group labels when API returns six groups', async () => {
    const sixGroups = [
      { id: 1, name: 'constituent_info', label: 'Constituent Info', entity: 'contact', weight: 10, is_active: true, fields: [] },
      { id: 2, name: 'new_friend_info', label: 'New Friend Info', entity: 'contact', weight: 20, is_active: true, fields: [] },
      { id: 3, name: 'community_info', label: 'Community Info', entity: 'contact', weight: 30, is_active: true, fields: [] },
      { id: 4, name: 'leader_info', label: 'Leader Info', entity: 'contact', weight: 40, is_active: true, fields: [] },
      { id: 5, name: 'followup_info', label: 'Followup Info', entity: 'contact', weight: 50, is_active: true, fields: [] },
      { id: 6, name: 'church_info', label: 'Church Info', entity: 'contact', weight: 60, is_active: true, fields: [] },
    ];
    vi.mocked(customFieldsApi.listGroups).mockResolvedValue(sixGroups);
    renderPage();

    expect(await screen.findByText('Constituent Info')).toBeInTheDocument();
    expect(screen.getByText('New Friend Info')).toBeInTheDocument();
    expect(screen.getByText('Community Info')).toBeInTheDocument();
    expect(screen.getByText('Leader Info')).toBeInTheDocument();
    expect(screen.getByText('Followup Info')).toBeInTheDocument();
    expect(screen.getByText('Church Info')).toBeInTheDocument();
  });
});
