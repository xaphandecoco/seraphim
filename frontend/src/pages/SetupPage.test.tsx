/**
 * Frontend component tests for the Setup wizard CompreFace dual-key fields (F-S3).
 *
 * AC: 'setup wizard collects both keys, labeled exactly "Detection Service API
 * Key" / "Recognition Service API Key"', sent in /setup and /setup/test-services.
 *
 * Also covers the a11y/security self-review fixes:
 *   - the key inputs are type=password (masked)
 *   - the show/hide toggles have accessible names and flip the input type
 *
 * The api module is mocked so no network is hit; useNavigate is stubbed.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

// --- Mocks -----------------------------------------------------------------
const mockGet = vi.fn();
const mockPost = vi.fn();
vi.mock('@/services/api', () => ({
  api: {
    get: (...a: any[]) => mockGet(...a),
    post: (...a: any[]) => mockPost(...a),
  },
}));

const mockNavigate = vi.fn();
vi.mock('react-router-dom', () => ({
  useNavigate: () => mockNavigate,
}));

import { SetupPage } from './SetupPage';

beforeEach(() => {
  mockGet.mockReset();
  mockPost.mockReset();
  mockNavigate.mockReset();
  // /setup/status is called on mount; report setup NOT complete so the wizard renders.
  mockGet.mockResolvedValue({ data: { setup_complete: false } });
  mockPost.mockResolvedValue({ data: {} });
});

async function gotoServicesStep() {
  render(<SetupPage />);
  // Step 1 → Next → Step 2 (Services).
  const next = await screen.findByRole('button', { name: /next/i });
  fireEvent.click(next);
}

describe('SetupPage — CompreFace dual key fields (F-S3)', () => {
  it('renders both service-key fields with the exact AC labels on the Services step', async () => {
    await gotoServicesStep();
    expect(
      await screen.findByLabelText('Detection Service API Key')
    ).toBeInTheDocument();
    expect(
      screen.getByLabelText('Recognition Service API Key')
    ).toBeInTheDocument();
  });

  it('masks both key inputs by default (type=password)', async () => {
    await gotoServicesStep();
    const detect = await screen.findByLabelText('Detection Service API Key');
    const recognize = screen.getByLabelText('Recognition Service API Key');
    expect(detect).toHaveAttribute('type', 'password');
    expect(recognize).toHaveAttribute('type', 'password');
  });

  it('helper text explains CompreFace issues one key per service', async () => {
    await gotoServicesStep();
    expect(
      await screen.findByText(/one API key per service/i)
    ).toBeInTheDocument();
  });

  it('show/hide toggle reveals the Detection key and has an accessible name', async () => {
    await gotoServicesStep();
    const detect = await screen.findByLabelText('Detection Service API Key');
    const toggle = screen.getByRole('button', { name: /show detection service api key/i });
    fireEvent.click(toggle);
    expect(detect).toHaveAttribute('type', 'text');
    // After revealing, the accessible name flips to "Hide ...".
    expect(
      screen.getByRole('button', { name: /hide detection service api key/i })
    ).toBeInTheDocument();
  });

  it('sends both keys in the /setup/test-services payload', async () => {
    await gotoServicesStep();
    fireEvent.change(await screen.findByLabelText('Service URL'), {
      target: { value: 'http://compreface:8000' },
    });
    fireEvent.change(screen.getByLabelText('Detection Service API Key'), {
      target: { value: 'detect-key-123' },
    });
    fireEvent.change(screen.getByLabelText('Recognition Service API Key'), {
      target: { value: 'recognize-key-456' },
    });
    fireEvent.click(screen.getByRole('button', { name: /test services/i }));

    await waitFor(() => {
      expect(mockPost).toHaveBeenCalledWith(
        '/setup/test-services',
        expect.objectContaining({
          compreface_url: 'http://compreface:8000',
          compreface_detect_api_key: 'detect-key-123',
          compreface_recognize_api_key: 'recognize-key-456',
        })
      );
    });
  });
});

describe('SetupPage — CiviCRM excision (S01)', () => {
  it('step 2 (Services) contains no element with text "CiviCRM"', async () => {
    await gotoServicesStep();
    // All text on the page after navigating to step 2 must be free of "CiviCRM"
    expect(screen.queryByText(/civicrm/i)).toBeNull();
  });

  it('service test result panel renders no CiviCRM row', async () => {
    mockPost.mockResolvedValue({
      data: { compreface_ok: true, compreface_message: 'CompreFace reachable' },
    });

    await gotoServicesStep();

    fireEvent.click(screen.getByRole('button', { name: /test services/i }));

    await waitFor(() => {
      expect(screen.getByText('CompreFace reachable')).toBeInTheDocument();
    });

    expect(screen.queryByText(/civicrm/i)).toBeNull();
  });

  it('/setup/test-services payload does not include civicrm_url', async () => {
    await gotoServicesStep();
    fireEvent.click(screen.getByRole('button', { name: /test services/i }));

    await waitFor(() => expect(mockPost).toHaveBeenCalled());

    const [, payload] = mockPost.mock.calls[0];
    expect(payload).not.toHaveProperty('civicrm_url');
  });
});

describe('SetupPage — final submit carries both CompreFace keys', () => {
  it('POST /setup includes both detect + recognize keys', async () => {
    render(<SetupPage />);

    // Step 1 (Database) → Next
    fireEvent.click(await screen.findByRole('button', { name: /next/i }));

    // Step 2 (Services) — fill compreface fields
    fireEvent.change(await screen.findByLabelText('Service URL'), {
      target: { value: 'http://compreface:8000' },
    });
    fireEvent.change(screen.getByLabelText('Detection Service API Key'), {
      target: { value: 'detect-key-123' },
    });
    fireEvent.change(screen.getByLabelText('Recognition Service API Key'), {
      target: { value: 'recognize-key-456' },
    });
    fireEvent.click(screen.getByRole('button', { name: /next/i }));

    // Step 3 (Admin) — set a valid, confirmed password so Next enables
    const pw = 'Str0ng!Passw0rd';
    fireEvent.change(screen.getByPlaceholderText(/Min 12 chars/i), {
      target: { value: pw },
    });
    fireEvent.change(screen.getByPlaceholderText(/Re-enter password/i), {
      target: { value: pw },
    });
    fireEvent.change(screen.getByPlaceholderText(/admin@lightnc.org/i), {
      target: { value: 'admin@lightnc.org' },
    });
    fireEvent.click(screen.getByRole('button', { name: /next/i }));

    // Step 4 (Camera) → Skip to Confirm
    fireEvent.click(await screen.findByRole('button', { name: /^skip$/i }));

    // Step 5 (Confirm) → Complete Setup
    fireEvent.click(await screen.findByRole('button', { name: /complete setup/i }));

    await waitFor(() => {
      const setupCall = mockPost.mock.calls.find((c) => c[0] === '/setup');
      expect(setupCall).toBeTruthy();
      expect(setupCall![1]).toMatchObject({
        compreface_detect_api_key: 'detect-key-123',
        compreface_recognize_api_key: 'recognize-key-456',
      });
    });
  });
});
