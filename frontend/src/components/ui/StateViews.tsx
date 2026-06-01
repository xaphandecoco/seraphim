import type { LucideIcon } from 'lucide-react';
import { AlertCircle, RefreshCw } from 'lucide-react';

export function LoadingState({ message = 'Loading…' }: { message?: string }) {
  return (
    <div className="flex flex-col items-center justify-center py-16" role="status" aria-live="polite">
      <div className="mb-3 h-8 w-8 animate-spin rounded-full border-4 border-border border-t-primary" aria-hidden="true" />
      <p className="text-sm text-foreground/50">{message}</p>
    </div>
  );
}

export function EmptyState({
  icon: Icon,
  title,
  description,
}: {
  icon?: LucideIcon;
  title: string;
  description?: string;
}) {
  return (
    <div className="flex flex-col items-center justify-center py-16 text-foreground/50">
      {Icon && <Icon size={40} className="mb-3 opacity-40" aria-hidden="true" />}
      <p className="text-sm font-medium">{title}</p>
      {description && <p className="mt-1 text-xs">{description}</p>}
    </div>
  );
}

export function ErrorState({
  message,
  onRetry,
}: {
  message?: string;
  onRetry?: () => void;
}) {
  return (
    <div className="flex flex-col items-center justify-center py-16 text-foreground/50" role="alert">
      <AlertCircle size={40} className="mb-3 text-red-400" aria-hidden="true" />
      <p className="text-sm font-medium text-foreground">{message || 'Something went wrong'}</p>
      {onRetry && (
        <button
          onClick={onRetry}
          className="mt-3 flex items-center gap-2 rounded-xl border border-border bg-background px-4 py-2 text-xs font-semibold text-foreground hover:bg-primary/10"
        >
          <RefreshCw size={13} aria-hidden="true" />
          Try again
        </button>
      )}
    </div>
  );
}
