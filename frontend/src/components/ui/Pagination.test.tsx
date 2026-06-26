import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { Pagination } from './Pagination';

describe('Pagination — prev/next disabled boundaries', () => {
  it('Prev button is disabled when page === 1', () => {
    render(
      <Pagination page={1} pageSize={10} total={50} onPageChange={vi.fn()} />
    );
    const prev = screen.getByRole('button', { name: /previous page/i });
    expect(prev).toBeDisabled();
  });

  it('Prev button is enabled when page > 1', () => {
    render(
      <Pagination page={2} pageSize={10} total={50} onPageChange={vi.fn()} />
    );
    const prev = screen.getByRole('button', { name: /previous page/i });
    expect(prev).not.toBeDisabled();
  });

  it('Next button is disabled when page === last page', () => {
    render(
      <Pagination page={5} pageSize={10} total={50} onPageChange={vi.fn()} />
    );
    const next = screen.getByRole('button', { name: /next page/i });
    expect(next).toBeDisabled();
  });

  it('Next button is disabled when total === 0', () => {
    render(
      <Pagination page={1} pageSize={10} total={0} onPageChange={vi.fn()} />
    );
    const next = screen.getByRole('button', { name: /next page/i });
    expect(next).toBeDisabled();
  });

  it('Next button is enabled when page < last page', () => {
    render(
      <Pagination page={2} pageSize={10} total={50} onPageChange={vi.fn()} />
    );
    const next = screen.getByRole('button', { name: /next page/i });
    expect(next).not.toBeDisabled();
  });
});

describe('Pagination — page math', () => {
  it('shows correct Page X of N text', () => {
    render(
      <Pagination page={2} pageSize={10} total={35} onPageChange={vi.fn()} />
    );
    expect(screen.getByText(/page 2 of 4/i)).toBeInTheDocument();
  });

  it('shows correct showing range', () => {
    render(
      <Pagination page={2} pageSize={10} total={35} onPageChange={vi.fn()} />
    );
    expect(screen.getByText(/showing 11.{1,3}20 of 35/i)).toBeInTheDocument();
  });

  it('shows last page range correctly (partial page)', () => {
    render(
      <Pagination page={4} pageSize={10} total={35} onPageChange={vi.fn()} />
    );
    expect(screen.getByText(/showing 31.{1,3}35 of 35/i)).toBeInTheDocument();
  });

  it('shows "No results" when total is 0', () => {
    render(
      <Pagination page={1} pageSize={10} total={0} onPageChange={vi.fn()} />
    );
    expect(screen.getByText(/no results/i)).toBeInTheDocument();
  });

  it('calls onPageChange with page - 1 when Prev is clicked', () => {
    const handler = vi.fn();
    render(
      <Pagination page={3} pageSize={10} total={50} onPageChange={handler} />
    );
    fireEvent.click(screen.getByRole('button', { name: /previous page/i }));
    expect(handler).toHaveBeenCalledWith(2);
  });

  it('calls onPageChange with page + 1 when Next is clicked', () => {
    const handler = vi.fn();
    render(
      <Pagination page={3} pageSize={10} total={50} onPageChange={handler} />
    );
    fireEvent.click(screen.getByRole('button', { name: /next page/i }));
    expect(handler).toHaveBeenCalledWith(4);
  });
});

describe('Pagination — page size change', () => {
  it('does not render page-size select when onPageSizeChange is not provided', () => {
    render(
      <Pagination page={1} pageSize={10} total={50} onPageChange={vi.fn()} />
    );
    expect(screen.queryByRole('combobox')).toBeNull();
  });

  it('renders page-size select when onPageSizeChange is provided', () => {
    render(
      <Pagination
        page={1}
        pageSize={10}
        total={50}
        onPageChange={vi.fn()}
        onPageSizeChange={vi.fn()}
      />
    );
    expect(screen.getByRole('combobox')).toBeInTheDocument();
  });

  it('calls onPageSizeChange with the new size when select changes', () => {
    const sizeHandler = vi.fn();
    render(
      <Pagination
        page={1}
        pageSize={10}
        total={100}
        onPageChange={vi.fn()}
        onPageSizeChange={sizeHandler}
      />
    );
    fireEvent.change(screen.getByRole('combobox'), { target: { value: '25' } });
    expect(sizeHandler).toHaveBeenCalledWith(25);
  });

  it('page-size select shows options 10, 25, 50, 100', () => {
    render(
      <Pagination
        page={1}
        pageSize={10}
        total={100}
        onPageChange={vi.fn()}
        onPageSizeChange={vi.fn()}
      />
    );
    const select = screen.getByRole('combobox') as HTMLSelectElement;
    const optionValues = Array.from(select.options).map((o) => o.value);
    expect(optionValues).toEqual(['10', '25', '50', '100']);
  });
});

describe('Pagination — touch target sizes', () => {
  it('Prev and Next buttons have min-h-[44px] and min-w-[44px] classes', () => {
    const { container } = render(
      <Pagination page={2} pageSize={10} total={50} onPageChange={vi.fn()} />
    );
    const buttons = container.querySelectorAll('button');
    buttons.forEach((btn) => {
      expect(btn.className).toContain('min-h-[44px]');
      expect(btn.className).toContain('min-w-[44px]');
    });
  });
});
