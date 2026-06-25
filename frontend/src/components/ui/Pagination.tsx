const PAGE_SIZE_OPTIONS = [10, 25, 50, 100] as const;

interface PaginationProps {
  page: number;
  pageSize: number;
  total: number;
  onPageChange: (page: number) => void;
  onPageSizeChange?: (size: number) => void;
}

export function Pagination({
  page,
  pageSize,
  total,
  onPageChange,
  onPageSizeChange,
}: PaginationProps) {
  const totalPages = total === 0 ? 1 : Math.ceil(total / pageSize);
  const isPrevDisabled = page === 1;
  const isNextDisabled = page >= totalPages || total === 0;

  const firstItem = total === 0 ? 0 : (page - 1) * pageSize + 1;
  const lastItem = Math.min(page * pageSize, total);

  return (
    <div className="flex flex-wrap items-center justify-between gap-3 px-1 py-2 text-sm text-foreground/70">
      {/* Showing range */}
      <span className="text-xs text-foreground/50">
        {total === 0
          ? 'No results'
          : `Showing ${firstItem}–${lastItem} of ${total}`}
      </span>

      {/* Page info + controls */}
      <div className="flex items-center gap-2">
        {/* Optional page-size selector */}
        {onPageSizeChange && (
          <select
            value={pageSize}
            onChange={(e) => onPageSizeChange(Number(e.target.value))}
            className="min-h-[44px] rounded-xl border border-border bg-background px-2 text-xs text-foreground focus:outline-none focus:ring-2 focus:ring-ring"
            aria-label="Rows per page"
          >
            {PAGE_SIZE_OPTIONS.map((size) => (
              <option key={size} value={size}>
                {size} / page
              </option>
            ))}
          </select>
        )}

        <span className="text-xs text-foreground/60">
          Page {page} of {totalPages}
        </span>

        <button
          onClick={() => onPageChange(page - 1)}
          disabled={isPrevDisabled}
          aria-label="Previous page"
          className="min-h-[44px] min-w-[44px] rounded-xl border border-border bg-card px-3 text-xs font-semibold text-foreground transition-colors hover:bg-primary/10 disabled:cursor-not-allowed disabled:opacity-40 focus:outline-none focus:ring-2 focus:ring-ring"
        >
          Prev
        </button>

        <button
          onClick={() => onPageChange(page + 1)}
          disabled={isNextDisabled}
          aria-label="Next page"
          className="min-h-[44px] min-w-[44px] rounded-xl border border-border bg-card px-3 text-xs font-semibold text-foreground transition-colors hover:bg-primary/10 disabled:cursor-not-allowed disabled:opacity-40 focus:outline-none focus:ring-2 focus:ring-ring"
        >
          Next
        </button>
      </div>
    </div>
  );
}
