// S11 — DedupeRuleEditor: admin CRUD for dedupe rule sets.

import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import { Plus, Trash2, Pencil, Check, X, Star, ToggleLeft, ToggleRight } from 'lucide-react';
import { listRuleSets, createRuleSet, updateRuleSet, deleteRuleSet } from '@/services/dedupe';
import type { RuleSet, RuleSetCreate, DedupeFieldRule } from '@/types/dedupe';
import { DEDUPE_FIELD_WHITELIST } from '@/types/dedupe';
import { LoadingState, ErrorState, EmptyState } from '@/components/ui/StateViews';
import { ConfirmDialog } from '@/components/ui/ConfirmDialog';

// ─── Field rule row inside the form ──────────────────────────────────────────

interface RuleRowProps {
  rule: DedupeFieldRule;
  index: number;
  onChange: (index: number, rule: DedupeFieldRule) => void;
  onRemove: (index: number) => void;
}

function RuleRow({ rule, index, onChange, onRemove }: RuleRowProps) {
  return (
    <div className="flex items-center gap-2">
      <select
        value={rule.field}
        onChange={(e) => onChange(index, { ...rule, field: e.target.value })}
        className="flex-1 rounded-xl border border-border bg-background px-3 py-2 text-xs text-foreground focus:outline-none focus:ring-2 focus:ring-ring"
        aria-label={`Rule ${index + 1} field`}
      >
        {DEDUPE_FIELD_WHITELIST.map((f) => (
          <option key={f} value={f}>
            {f.replace(/_/g, ' ')}
          </option>
        ))}
      </select>
      <input
        type="number"
        min={1}
        max={1000}
        value={rule.weight}
        onChange={(e) => onChange(index, { ...rule, weight: Number(e.target.value) })}
        className="w-20 rounded-xl border border-border bg-background px-3 py-2 text-xs text-foreground focus:outline-none focus:ring-2 focus:ring-ring"
        aria-label={`Rule ${index + 1} weight`}
        placeholder="Weight"
      />
      <button
        type="button"
        onClick={() => onRemove(index)}
        aria-label={`Remove rule ${index + 1}`}
        className="flex h-8 w-8 items-center justify-center rounded-full text-foreground/40 hover:bg-background hover:text-destructive focus:outline-none focus:ring-2 focus:ring-ring"
      >
        <Trash2 size={14} aria-hidden="true" />
      </button>
    </div>
  );
}

// ─── Rule set form (create / edit) ───────────────────────────────────────────

interface RuleSetFormProps {
  initial?: RuleSet;
  onSave: (data: RuleSetCreate) => void;
  onCancel: () => void;
  isPending: boolean;
}

