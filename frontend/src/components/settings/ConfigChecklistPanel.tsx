import { useQuery } from '@tanstack/react-query';
import { CheckCircle, XCircle } from 'lucide-react';
import { fetchConfigChecklist } from '@/services/settings';
import { LoadingState, ErrorState } from '@/components/ui/StateViews';

export function ConfigChecklistPanel() {
  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: ['settings', 'config-checklist'],
    queryFn: fetchConfigChecklist,
  });

  if (isLoading) {
    return <LoadingState message="Loading config checklist…" />;
  }

  if (isError || !data) {
    return (
      <ErrorState
        message="Unable to load config checklist"
        onRetry={() => refetch()}
      />
    );
  }

  const required = data.items.filter((i) => i.required);
  const optional = data.items.filter((i) => !i.required);
  const requiredSet = required.filter((i) => i.is_set).length;

  return (
    <div className="space-y-4">
      {/* Completion summary */}
      <div className="flex items-center justify-between">
        <span className="text-sm font-semibold text-foreground">Required</span>
        <span
          className={`text-xs font-bold ${
            data.required_complete ? 'text-green-600 dark:text-green-400' : 'text-red-500'
          }`}
        >
          {requiredSet}/{required.length} complete
        </span>
      </div>

      {/* Required items */}
      {required.length > 0 && (
        <div className="space-y-2">
          {required.map((item) => (
            <div key={item.key} className="flex items-center gap-2 text-xs">
              {item.is_set ? (
                <CheckCircle
                  size={14}
                  className="shrink-0 text-green-500"
                  aria-label="set"
                />
              ) : (
                <XCircle
                  size={14}
                  className="shrink-0 text-red-500"
                  aria-label="not set"
                />
              )}
              <span
                className={
                  item.is_set ? 'text-foreground/60' : 'font-medium text-foreground'
                }
              >
                {item.label}
              </span>
            </div>
          ))}
        </div>
      )}

      {/* Optional items */}
      {optional.length > 0 && (
        <>
          <div className="border-t border-border pt-2">
            <p className="mb-2 text-xs font-semibold text-foreground/50">Optional</p>
            <div className="space-y-1.5">
              {optional.map((item) => (
                <div key={item.key} className="flex items-center gap-2 text-xs text-foreground/50">
                  {item.is_set ? (
                    <CheckCircle size={12} className="shrink-0 text-green-400" aria-label="set" />
                  ) : (
                    <XCircle size={12} className="shrink-0 text-foreground/30" aria-label="not set" />
                  )}
                  <span>{item.label}</span>
                </div>
              ))}
            </div>
          </div>
        </>
      )}

      {data.all_complete && (
        <div className="rounded-xl bg-green-50 p-2 text-xs font-medium text-green-700 dark:bg-green-900/20 dark:text-green-400">
          All configuration items are set.
        </div>
      )}
    </div>
  );
}
