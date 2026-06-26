import { useState, useMemo } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import { ChevronDown, Save, X } from 'lucide-react';
import { getColumns, listPresets, savePreset } from '@/services/imports';
import { LoadingState, ErrorState } from '@/components/ui/StateViews';
import type {
  UploadColumn,
  ImportEntity,
  MatchKey,
  ConflictPolicy,
  ImportPreset,
} from '@/types/imports';

interface MapStepProps {
  batchId: number;
  entity: ImportEntity;
  columns: UploadColumn[];
  selectedSheet: string | null;
  columnMap: Record<string, string>;
  matchKey: MatchKey;
  conflictPolicy: ConflictPolicy;
  targetEventId: number | null;
  onColumnMapChange: (map: Record<string, string>) => void;
  onMatchKeyChange: (key: MatchKey) => void;
  onConflictPolicyChange: (policy: ConflictPolicy) => void;
  onTargetEventIdChange: (id: number | null) => void;
  onBack: () => void;
  onNext: () => void;
}

const IGNORE_KEY = 'ignore';

function getDuplicateTargets(map: Record<string, string>): Set<string> {
  const seen: Record<string, number> = {};
  for (const v of Object.values(map)) {
    if (v && v !== IGNORE_KEY) {
      seen[v] = (seen[v] ?? 0) + 1;
    }
  }
  return new Set(Object.entries(seen).filter(([, c]) => c > 1).map(([k]) => k));
}

interface SavePresetPanelProps {
  entity: ImportEntity;
  columnMap: Record<string, string>;
  conflictPolicy: ConflictPolicy;
  matchKey: MatchKey;
  onClose: () => void;
}

function SavePresetPanel({ entity, columnMap, conflictPolicy, matchKey, onClose }: SavePresetPanelProps) {
  const queryClient = useQueryClient();
  const [name, setName] = useState('');
  const [isShared, setIsShared] = useState(true);

  const mutation = useMutation({
    mutationFn: () =>
      savePreset({
        name: name.trim(),
        entity,
        column_map: columnMap,
        options: { conflict_policy: conflictPolicy, match_key: matchKey },
        is_shared: isShared,
      }),
    onSuccess: () => {
      toast.success('Preset saved.');
      queryClient.invalidateQueries({ queryKey: ['import-presets', entity] });
      onClose();
    },
    onError: (err: unknown) => {
      const detail = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      toast.error(detail ?? 'Failed to save preset.');
    },
  });

  return (
    <div className="rounded-xl border border-border bg-card p-4 space-y-3">
      <div className="flex items-center justify-between">
        <p className="text-sm font-semibold text-foreground">Save as preset</p>
        <button onClick={onClose} aria-label="Close" className="text-foreground/40 hover:text-foreground">
          <X size={14} aria-hidden="true" />
        </button>
      </div>
      <input
        type="text"
        value={name}
        onChange={(e) => setName(e.target.value)}
        placeholder="Preset name…"
        className="w-full rounded-xl border border-border bg-background px-3 py-2 text-sm text-foreground placeholder:text-foreground/40 focus:outline-none focus:ring-2 focus:ring-ring"
      />
      <label className="flex items-center gap-2 text-sm text-foreground">
        <input
          type="checkbox"
          checked={isShared}
          onChange={(e) => setIsShared(e.target.checked)}
          className="rounded border border-border accent-primary"
        />
        Share with all volunteers
      </label>
      <button
        type="button"
        onClick={() => { if (name.trim()) mutation.mutate(); }}
        disabled={!name.trim() || mutation.isPending}
        className="flex w-full items-center justify-center gap-2 rounded-xl bg-primary py-2 text-sm font-bold text-primary-foreground disabled:cursor-not-allowed disabled:opacity-40"
      >
        <Save size={14} aria-hidden="true" />
        {mutation.isPending ? 'Saving…' : 'Save preset'}
      </button>
    </div>
  );
}