function RuleSetForm({ initial, onSave, onCancel, isPending }: RuleSetFormProps) {
  const [name, setName] = useState(initial?.name ?? '');
  const [description, setDescription] = useState(initial?.description ?? '');
  const [threshold, setThreshold] = useState(initial?.threshold ?? 70);
  const [isDefault, setIsDefault] = useState(initial?.is_default ?? false);
  const [isActive, setIsActive] = useState(initial?.is_active ?? true);
  const [rules, setRules] = useState<DedupeFieldRule[]>(
    initial?.rules?.length ? initial.rules : [{ field: 'email', weight: 100 }],
  );

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!name.trim()) { toast.error('Name is required'); return; }
    if (rules.length === 0) { toast.error('At least one rule is required'); return; }
    if (threshold < 1) { toast.error('Threshold must be at least 1'); return; }
    onSave({ name: name.trim(), description: description.trim() || null, rules, threshold, is_default: isDefault, is_active: isActive });
  }

  function addRule() {
    setRules((prev) => [...prev, { field: 'first_name', weight: 30 }]);
  }

  function updateRule(i: number, rule: DedupeFieldRule) {
    setRules((prev) => prev.map((r, idx) => (idx === i ? rule : r)));
  }

  function removeRule(i: number) {
    setRules((prev) => prev.filter((_, idx) => idx !== i));
  }

  return (
    <form onSubmit={handleSubmit} className="rounded-2xl border border-border bg-card p-5 space-y-4">
      <h3 className="text-sm font-bold text-foreground">
        {initial ? 'Edit Rule Set' : 'New Rule Set'}
      </h3>

      <div className="space-y-2">
        <label className="text-xs font-semibold text-foreground/60" htmlFor="rs-name">
          Name
        </label>
        <input
          id="rs-name"
          type="text"
          value={name}
          onChange={(e) => setName(e.target.value)}
          maxLength={100}
          required
          className="h-10 w-full rounded-xl border border-border bg-background px-3 text-sm text-foreground focus:outline-none focus:ring-2 focus:ring-ring"
          placeholder="e.g. Default"
        />
      </div>

      <div className="space-y-2">
        <label className="text-xs font-semibold text-foreground/60" htmlFor="rs-desc">
          Description
        </label>
        <input
          id="rs-desc"
          type="text"
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          className="h-10 w-full rounded-xl border border-border bg-background px-3 text-sm text-foreground focus:outline-none focus:ring-2 focus:ring-ring"
          placeholder="Optional"
        />
      </div>

      <div className="space-y-2">
        <label className="text-xs font-semibold text-foreground/60" htmlFor="rs-threshold">
          Threshold (minimum score)
        </label>
        <input
          id="rs-threshold"
          type="number"
          min={1}
          value={threshold}
          onChange={(e) => setThreshold(Number(e.target.value))}
          className="h-10 w-32 rounded-xl border border-border bg-background px-3 text-sm text-foreground focus:outline-none focus:ring-2 focus:ring-ring"
        />
      </div>

      {/* Rules list */}
      <div className="space-y-2">
        <div className="flex items-center justify-between">
          <span className="text-xs font-semibold text-foreground/60">Rules</span>
          <button
            type="button"
            onClick={addRule}
            className="flex items-center gap-1 rounded-lg border border-border px-2 py-1 text-xs font-semibold text-foreground/70 hover:bg-background focus:outline-none focus:ring-2 focus:ring-ring"
          >
            <Plus size={11} aria-hidden="true" />
            Add rule
          </button>
        </div>
        <div className="space-y-2">
          {rules.map((rule, i) => (
            <RuleRow
              key={i}
              rule={rule}
              index={i}
              onChange={updateRule}
              onRemove={removeRule}
            />
          ))}
        </div>
        <p className="text-[10px] text-foreground/40">
          Field · Weight (1–1000). Score = sum of weights for matched fields.
        </p>
      </div>

      {/* Toggles */}
      <div className="flex gap-4">
        <label className="flex cursor-pointer items-center gap-2 text-xs font-semibold text-foreground/70">
          <input
            type="checkbox"
            checked={isDefault}
            onChange={(e) => setIsDefault(e.target.checked)}
            className="accent-primary"
          />
          Default
        </label>
        <label className="flex cursor-pointer items-center gap-2 text-xs font-semibold text-foreground/70">
          <input
            type="checkbox"
            checked={isActive}
            onChange={(e) => setIsActive(e.target.checked)}
            className="accent-primary"
          />
          Active
        </label>
      </div>

      <div className="flex justify-end gap-2 pt-1">
        <button
          type="button"
          onClick={onCancel}
          className="rounded-xl border border-border px-4 py-2 text-sm font-semibold text-foreground/70 hover:bg-background focus:outline-none focus:ring-2 focus:ring-ring"
        >
          Cancel
        </button>
        <button
          type="submit"
          disabled={isPending}
          className="flex items-center gap-1.5 rounded-xl bg-primary px-4 py-2 text-sm font-bold text-primary-foreground hover:bg-primary/85 disabled:opacity-50 focus:outline-none focus:ring-2 focus:ring-ring"
        >
          <Check size={13} aria-hidden="true" />
          {isPending ? 'Saving…' : 'Save'}
        </button>
      </div>
    </form>
  );
}

// ─── Main editor ──────────────────────────────────────────────────────────────

