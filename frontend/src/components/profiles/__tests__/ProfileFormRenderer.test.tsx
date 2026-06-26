/**
 * Tests for ProfileFormRenderer — schema-driven form renderer.
 *
 * §8.3 acceptance criteria:
 *   - Required fields display asterisk
 *   - Inline validation error for empty required field on submit
 *   - Honeypot field is visually hidden when isPublic=true
 *   - onSubmit called with correct values when form is valid
 *   - contact_reference field rendered as free-text input
 *   - Fields grouped by section correctly
 */

import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { ProfileFormRenderer } from '../ProfileFormRenderer';
import type { ResolvedField, ProfileSettings, PublicProfileSchema } from '@/types/profile';

// ---------- Fixtures ----------------------------------------------------------

const baseSettings: ProfileSettings = {
  submit_label: 'Register',
  success_message: 'Thank you!',
  notify_google_chat: true,
  notify_gmail: true,
};

function makeField(overrides: Partial<ResolvedField>): ResolvedField {
  return {
    id: 'core:first_name',
    field_type: 'core',
    label: 'First Name',
    is_required: false,
    weight: 0,
    section: null,
    core_field: 'first_name',
    data_type: 'text',
    ...overrides,
  };
}

function makeSchema(fields: ResolvedField[]): PublicProfileSchema {
  return { name: 'Test Profile', settings: baseSettings, fields };
}

// ---------- Tests -------------------------------------------------------------

describe('ProfileFormRenderer — required asterisk', () => {
  it('renders required asterisk for required fields', () => {
    const schema = makeSchema([
      makeField({ id: 'core:first_name', label: 'First Name', is_required: true, core_field: 'first_name' }),
      makeField({ id: 'core:last_name', label: 'Last Name', is_required: false, core_field: 'last_name', weight: 10 }),
    ]);
    render(
      <ProfileFormRenderer schema={schema} onSubmit={vi.fn()} />,
    );
    // Required field has aria-hidden asterisk (*) via FormField
    const firstNameLabel = screen.getByText('First Name').closest('label');
    expect(firstNameLabel).toBeInTheDocument();
    // The asterisk span is inside the label
    expect(firstNameLabel?.textContent).toContain('*');
    // Non-required field should not have asterisk
    const lastNameLabel = screen.getByText('Last Name').closest('label');
    expect(lastNameLabel?.textContent).not.toContain('*');
  });
});