export function MapStep({
  batchId,
  entity,
  columns,
  selectedSheet,
  columnMap,
  matchKey,
  conflictPolicy,
  targetEventId,
  onColumnMapChange,
  onMatchKeyChange,
  onConflictPolicyChange,
  onTargetEventIdChange,
  onBack,
  onNext,
}: MapStepProps) {
  const [showSavePanel, setShowSavePanel] = useState(false);
  const [loadPresetOpen, setLoadPresetOpen] = useState(false);

  const { data: columnsData, isLoading, isError, refetch } = useQuery({
    queryKey: ['import-columns', batchId, selectedSheet] as const,
    queryFn: () => getColumns(batchId, selectedSheet ?? undefined),
    enabled: !!batchId,
  });

  const { data: presetsData } = useQuery({
    queryKey: ['import-presets', entity] as const,
    queryFn: () => listPresets(entity),
    enabled: loadPresetOpen,
  });

  // Build initial map from suggested_map when columns data first loads
  const effectiveMap = useMemo(() => {
    if (Object.keys(columnMap).length > 0) return columnMap;
    if (!columnsData) return {};
    const map: Record<string, string> = {};
    for (const header of columnsData.headers) {
      const suggestion = columnsData.suggested_map[header];
      map[header] = suggestion?.target ?? IGNORE_KEY;
    }
    return map;
  }, [columnMap, columnsData]);

  const duplicateTargets = useMemo(() => getDuplicateTargets(effectiveMap), [effectiveMap]);

  const hasDuplicates = duplicateTargets.size > 0;

  const handleTargetChange = (header: string, target: string) => {
    onColumnMapChange({ ...effectiveMap, [header]: target });
  };

  const loadPreset = (preset: ImportPreset) => {
    onColumnMapChange(preset.column_map);
    const opts = preset.options as Record<string, string>;
    if (opts.conflict_policy) onConflictPolicyChange(opts.conflict_policy as ConflictPolicy);
    if (opts.match_key) onMatchKeyChange(opts.match_key as MatchKey);
    setLoadPresetOpen(false);
    toast.success(`Preset "${preset.name}" loaded.`);
  };

  if (isLoading) return <LoadingState message="Loading column suggestions…" />;
  if (isError) return <ErrorState message="Failed to load column mapping." onRetry={() => refetch()} />;

  const targets = columnsData?.targets ?? [];
  const displayHeaders = columnsData?.headers ?? columns.map((c) => c.name);

  return (
    <div className="space-y-6">
      {/* Preset bar */}
      <div className="flex flex-wrap items-center gap-2">
        <div className="relative">
          <button
            type="button"
            onClick={() => setLoadPresetOpen((v) => !v)}
            className="flex items-center gap-1.5 rounded-xl border border-border bg-card px-3 py-2 text-xs font-semibold text-foreground hover:bg-primary/10"
          >
            Load preset
            <ChevronDown size={12} aria-hidden="true" />
          </button>
          {loadPresetOpen && (
            <div className="absolute left-0 top-full z-20 mt-1 w-56 rounded-xl border border-border bg-card shadow-xl">
              {!presetsData || presetsData.items.length === 0 ? (
                <p className="px-4 py-3 text-xs text-foreground/50">No presets saved for {entity}.</p>
              ) : (
                <ul>
                  {presetsData.items.map((p) => (
                    <li key={p.id}>
                      <button
                        type="button"
                        onClick={() => loadPreset(p)}
                        className="w-full px-4 py-2.5 text-left text-sm text-foreground hover:bg-primary/10"
                      >
                        {p.name}
                        {p.is_shared && (
                          <span className="ml-2 text-xs text-foreground/40">shared</span>
                        )}
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          )}
        </div>

        <button
          type="button"
          onClick={() => setShowSavePanel((v) => !v)}
          className="flex items-center gap-1.5 rounded-xl border border-border bg-card px-3 py-2 text-xs font-semibold text-foreground hover:bg-primary/10"
        >
          <Save size={12} aria-hidden="true" />
          Save as preset…
        </button>
      </div>

      {showSavePanel && (
        <SavePresetPanel
          entity={entity}
          columnMap={effectiveMap}
          conflictPolicy={conflictPolicy}
          matchKey={matchKey}
          onClose={() => setShowSavePanel(false)}
        />
      )}

      {/* Duplicate target error */}
      {hasDuplicates && (
        <div
          role="alert"
          className="rounded-xl border border-destructive/40 bg-destructive/10 px-4 py-3 text-sm text-destructive"
        >
          Two or more columns share the same target:{' '}
          <strong>{Array.from(duplicateTargets).join(', ')}</strong>.
          Each target may only be mapped once.
        </div>
      )}

      {/* Match key + conflict policy */}
      <div className="grid gap-4 sm:grid-cols-2">
        <div>
          <label className="mb-1 block text-sm font-semibold text-foreground" htmlFor="match-key">
            Dedupe match key
          </label>
          <select
            id="match-key"
            value={matchKey}
            onChange={(e) => onMatchKeyChange(e.target.value as MatchKey)}
            className="w-full rounded-xl border border-border bg-background px-3 py-2.5 text-sm text-foreground focus:outline-none focus:ring-2 focus:ring-ring"
          >
            <option value="external_id">Contact ID (external_id)</option>
            <option value="email">Email address</option>
            <option value="name">Full name</option>
          </select>
        </div>

        <div>
          <p className="mb-1 text-sm font-semibold text-foreground">Conflict policy</p>
          <div
            className="flex overflow-hidden rounded-xl border border-border bg-background"
            role="group"
            aria-label="Conflict policy"
          >
            {(
              [
                { value: 'skip', label: 'Skip existing' },
                { value: 'update', label: 'Update all' },
                { value: 'fill', label: 'Fill empty only' },
              ] as { value: ConflictPolicy; label: string }[]
            ).map((opt) => (
              <button
                key={opt.value}
                type="button"
                onClick={() => onConflictPolicyChange(opt.value)}
                className={`flex-1 py-2.5 text-xs font-semibold transition-colors ${
                  conflictPolicy === opt.value
                    ? 'bg-primary text-primary-foreground'
                    : 'text-foreground hover:bg-primary/10'
                }`}
              >
                {opt.label}
              </button>
            ))}
          </div>
        </div>
      </div>

      {/* Participant: target event */}
      {entity === 'participant' && (
        <div>
          <label className="mb-1 block text-sm font-semibold text-foreground" htmlFor="target-event-id">
            Target event ID
            <span className="ml-1 text-xs font-normal text-foreground/50">
              (leave blank to use an Event ID column)
            </span>
          </label>
          <input
            id="target-event-id"
            type="number"
            min={1}
            value={targetEventId ?? ''}
            onChange={(e) => {
              const v = e.target.value;
              onTargetEventIdChange(v ? Number(v) : null);
            }}
            placeholder="Event ID…"
            className="w-full rounded-xl border border-border bg-background px-3 py-2.5 text-sm text-foreground placeholder:text-foreground/40 focus:outline-none focus:ring-2 focus:ring-ring"
          />
        </div>
      )}

      {/* Column mapping table */}
      <div>
        <h3 className="mb-3 text-sm font-semibold text-foreground">Column mapping</h3>

        {/* Mobile: stacked cards */}
        <div className="block space-y-2 md:hidden">
          {displayHeaders.map((header) => {
            const currentTarget = effectiveMap[header] ?? IGNORE_KEY;
            const isDuplicate = currentTarget !== IGNORE_KEY && duplicateTargets.has(currentTarget);
            return (
              <div
                key={header}
                className={`rounded-xl border px-4 py-3 ${isDuplicate ? 'border-destructive/50 bg-destructive/5' : 'border-border bg-card'}`}
              >
                <p className="mb-2 text-xs font-semibold text-foreground">{header}</p>
                <select
                  aria-label={`Map ${header}`}
                  value={currentTarget}
                  onChange={(e) => handleTargetChange(header, e.target.value)}
                  className="w-full rounded-xl border border-border bg-background px-2 py-2 text-xs text-foreground focus:outline-none focus:ring-2 focus:ring-ring"
                >
                  <option value={IGNORE_KEY}>— ignore —</option>
                  {targets.map((t) => (
                    <option key={t.key} value={t.key}>{t.label}</option>
                  ))}
                </select>
                {isDuplicate && (
                  <p className="mt-1 text-xs text-destructive">Duplicate target</p>
                )}
              </div>
            );
          })}
        </div>

        {/* Desktop: two-column table */}
        <div className="hidden md:block overflow-x-auto rounded-2xl border border-border bg-card">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border">
                <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-foreground/60">
                  Source column
                </th>
                <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-foreground/60">
                  Maps to
                </th>
              </tr>
            </thead>
            <tbody>
              {displayHeaders.map((header) => {
                const currentTarget = effectiveMap[header] ?? IGNORE_KEY;
                const isDuplicate = currentTarget !== IGNORE_KEY && duplicateTargets.has(currentTarget);
                return (
                  <tr key={header} className="border-b border-border last:border-0">
                    <td className="px-4 py-3">
                      <span className="font-medium text-foreground">{header}</span>
                    </td>
                    <td className="px-4 py-3">
                      <select
                        aria-label={`Map ${header}`}
                        value={currentTarget}
                        onChange={(e) => handleTargetChange(header, e.target.value)}
                        className={`w-full max-w-xs rounded-xl border px-2 py-1.5 text-sm text-foreground focus:outline-none focus:ring-2 focus:ring-ring ${
                          isDuplicate
                            ? 'border-destructive bg-destructive/5'
                            : 'border-border bg-background'
                        }`}
                      >
                        <option value={IGNORE_KEY}>— ignore —</option>
                        {targets.map((t) => (
                          <option key={t.key} value={t.key}>{t.label}</option>
                        ))}
                      </select>
                      {isDuplicate && (
                        <p className="mt-0.5 text-xs text-destructive">Duplicate target</p>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>

      {/* Footer */}
      <div className="flex justify-between pt-2">
        <button
          type="button"
          onClick={onBack}
          className="rounded-xl border border-border bg-background px-5 py-2.5 text-sm font-semibold text-foreground hover:bg-primary/10"
        >
          Back
        </button>
        <button
          type="button"
          onClick={onNext}
          disabled={hasDuplicates}
          title={hasDuplicates ? 'Resolve duplicate targets before proceeding' : undefined}
          className="rounded-xl bg-primary px-6 py-2.5 text-sm font-bold text-primary-foreground shadow-sm transition-all active:scale-[0.98] disabled:cursor-not-allowed disabled:opacity-40"
        >
          Next: Preview
        </button>
      </div>
    </div>
  );
}
