import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { DataTable, type Column } from './DataTable';

interface Row {
  id: number;
  name: string;
  status: string;
}

const columns: Column<Row>[] = [
  { key: 'name', header: 'Name' },
  { key: 'status', header: 'Status' },
];

const rows: Row[] = [
  { id: 1, name: 'Alice', status: 'active' },
  { id: 2, name: 'Bob', status: 'inactive' },
];

const getRowKey = (row: Row) => row.id;

describe('DataTable — headers', () => {
  it('renders column headers', () => {
    render(
      <DataTable
        columns={columns}
        rows={rows}
        getRowKey={getRowKey}
        isLoading={false}
      />
    );
    expect(screen.getAllByText('Name').length).toBeGreaterThan(0);
    expect(screen.getAllByText('Status').length).toBeGreaterThan(0);
  });
});

describe('DataTable — rows', () => {
  it('renders row data for each row', () => {
    render(
      <DataTable
        columns={columns}
        rows={rows}
        getRowKey={getRowKey}
        isLoading={false}
      />
    );
    expect(screen.getAllByText('Alice').length).toBeGreaterThan(0);
    expect(screen.getAllByText('Bob').length).toBeGreaterThan(0);
    expect(screen.getAllByText('active').length).toBeGreaterThan(0);
    expect(screen.getAllByText('inactive').length).toBeGreaterThan(0);
  });

  it('uses a custom render function when provided', () => {
    const customColumns: Column<Row>[] = [
      {
        key: 'name',
        header: 'Name',
        render: (row) => <span data-testid={`name-${row.id}`}>{row.name.toUpperCase()}</span>,
      },
    ];
    render(
      <DataTable
        columns={customColumns}
        rows={[{ id: 1, name: 'Alice', status: 'active' }]}
        getRowKey={getRowKey}
        isLoading={false}
      />
    );
    expect(screen.getAllByTestId('name-1').length).toBeGreaterThan(0);
    expect(screen.getAllByText('ALICE').length).toBeGreaterThan(0);
  });
});

describe('DataTable — onRowClick', () => {
  it('fires onRowClick with the full row object when a desktop row is clicked', () => {
    const handleClick = vi.fn();
    render(
      <DataTable
        columns={columns}
        rows={rows}
        getRowKey={getRowKey}
        isLoading={false}
        onRowClick={handleClick}
      />
    );
    // Click the first button-role row in the desktop table (tr[role=button])
    const rowButtons = screen.getAllByRole('button');
    fireEvent.click(rowButtons[0]);
    expect(handleClick).toHaveBeenCalledTimes(1);
    expect(handleClick).toHaveBeenCalledWith(rows[0]);
  });

  it('fires onRowClick on Enter keypress', () => {
    const handleClick = vi.fn();
    render(
      <DataTable
        columns={columns}
        rows={[{ id: 1, name: 'Alice', status: 'active' }]}
        getRowKey={getRowKey}
        isLoading={false}
        onRowClick={handleClick}
      />
    );
    const rowButtons = screen.getAllByRole('button');
    fireEvent.keyDown(rowButtons[0], { key: 'Enter' });
    expect(handleClick).toHaveBeenCalledTimes(1);
  });
});

describe('DataTable — loading state', () => {
  it('renders LoadingState when isLoading is true', () => {
    render(
      <DataTable
        columns={columns}
        rows={[]}
        getRowKey={getRowKey}
        isLoading={true}
      />
    );
    // LoadingState renders a role="status" element
    expect(screen.getByRole('status')).toBeInTheDocument();
  });

  it('does not render table when isLoading is true', () => {
    render(
      <DataTable
        columns={columns}
        rows={rows}
        getRowKey={getRowKey}
        isLoading={true}
      />
    );
    expect(screen.queryByRole('table')).toBeNull();
  });
});

describe('DataTable — empty state', () => {
  it('renders default EmptyState when rows is empty and no emptyState prop', () => {
    render(
      <DataTable
        columns={columns}
        rows={[]}
        getRowKey={getRowKey}
        isLoading={false}
      />
    );
    expect(screen.getByText('No results')).toBeInTheDocument();
  });

  it('renders custom emptyState when provided and rows is empty', () => {
    render(
      <DataTable
        columns={columns}
        rows={[]}
        getRowKey={getRowKey}
        isLoading={false}
        emptyState={<div data-testid="custom-empty">Nothing here yet</div>}
      />
    );
    expect(screen.getByTestId('custom-empty')).toBeInTheDocument();
    expect(screen.getByText('Nothing here yet')).toBeInTheDocument();
  });
});
