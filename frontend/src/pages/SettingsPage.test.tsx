/**
 * Frontend component tests for the Settings page CompreFace key editor
 * (Self-Review Issue 2 — S3 AC: 'Settings collect both keys, labeled exactly
 * "Detection Service API Key" / "Recognition Service API Key"').
 *
 * Verifies:
 *   - both labeled, masked (type=password) fields appear in the System edit panel
 *   - blank fields are NOT sent in PUT /settings (preserve existing DB value —
 *     avoids writing "********" as a literal key)
 *   - a typed key IS sent
 *   - Cancel clears the typed-but-unsaved key values
 *   - sensitive settings render as ******** in the read-only system list
 *
 * api/toast/router/authStore are mocked so no network or navigation occurs.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

// --- Mocks -----------------------------------------------------------------
const mockGet = vi.fn();
const mockPost = vi.fn();
const mockPut = vi.fn();
const mockDelete = vi.fn();
vi.mock('@/services/api', () => ({
  api: {
    get: (...a: any[]) => mockGet(...a),
    post: (...a: any[]) => mockPost(...a),
    put: (...a: any[]) => mockPut(...a),
    delete: (...a: any[]) => mockDelete(...a),
  },
}));

vi.mock('sonner', () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

vi.mock('react-router-dom', () => ({
  useNavigate: () => vi.fn(),
}));

vi.mock('@/store/authStore', () => ({
  useAuthStore: (selector: any) =>
    selector({
      user: { name: 'Admin', email: 'admin@lightnc.org', role: 'admin' },
      logout: vi.fn(),
    }),
}));

import { SettingsPage } from './SettingsPage';

const SETTINGS_PAYLOAD = {
  settings: [
    {
      key: 'database_url',
      value: { value: 'postgresql://u:p@db:5432/seraphim' },
      category: 'general',
      sensitive: true,
    },
    {
      key: 'redis_url',
      value: { value: 'redis://redis:6379/0' },
      category: 'general',
      sensitive: true,
    },
    {
      key: 'compreface_detect_api_key',
      value: { value: '********' },
      category: 'general',
      sensitive: true,
    },
    {
      key: 'compreface_recognize_api_key',
      value: { value: '********' },
      category: 'general',
      sensitive: true,
    },
  ],
};

beforeEach(() => {
  mockGet.mockReset();
  mockPut.mockReset();
  mockGet.mockImplementation((url: string) => {
    if (url === '/settings') return Promise.resolve({ data: SETTINGS_PAYLOAD });
    if (url === '/cameras') return Promise.resolve({ data: [] });
    return Promise.resolve({ data: {} });
  });
  mockPut.mockResolvedValue({ data: SETTINGS_PAYLOAD });
});

async function openSystemEdit() {
  render(<SettingsPage />);
  // Wait for load to finish (Loading… disappears) and the edit button to appear.
  const editBtn = await screen.findByRole('button', { name: /edit connection settings/i });
  fireEvent.click(editBtn);
}

describe('SettingsPage — CompreFace key editor', () => {
  it('shows both labeled, masked key fields in the system edit panel', async () => {
    await openSystemEdit();
    const detect = await screen.findByLabelText('Detection Service API Key');
    const recognize = screen.getByLabelText('Recognition Service API Key');
    expect(detect).toHaveAttribute('type', 'password');
    expect(recognize).toHaveAttribute('type', 'password');
  });

  it('does NOT send blank keys in PUT /settings (preserve existing DB value)', async () => {
    await openSystemEdit();
    fireEvent.click(await screen.findByRole('button', { name: /^save$/i }));

    await waitFor(() => expect(mockPut).toHaveBeenCalled());
    const body = mockPut.mock.calls[0][1];
    expect(body.settings).not.toHaveProperty('compreface_detect_api_key');
    expect(body.settings).not.toHaveProperty('compreface_recognize_api_key');
  });

  it('sends a typed key in PUT /settings', async () => {
    await openSystemEdit();
    fireEvent.change(await screen.findByLabelText('Detection Service API Key'), {
      target: { value: 'new-detect-key' },
    });
    fireEvent.click(screen.getByRole('button', { name: /^save$/i }));

    await waitFor(() => expect(mockPut).toHaveBeenCalled());
    const body = mockPut.mock.calls[0][1];
    expect(body.settings.compreface_detect_api_key).toBe('new-detect-key');
    // The untouched recognition key must still be omitted.
    expect(body.settings).not.toHaveProperty('compreface_recognize_api_key');
  });

  it('Cancel clears typed-but-unsaved key values', async () => {
    await openSystemEdit();
    const detect = await screen.findByLabelText('Detection Service API Key');
    fireEvent.change(detect, { target: { value: 'typed-then-cancelled' } });
    fireEvent.click(screen.getByRole('button', { name: /^cancel$/i }));

    // Re-open the editor; the field must be empty again.
    fireEvent.click(await screen.findByRole('button', { name: /edit connection settings/i }));
    const reopened = await screen.findByLabelText('Detection Service API Key');
    expect(reopened).toHaveValue('');
  });

  it('renders sensitive settings as ******** in the read-only system list', async () => {
    render(<SettingsPage />);
    // The read-only list shows the key name + a masked value.
    expect(await screen.findByText('compreface_recognize_api_key')).toBeInTheDocument();
    // At least one masked value rendered for the sensitive rows.
    const masked = screen.getAllByText('********');
    expect(masked.length).toBeGreaterThanOrEqual(1);
  });
});