describe('ProfileFormRenderer — inline validation error', () => {
  it('shows inline error for empty required field on submit attempt', async () => {
    const onSubmit = vi.fn();
    const schema = makeSchema([
      makeField({
        id: 'core:first_name',
        label: 'First Name',
        is_required: true,
        core_field: 'first_name',
      }),
    ]);

    render(<ProfileFormRenderer schema={schema} onSubmit={onSubmit} />);

    const submitBtn = screen.getByRole('button', { name: /register/i });
    fireEvent.click(submitBtn);

    await waitFor(() => {
      expect(screen.getByText(/first name is required/i)).toBeInTheDocument();
    });
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it('clears error after user starts typing', async () => {
    const schema = makeSchema([
      makeField({ id: 'core:first_name', label: 'First Name', is_required: true, core_field: 'first_name' }),
    ]);
    render(<ProfileFormRenderer schema={schema} onSubmit={vi.fn()} />);

    fireEvent.click(screen.getByRole('button', { name: /register/i }));
    await waitFor(() => {
      expect(screen.getByText(/first name is required/i)).toBeInTheDocument();
    });

    const input = screen.getByRole('textbox', { name: /first name/i });
    fireEvent.change(input, { target: { value: 'Maria' } });

    await waitFor(() => {
      expect(screen.queryByText(/first name is required/i)).toBeNull();
    });
  });
});

describe('ProfileFormRenderer — honeypot', () => {
  it('honeypot input is NOT present when isPublic is false', () => {
    const schema = makeSchema([
      makeField({ id: 'core:first_name', label: 'First Name', is_required: false, core_field: 'first_name' }),
    ]);
    render(<ProfileFormRenderer schema={schema} onSubmit={vi.fn()} isPublic={false} />);
    const honeypot = document.querySelector('input[name="website"]');
    expect(honeypot).toBeNull();
  });

  it('honeypot input is visually hidden when isPublic=true', () => {
    const schema = makeSchema([
      makeField({ id: 'core:first_name', label: 'First Name', is_required: false, core_field: 'first_name' }),
    ]);
    render(<ProfileFormRenderer schema={schema} onSubmit={vi.fn()} isPublic={true} />);

    const honeypot = document.querySelector('input[name="website"]') as HTMLInputElement;
    expect(honeypot).not.toBeNull();
    // Hidden via parent wrapper with className="hidden"
    const wrapper = honeypot.closest('.hidden');
    expect(wrapper).not.toBeNull();
    // tabIndex must be -1
    expect(honeypot.tabIndex).toBe(-1);
    // autoComplete must be "off"
    expect(honeypot.autocomplete).toBe('off');
  });
});

describe('ProfileFormRenderer — onSubmit called with values', () => {
  it('calls onSubmit with form values when all required fields are filled', async () => {
    const onSubmit = vi.fn().mockResolvedValue(undefined);
    const schema = makeSchema([
      makeField({ id: 'core:first_name', label: 'First Name', is_required: true, core_field: 'first_name', weight: 0 }),
      makeField({ id: 'core:last_name', label: 'Last Name', is_required: true, core_field: 'last_name', weight: 10 }),
    ]);

    render(<ProfileFormRenderer schema={schema} onSubmit={onSubmit} />);

    fireEvent.change(screen.getByRole('textbox', { name: /first name/i }), {
      target: { value: 'Maria' },
    });
    fireEvent.change(screen.getByRole('textbox', { name: /last name/i }), {
      target: { value: 'Santos' },
    });

    fireEvent.click(screen.getByRole('button', { name: /register/i }));

    await waitFor(() => {
      expect(onSubmit).toHaveBeenCalledWith(
        expect.objectContaining({ first_name: 'Maria', last_name: 'Santos' }),
      );
    });
  });

  it('includes honeypot value in payload when isPublic=true', async () => {
    const onSubmit = vi.fn().mockResolvedValue(undefined);
    const schema = makeSchema([
      makeField({ id: 'core:first_name', label: 'First Name', is_required: false, core_field: 'first_name' }),
    ]);

    render(<ProfileFormRenderer schema={schema} onSubmit={onSubmit} isPublic />);

    fireEvent.click(screen.getByRole('button', { name: /register/i }));

    await waitFor(() => {
      expect(onSubmit).toHaveBeenCalledWith(
        expect.objectContaining({ website: '' }),
      );
    });
  });
});

describe('ProfileFormRenderer — contact_reference field', () => {
  it('renders contact_reference field as free-text input', () => {
    const schema = makeSchema([
      makeField({
        id: 'custom:invited_by',
        field_type: 'custom',
        label: 'Invited By',
        is_required: false,
        weight: 0,
        data_type: 'contact_reference',
        custom_field_name: 'invited_by',
        core_field: null,
      }),
    ]);

    render(<ProfileFormRenderer schema={schema} onSubmit={vi.fn()} />);

    const input = screen.getByRole('textbox', { name: /invited by/i }) as HTMLInputElement;
    expect(input).toBeInTheDocument();
    expect(input.type).toBe('text');
  });
});

describe('ProfileFormRenderer — section grouping', () => {
  it('renders section headings when fields have different sections', () => {
    const schema = makeSchema([
      makeField({
        id: 'core:first_name',
        label: 'First Name',
        is_required: true,
        section: 'Personal Info',
        weight: 0,
        core_field: 'first_name',
      }),
      makeField({
        id: 'core:last_name',
        label: 'Last Name',
        is_required: false,
        section: 'Visit Info',
        weight: 10,
        core_field: 'last_name',
      }),
    ]);

    render(<ProfileFormRenderer schema={schema} onSubmit={vi.fn()} />);

    expect(screen.getByText('Personal Info')).toBeInTheDocument();
    expect(screen.getByText('Visit Info')).toBeInTheDocument();
  });

  it('does not render "General" section heading (default section is implicit)', () => {
    const schema = makeSchema([
      makeField({ id: 'core:first_name', label: 'First Name', section: null, is_required: false, core_field: 'first_name' }),
    ]);

    render(<ProfileFormRenderer schema={schema} onSubmit={vi.fn()} />);

    expect(screen.queryByText('General')).toBeNull();
  });
});
