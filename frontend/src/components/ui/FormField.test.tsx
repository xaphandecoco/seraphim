import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { FormField } from './FormField';

describe('FormField', () => {
  it('renders the label text', () => {
    render(
      <FormField label="Email address" htmlFor="email">
        <input id="email" type="email" />
      </FormField>
    );
    expect(screen.getByText('Email address')).toBeInTheDocument();
  });

  it('label htmlFor points to the correct id', () => {
    render(
      <FormField label="Email address" htmlFor="email">
        <input id="email" type="email" />
      </FormField>
    );
    const label = screen.getByText('Email address').closest('label');
    expect(label).toHaveAttribute('for', 'email');
  });

  it('renders a red required asterisk when required is true', () => {
    render(
      <FormField label="Name" htmlFor="name" required>
        <input id="name" type="text" />
      </FormField>
    );
    const asterisk = screen.getByText('*');
    expect(asterisk).toBeInTheDocument();
    expect(asterisk.className).toContain('text-red-500');
  });

  it('does not render a required asterisk when required is false/absent', () => {
    render(
      <FormField label="Name" htmlFor="name">
        <input id="name" type="text" />
      </FormField>
    );
    expect(screen.queryByText('*')).toBeNull();
  });

  it('renders children as the control slot', () => {
    render(
      <FormField label="Username" htmlFor="username">
        <input id="username" type="text" data-testid="ctrl" />
      </FormField>
    );
    expect(screen.getByTestId('ctrl')).toBeInTheDocument();
  });

  it('renders hint text as small muted text', () => {
    render(
      <FormField label="Password" htmlFor="pw" hint="At least 8 characters">
        <input id="pw" type="password" />
      </FormField>
    );
    const hint = screen.getByText('At least 8 characters');
    expect(hint).toBeInTheDocument();
    expect(hint.tagName).toBe('P');
    expect(hint.className).toContain('text-foreground/50');
  });

  it('renders error message in text-red-500 when error is provided', () => {
    render(
      <FormField label="Email" htmlFor="em" error="Invalid email">
        <input id="em" type="email" />
      </FormField>
    );
    const error = screen.getByText('Invalid email');
    expect(error).toBeInTheDocument();
    expect(error.className).toContain('text-red-500');
  });

  it('does not render hint when error is also provided', () => {
    render(
      <FormField
        label="Email"
        htmlFor="em"
        hint="Enter your email"
        error="Invalid email"
      >
        <input id="em" type="email" />
      </FormField>
    );
    // Error is shown; hint should be suppressed
    expect(screen.getByText('Invalid email')).toBeInTheDocument();
    expect(screen.queryByText('Enter your email')).toBeNull();
  });

  it('does not render error or hint paragraphs when neither is provided', () => {
    render(
      <FormField label="Name" htmlFor="name">
        <input id="name" type="text" />
      </FormField>
    );
    expect(screen.queryByRole('alert')).toBeNull();
  });
});