export function DedupeRuleEditor() {
  const queryClient = useQueryClient();
  const [editing, setEditing] = useState<RuleSet | null>(null);
  const [creating, setCreating] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<RuleSet | null>(null);

  const { data: ruleSets = [], isLoading, isError, refetch } = useQuery({
    queryKey: ['dedupe', 'rule-sets'],
    queryFn: listRuleSets,
  });

  const createMutation = useMutation({
    mutationFn: (data: RuleSetCreate) => createRuleSet(data),
    onSuccess: (rs) => {
      toast.success(`Rule set "${rs.name}" created`);
      queryClient.invalidateQueries({ queryKey: ['dedupe', 'rule-sets'] });
      setCreating(false);
    },
    onError: (err: unknown) => {
      const ax = err as { response?: { data?: { detail?: string } } };
      toast.error(ax.response?.data?.detail ?? 'Failed to create rule set');
    },
  });

  const updateMutation = useMutation({
    mutationFn: ({ id, data }: { id: number; data: RuleSetCreate }) => updateRuleSet(id, data),
    onSuccess: (rs) => {
      toast.success(`Rule set "${rs.name}" updated`);
      queryClient.invalidateQueries({ queryKey: ['dedupe', 'rule-sets'] });
      setEditing(null);
    },
    onError: (err: unknown) => {
      const ax = err as { response?: { data?: { detail?: string } } };
      toast.error(ax.response?.data?.detail ?? 'Failed to update rule set');
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (id: number) => deleteRuleSet(id),
    onSuccess: () => {
      toast.success('Rule set deleted');
      queryClient.invalidateQueries({ queryKey: ['dedupe', 'rule-sets'] });
      setDeleteTarget(null);
    },
    onError: (err: unknown) => {
      const ax = err as { response?: { data?: { detail?: string } } };
      toast.error(ax.response?.data?.detail ?? 'Failed to delete rule set');
      setDeleteTarget(null);
    },
  });

  if (isLoading) return <LoadingState message="Loading rule sets…" />;
  if (isError) return <ErrorState message="Failed to load rule sets" onRetry={() => refetch()} />;

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-bold text-foreground">Dedupe Rule Sets</h2>
        {!creating && editing === null && (
          <button
            type="button"
            onClick={() => setCreating(true)}
            className="flex items-center gap-1.5 rounded-xl bg-primary px-3 py-2 text-xs font-bold text-primary-foreground hover:bg-primary/85 focus:outline-none focus:ring-2 focus:ring-ring"
          >
            <Plus size={13} aria-hidden="true" />
            New rule set
          </button>
        )}
      </div>

      {/* Create form */}
      {creating && (
        <RuleSetForm
          onSave={(data) => createMutation.mutate(data)}
          onCancel={() => setCreating(false)}
          isPending={createMutation.isPending}
        />
      )}

      {/* Edit form */}
      {editing && (
        <RuleSetForm
          initial={editing}
          onSave={(data) => updateMutation.mutate({ id: editing.id, data })}
          onCancel={() => setEditing(null)}
          isPending={updateMutation.isPending}
        />
      )}

      {/* List */}
      {ruleSets.length === 0 && !creating ? (
        <EmptyState
          icon={ToggleLeft}
          title="No rule sets yet"
          description="Create a rule set to configure how duplicates are detected."
        />
      ) : (
        <ul className="space-y-2">
          {ruleSets.map((rs) => (
            <li key={rs.id} className="rounded-xl border border-border bg-card p-4">
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <div className="flex items-center gap-2">
                    <p className="font-semibold text-foreground truncate">{rs.name}</p>
                    {rs.is_default && (
                      <span className="inline-flex items-center gap-1 rounded-full bg-primary/10 px-2 py-0.5 text-[10px] font-bold text-primary">
                        <Star size={9} aria-hidden="true" />
                        Default
                      </span>
                    )}
                    {rs.is_active ? (
                      <span className="inline-flex items-center gap-1 rounded-full bg-green-500/10 px-2 py-0.5 text-[10px] font-bold text-green-600">
                        <ToggleRight size={9} aria-hidden="true" />
                        Active
                      </span>
                    ) : (
                      <span className="inline-flex items-center gap-1 rounded-full bg-muted px-2 py-0.5 text-[10px] font-bold text-foreground/40">
                        <ToggleLeft size={9} aria-hidden="true" />
                        Inactive
                      </span>
                    )}
                  </div>
                  {rs.description && (
                    <p className="mt-0.5 text-xs text-foreground/50">{rs.description}</p>
                  )}
                  <p className="mt-1 text-xs text-foreground/40">
                    Threshold: {rs.threshold} · {rs.rules.length} rule{rs.rules.length !== 1 ? 's' : ''}
                  </p>
                  <div className="mt-1.5 flex flex-wrap gap-1">
                    {rs.rules.map((r, i) => (
                      <span
                        key={i}
                        className="rounded-full border border-border px-2 py-0.5 text-[10px] text-foreground/60"
                      >
                        {r.field.replace(/_/g, ' ')} ×{r.weight}
                      </span>
                    ))}
                  </div>
                </div>
                <div className="flex shrink-0 gap-1">
                  <button
                    type="button"
                    onClick={() => { setEditing(rs); setCreating(false); }}
                    aria-label={`Edit "${rs.name}"`}
                    className="flex h-8 w-8 items-center justify-center rounded-full text-foreground/40 hover:bg-background hover:text-primary focus:outline-none focus:ring-2 focus:ring-ring"
                  >
                    <Pencil size={14} aria-hidden="true" />
                  </button>
                  <button
                    type="button"
                    onClick={() => setDeleteTarget(rs)}
                    aria-label={`Delete "${rs.name}"`}
                    className="flex h-8 w-8 items-center justify-center rounded-full text-foreground/40 hover:bg-background hover:text-destructive focus:outline-none focus:ring-2 focus:ring-ring"
                  >
                    <X size={14} aria-hidden="true" />
                  </button>
                </div>
              </div>
            </li>
          ))}
        </ul>
      )}

      {/* Delete confirm */}
      {deleteTarget && (
        <ConfirmDialog
          message={`Delete rule set "${deleteTarget.name}"? This cannot be undone.`}
          confirmLabel="Delete"
          destructive
          onConfirm={() => deleteMutation.mutate(deleteTarget.id)}
          onCancel={() => setDeleteTarget(null)}
        />
      )}
    </div>
  );
}
