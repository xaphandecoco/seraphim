import type { ReactNode, KeyboardEvent } from 'react';
import { LoadingState, EmptyState } from './StateViews';

export interface Column<T> {
  key: string;
  header: string;
  render?: (row: T) => ReactNode;
  className?: string;
}

interface DataTableProps<T> {
  columns: Column<T>[];
  rows: T[];
  getRowKey: (row: T) => string | number;
  onRowClick?: (row: T) => void;
  isLoading: boolean;
  emptyState?: ReactNode;
  selectedKeys?: Set<string | number>;
  onSelectionChange?: (keys: Set<string | number>) => void;
}

export function DataTable<T>({
  columns,
  rows,
  getRowKey,
  onRowClick,
  isLoading,
  emptyState,
  selectedKeys,
  onSelectionChange,
}: DataTableProps<T>) {
  if (isLoading) {
    return <LoadingState />;
  }

  if (rows.length === 0) {
    return <>{emptyState ?? <EmptyState title="No results" />}</>;
  }

  const getCellValue = (col: Column<T>, row: T): ReactNode => {
    if (col.render) return col.render(row);
    const value = (row as Record<string, unknown>)[col.key];
    return value != null ? String(value) : null;
  };

  const handleKeyPress = (e: KeyboardEvent<HTMLElement>, row: T) => {
    if (onRowClick && (e.key === 'Enter' || e.key === ' ')) {
      e.preventDefault();
      onRowClick(row);
    }
  };

  const toggleKey = (key: string | number) => {
    if (!onSelectionChange) return;
    const next = new Set(selectedKeys ?? []);
    if (next.has(key)) {
      next.delete(key);
    } else {
      next.add(key);
    }
    onSelectionChange(next);
  };

  const hasSelection = !!onSelectionChange;

  return (
    <>
      {/* Desktop table — hidden on mobile */}
      <div className="hidden md:block w-full overflow-x-auto">
        <table className="w-full text-sm text-foreground">
          <thead>
            <tr className="border-b border-border bg-card">
              {hasSelection && (
                <th
                  scope="col"
                  className="w-10 px-3 py-3 text-left"
                  aria-label="Select row"
                />
              )}
              {columns.map((col) => (
                <th
                  key={col.key}
                  scope="col"
                  className={`px-4 py-3 text-left text-xs font-semibold text-foreground/60 uppercase tracking-wide ${col.className ?? ''}`}
                >
                  {col.header}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => {
              const key = getRowKey(row);
              const isChecked = hasSelection && (selectedKeys?.has(key) ?? false);
              return (
                <tr
                  key={key}
                  className={`border-b border-border last:border-0 transition-colors ${
                    onRowClick
                      ? 'cursor-pointer hover:bg-primary/10 focus:outline-none focus:bg-primary/10'
                      : isChecked
                      ? 'bg-primary/5'
                      : ''
                  }`}
                  onClick={onRowClick ? () => onRowClick(row) : undefined}
                  onKeyDown={onRowClick ? (e) => handleKeyPress(e, row) : undefined}
                  tabIndex={onRowClick ? 0 : undefined}
                  role={onRowClick ? 'button' : undefined}
                >
                  {hasSelection && (
                    <td
                      className="w-10 px-3 py-3"
                      onClick={(e) => { e.stopPropagation(); toggleKey(key); }}
                    >
                      <input
                        type="checkbox"
                        checked={isChecked}
                        onChange={() => toggleKey(key)}
                        aria-label="Select row"
                        className="rounded border border-border accent-primary"
                      />
                    </td>
                  )}
                  {columns.map((col) => (
                    <td
                      key={col.key}
                      className={`px-4 py-3 ${col.className ?? ''}`}
                    >
                      {getCellValue(col, row)}
                    </td>
                  ))}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {/* Mobile card list — hidden on desktop */}
      <div className="block md:hidden space-y-2 p-2">
        {rows.map((row) => {
          const key = getRowKey(row);
          const isChecked = hasSelection && (selectedKeys?.has(key) ?? false);
          return (
            <div
              key={key}
              className={`rounded-2xl bg-card border border-border p-4 transition-colors ${
                isChecked ? 'border-primary bg-primary/5' : ''
              } ${
                onRowClick
                  ? 'cursor-pointer hover:bg-primary/10 focus:outline-none focus:bg-primary/10'
                  : ''
              }`}
              onClick={onRowClick ? () => onRowClick(row) : undefined}
              onKeyDown={onRowClick ? (e) => handleKeyPress(e, row) : undefined}
              tabIndex={onRowClick ? 0 : undefined}
              role={onRowClick ? 'button' : undefined}
            >
              {hasSelection && (
                <div
                  className="mb-2 flex items-center gap-2"
                  onClick={(e) => { e.stopPropagation(); toggleKey(key); }}
                >
                  <input
                    type="checkbox"
                    checked={isChecked}
                    onChange={() => toggleKey(key)}
                    aria-label="Select row"
                    className="rounded border border-border accent-primary"
                  />
                  <span className="text-xs text-foreground/50">Select</span>
                </div>
              )}
              {columns.map((col) => (
                <div key={col.key} className="flex justify-between gap-2 py-0.5">
                  <span className="text-xs font-semibold text-foreground/50 shrink-0">
                    {col.header}
                  </span>
                  <span className={`text-xs text-foreground text-right ${col.className ?? ''}`}>
                    {getCellValue(col, row)}
                  </span>
                </div>
              ))}
            </div>
          );
        })}
      </div>
    </>
  );
}
