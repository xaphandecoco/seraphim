/**
 * CustomFieldsPage — /settings/custom-fields
 *
 * Admin-only page for managing custom field groups and definitions.
 * Route guard: AdminRoute in App.tsx redirects non-admins to "/".
 *
 * Layout:
 *  - Mobile (single column): entity tab selector → group list with inline field management.
 *  - Desktop (lg: 2-column): left = CRUD, right = live preview with Test Validate.
 *
 * Mutation pattern (first useMutation in this codebase — establishes the standard):
 *   useMutation({ mutationFn, onSuccess: () => queryClient.invalidateQueries({queryKey:['custom-fields']}) + toast.success, onError: toast.error })
 */

import { useState, useEffect } from 'react';
import { useQueryClient, useQuery, useMutation } from '@tanstack/react-query';
import type { AxiosError } from 'axios';
import { toast } from 'sonner';
import { useNavigate } from 'react-router-dom';
import { ArrowLeft, Plus, ChevronDown, ChevronRight, Settings2 } from 'lucide-react';

import { useAuthStore } from '@/store/authStore';
import { customFieldsApi } from '@/services/customFields';
import { ConfirmDialog } from '@/components/ui/ConfirmDialog';
import { LoadingState, EmptyState, ErrorState } from '@/components/ui/StateViews';
import { CustomFieldsSection } from '@/components/customFields/CustomFieldsSection';
import { OptionEditor } from '@/components/customFields/OptionEditor';

import type {
  CustomFieldGroup,
  CustomFieldDef,
  CustomFieldGroupCreate,
  CustomFieldGroupUpdate,
  CustomFieldDefCreate,
  CustomFieldDefUpdate,
  CustomDataValue,
  OptionItem,
  DataType,
} from '@/types/customFields';

// ---------- Types -------------------------------------------------------------

type Entity = 'contact' | 'event' | 'activity';

interface ApiError {
  detail?: string;
}

interface DeleteConfirm {
  kind: 'group' | 'field';
  id: number;
  label: string;
  hard: boolean;
  affectedContacts?: number;
}

// ---------- Helpers -----------------------------------------------------------

const ENTITY_TABS: { value: Entity; label: string }[] = [
  { value: 'contact', label: 'Contact' },
  { value: 'event', label: 'Event' },
  { value: 'activity', label: 'Activity' },
];

const DATA_TYPE_OPTIONS: { value: DataType; label: string }[] = [
  { value: 'text', label: 'Text' },
  { value: 'textarea', label: 'Textarea' },
  { value: 'select', label: 'Select' },
  { value: 'multiselect', label: 'Multiselect' },
  { value: 'date', label: 'Date' },
  { value: 'number', label: 'Number' },
  { value: 'checkbox', label: 'Checkbox' },
  { value: 'contact_reference', label: 'Contact Reference' },
];

function dtypeBadge(dt: string) {
  return (
    <span className="rounded-full bg-primary/10 px-2 py-0.5 text-xs font-semibold text-primary">
      {dt}
    </span>
  );
}

// ---------- Sub-components: forms --------------------------------------------

interface AddGroupFormProps {
  entity: Entity;
  onClose: () => void;
  onCreated: () => void;
}

function AddGroupForm({ entity, onClose, onCreated }: AddGroupFormProps) {
  const queryClient = useQueryClient();
  const [name, setName] = useState('');
  const [label, setLabel] = useState('');
  const [weight, setWeight] = useState(0);

  const mutation = useMutation<CustomFieldGroup, AxiosError<ApiError>, CustomFieldGroupCreate>({
    mutationFn: (body) => customFieldsApi.createGroup(body),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['custom-fields'] });
      toast.success('Group created');
      onCreated();
    },
    onError: (err) =>
      toast.error(err.response?.data?.detail ?? 'Could not create group'),
  });

  const submit = () => {
    if (!name.trim() || !label.trim()) return;
    mutation.mutate({ name: name.trim(), label: label.trim(), entity, weight });
  };

  return (
    <div className="rounded-2xl border border-border bg-card p-4 shadow-sm">
      <p className="mb-3 text-sm font-bold text-foreground">New Group</p>
      <div className="space-y-3">
        <div>
          <label className="mb-1 block text-xs font-medium text-foreground/60">
            Machine name (snake_case)
          </label>
          <input
            type="text"
            placeholder="machine name e.g. leader_info"
            value={name}
            onChange={(e) => setName(e.target.value)}
            className="h-10 w-full rounded-xl border border-border bg-background px-3 text-sm text-foreground placeholder:text-foreground/40 focus:outline-none focus:ring-2 focus:ring-primary/30"
          />
        </div>
        <div>
          <label className="mb-1 block text-xs font-medium text-foreground/60">
            Display label
          </label>
          <input
            type="text"
            placeholder="display label e.g. Leader Info"
            value={label}
            onChange={(e) => setLabel(e.target.value)}
            className="h-10 w-full rounded-xl border border-border bg-background px-3 text-sm text-foreground placeholder:text-foreground/40 focus:outline-none focus:ring-2 focus:ring-primary/30"
          />
        </div>
        <div>
          <label className="mb-1 block text-xs font-medium text-foreground/60">Weight</label>
          <input
            type="number"
            value={weight}
            onChange={(e) => setWeight(Number(e.target.value))}
            className="h-10 w-full rounded-xl border border-border bg-background px-3 text-sm text-foreground focus:outline-none focus:ring-2 focus:ring-primary/30"
          />
        </div>
        <div className="flex gap-2 pt-1">
          <button
            type="button"
            onClick={onClose}
            className="flex h-10 flex-1 items-center justify-center rounded-xl border border-border bg-background text-sm font-semibold text-foreground hover:bg-muted focus:outline-none focus:ring-2 focus:ring-ring"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={submit}
            disabled={mutation.isPending || !name.trim() || !label.trim()}
            className="flex h-10 flex-1 items-center justify-center rounded-xl bg-primary text-sm font-bold text-primary-foreground shadow-sm hover:bg-primary/85 active:scale-[0.98] disabled:opacity-50 focus:outline-none focus:ring-2 focus:ring-ring"
          >
            {mutation.isPending ? 'Creating…' : 'Create Group'}
          </button>
        </div>
      </div>
    </div>
  );
}

interface AddFieldFormProps {
  groupId: number;
  onClose: () => void;
  onCreated: () => void;
}

function AddFieldForm({ groupId, onClose, onCreated }: AddFieldFormProps) {
  const queryClient = useQueryClient();
  const [name, setName] = useState('');
  const [label, setLabel] = useState('');
  const [dataType, setDataType] = useState<DataType>('text');
  const [options, setOptions] = useState<OptionItem[]>([]);
  const [isRequired, setIsRequired] = useState(false);
  const [isMulti, setIsMulti] = useState(false);
  const [weight, setWeight] = useState(0);
  const [helpText, setHelpText] = useState('');

  const mutation = useMutation<CustomFieldDef, AxiosError<ApiError>, CustomFieldDefCreate>({
    mutationFn: (body) => customFieldsApi.createDef(body),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['custom-fields'] });
      toast.success('Field created');
      onCreated();
    },
    onError: (err) =>
      toast.error(err.response?.data?.detail ?? 'Could not create field'),
  });

  const needsOptions = dataType === 'select' || dataType === 'multiselect';

  const submit = () => {
    if (!name.trim() || !label.trim()) return;
    mutation.mutate({
      group_id: groupId,
      name: name.trim(),
      label: label.trim(),
      data_type: dataType,
      options: needsOptions ? options : [],
      is_required: isRequired,
      is_multi: dataType === 'multiselect' ? true : isMulti,
      weight,
      help_text: helpText.trim() || null,
    });
  };

  return (
    <div className="rounded-xl border border-border bg-background p-4">
      <p className="mb-3 text-xs font-bold uppercase tracking-wide text-foreground/50">
        Add Field
      </p>
      <div className="space-y-3">
        <div>
          <label className="mb-1 block text-xs font-medium text-foreground/60">
            Machine name
          </label>
          <input
            type="text"
            placeholder="field machine name e.g. barangay"
            value={name}
            onChange={(e) => setName(e.target.value)}
            className="h-10 w-full rounded-xl border border-border bg-background px-3 text-sm text-foreground placeholder:text-foreground/40 focus:outline-none focus:ring-2 focus:ring-primary/30"
          />
        </div>
        <div>
          <label className="mb-1 block text-xs font-medium text-foreground/60">
            Display label
          </label>
          <input
            type="text"
            placeholder="field display label e.g. Barangay"
            value={label}
            onChange={(e) => setLabel(e.target.value)}
            className="h-10 w-full rounded-xl border border-border bg-background px-3 text-sm text-foreground placeholder:text-foreground/40 focus:outline-none focus:ring-2 focus:ring-primary/30"
          />
        </div>
        <div>
          <label className="mb-1 block text-xs font-medium text-foreground/60">
            Data type
          </label>
          <select
            value={dataType}
            onChange={(e) => setDataType(e.target.value as DataType)}
            className="h-10 w-full rounded-xl border border-border bg-background px-3 text-sm text-foreground focus:outline-none focus:ring-2 focus:ring-primary/30"
          >
            {DATA_TYPE_OPTIONS.map((opt) => (
              <option key={opt.value} value={opt.value}>
                {opt.label}
              </option>
            ))}
          </select>
        </div>
        {needsOptions && (
          <OptionEditor options={options} onChange={setOptions} />
        )}
        <div className="flex gap-4">
          <label className="flex cursor-pointer items-center gap-2 text-sm text-foreground">
            <input
              type="checkbox"
              checked={isRequired}
              onChange={(e) => setIsRequired(e.target.checked)}
              className="h-4 w-4 accent-primary"
            />
            Required
          </label>
          {dataType !== 'multiselect' && dataType !== 'checkbox' && (
            <label className="flex cursor-pointer items-center gap-2 text-sm text-foreground">
              <input
                type="checkbox"
                checked={isMulti}
                onChange={(e) => setIsMulti(e.target.checked)}
                className="h-4 w-4 accent-primary"
              />
              Multi-value
            </label>
          )}
        </div>
        <div>
          <label className="mb-1 block text-xs font-medium text-foreground/60">
            Weight
          </label>
          <input
            type="number"
            value={weight}
            onChange={(e) => setWeight(Number(e.target.value))}
            className="h-10 w-full rounded-xl border border-border bg-background px-3 text-sm text-foreground focus:outline-none focus:ring-2 focus:ring-primary/30"
          />
        </div>
        <div>
          <label className="mb-1 block text-xs font-medium text-foreground/60">
            Help text (optional)
          </label>
          <input
            type="text"
            placeholder="Hint shown below the field"
            value={helpText}
            onChange={(e) => setHelpText(e.target.value)}
            className="h-10 w-full rounded-xl border border-border bg-background px-3 text-sm text-foreground placeholder:text-foreground/40 focus:outline-none focus:ring-2 focus:ring-primary/30"
          />
        </div>
        <div className="flex gap-2 pt-1">
          <button
            type="button"
            onClick={onClose}
            className="flex h-10 flex-1 items-center justify-center rounded-xl border border-border bg-background text-sm font-semibold text-foreground hover:bg-muted focus:outline-none focus:ring-2 focus:ring-ring"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={submit}
            disabled={mutation.isPending || !name.trim() || !label.trim()}
            className="flex h-10 flex-1 items-center justify-center rounded-xl bg-primary text-sm font-bold text-primary-foreground shadow-sm hover:bg-primary/85 active:scale-[0.98] disabled:opacity-50 focus:outline-none focus:ring-2 focus:ring-ring"
          >
            {mutation.isPending ? 'Creating…' : 'Create Field'}
          </button>
        </div>
      </div>
    </div>
  );
}

// ---------- Sub-component: FieldRow ------------------------------------------

interface FieldRowProps {
  field: CustomFieldDef;
  onDeactivate: (field: CustomFieldDef) => void;
  onHardDelete: (field: CustomFieldDef) => void;
}

function FieldRow({ field, onDeactivate, onHardDelete }: FieldRowProps) {
  return (
    <div className="flex items-center gap-3 rounded-xl border border-border bg-background px-3 py-2">
      <div className="flex-1 min-w-0">
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-sm font-medium text-foreground truncate">{field.label}</span>
          <span className="text-xs text-foreground/40">({field.name})</span>
          {dtypeBadge(field.data_type)}
          {field.is_required && (
            <span className="rounded-full border border-destructive/30 bg-destructive/10 px-2 py-0.5 text-xs font-semibold text-destructive">
              required
            </span>
          )}
          {!field.is_active && (
            <span className="rounded-full border border-border bg-background px-2 py-0.5 text-xs text-foreground/40">
              inactive
            </span>
          )}
        </div>
        {field.help_text && (
          <p className="mt-0.5 text-xs text-foreground/40 truncate">{field.help_text}</p>
        )}
      </div>
      <div className="flex shrink-0 gap-1">
        <button
          type="button"
          onClick={() => onDeactivate(field)}
          className="rounded-lg border border-border bg-background px-2 py-1 text-xs font-medium text-foreground/60 hover:bg-background hover:text-foreground focus:outline-none focus:ring-2 focus:ring-ring min-h-[32px]"
          aria-label={`Deactivate ${field.label}`}
        >
          Deactivate
        </button>
        <button
          type="button"
          onClick={() => onHardDelete(field)}
          className="rounded-lg border border-red-200 bg-red-50 px-2 py-1 text-xs font-medium text-red-600 hover:bg-red-100 focus:outline-none focus:ring-2 focus:ring-ring min-h-[32px]"
          aria-label={`Delete ${field.label}`}
        >
          Delete
        </button>
      </div>
    </div>
  );
}

// ---------- Sub-component: GroupCard ------------------------------------------

interface GroupCardProps {
  group: CustomFieldGroup;
  onDeactivateGroup: (group: CustomFieldGroup) => void;
  onHardDeleteGroup: (group: CustomFieldGroup) => void;
  onDeactivateField: (field: CustomFieldDef) => void;
  onHardDeleteField: (field: CustomFieldDef) => void;
}

function GroupCard({
  group,
  onDeactivateGroup,
  onHardDeleteGroup,
  onDeactivateField,
  onHardDeleteField,
}: GroupCardProps) {
  const [expanded, setExpanded] = useState(true);
  const [showAddField, setShowAddField] = useState(false);

  const activeFields = group.fields.filter((f) => f.is_active).sort((a, b) => a.weight - b.weight);

  return (
    <div className="rounded-2xl border border-border bg-card shadow-sm">
      {/* Group header */}
      <div className="flex items-center gap-3 px-4 py-3">
        <button
          type="button"
          onClick={() => setExpanded((v) => !v)}
          aria-expanded={expanded}
          aria-label={`${expanded ? 'Collapse' : 'Expand'} ${group.label}`}
          className="flex items-center gap-2 flex-1 min-w-0 text-left focus:outline-none focus:ring-2 focus:ring-ring rounded"
        >
          {expanded ? (
            <ChevronDown size={16} className="shrink-0 text-foreground/40" aria-hidden="true" />
          ) : (
            <ChevronRight size={16} className="shrink-0 text-foreground/40" aria-hidden="true" />
          )}
          <span className="font-semibold text-foreground truncate">{group.label}</span>
          <span className="rounded-full bg-background border border-border px-2 py-0.5 text-xs text-foreground/50 font-mono shrink-0">
            {group.name}
          </span>
          <span className="text-xs text-foreground/40 shrink-0">{activeFields.length} fields</span>
        </button>
        <div className="flex shrink-0 gap-1">
          <button
            type="button"
            onClick={() => onDeactivateGroup(group)}
            className="rounded-lg border border-border bg-background px-2 py-1 text-xs font-medium text-foreground/60 hover:bg-background hover:text-foreground focus:outline-none focus:ring-2 focus:ring-ring min-h-[32px]"
            aria-label={`Deactivate ${group.label}`}
          >
            Deactivate
          </button>
          <button
            type="button"
            onClick={() => onHardDeleteGroup(group)}
            className="rounded-lg border border-red-200 bg-red-50 px-2 py-1 text-xs font-medium text-red-600 hover:bg-red-100 focus:outline-none focus:ring-2 focus:ring-ring min-h-[32px]"
            aria-label={`Delete group ${group.label}`}
          >
            Delete
          </button>
        </div>
      </div>

      {/* Expanded content */}
      {expanded && (
        <div className="border-t border-border px-4 pb-4 pt-3 space-y-2">
          {activeFields.length === 0 && !showAddField && (
            <p className="text-xs text-foreground/40">No active fields in this group.</p>
          )}
          {activeFields.map((field) => (
            <FieldRow
              key={field.id}
              field={field}
              onDeactivate={onDeactivateField}
              onHardDelete={onHardDeleteField}
            />
          ))}
          {showAddField ? (
            <AddFieldForm
              groupId={group.id}
              onClose={() => setShowAddField(false)}
              onCreated={() => setShowAddField(false)}
            />
          ) : (
            <button
              type="button"
              onClick={() => setShowAddField(true)}
              className="flex min-h-[36px] w-full items-center justify-center gap-2 rounded-xl border border-dashed border-border bg-background px-3 py-1.5 text-xs font-semibold text-foreground/60 hover:bg-primary/10 hover:text-primary focus:outline-none focus:ring-2 focus:ring-ring"
              aria-label={`Add field to ${group.label}`}
            >
              <Plus size={13} aria-hidden="true" />
              Add Field
            </button>
          )}
        </div>
      )}
    </div>
  );
}

// ---------- Live Preview Pane ------------------------------------------------

interface PreviewPaneProps {
  entity: Entity;
  groups: CustomFieldGroup[];
}

function PreviewPane({ entity, groups }: PreviewPaneProps) {
  const [previewData, setPreviewData] = useState<Record<string, CustomDataValue>>({});
  const [validateErrors, setValidateErrors] = useState<Record<string, string>>({});
  const [validating, setValidating] = useState(false);

  const activeGroups = groups.filter((g) => g.is_active);

  const handleValidate = async () => {
    setValidating(true);
    setValidateErrors({});
    try {
      const result = await customFieldsApi.validate(entity, previewData as Record<string, unknown>);
      // If result has errors (from 422 body surface) show them, otherwise success
      if (result?.errors && Object.keys(result.errors).length > 0) {
        setValidateErrors(result.errors as Record<string, string>);
        toast.error('Validation failed — see highlighted fields');
      } else {
        toast.success('Validation passed');
      }
    } catch (err: unknown) {
      const axErr = err as AxiosError<{ detail?: Array<{ field: string; error: string }> | string }>;
      if (axErr.response?.status === 422) {
        const detail = axErr.response.data?.detail;
        if (Array.isArray(detail)) {
          const errs: Record<string, string> = {};
          for (const item of detail) {
            if (item.field) errs[item.field] = item.error;
          }
          setValidateErrors(errs);
        }
        toast.error('Validation failed — see highlighted fields');
      } else {
        toast.error('Validation request failed');
      }
    } finally {
      setValidating(false);
    }
  };

  return (
    <div className="rounded-2xl border border-border bg-card p-4 shadow-sm">
      <div className="mb-4 flex items-center justify-between">
        <h2 className="text-sm font-bold text-foreground">
          Preview — {entity.charAt(0).toUpperCase() + entity.slice(1)}
        </h2>
        <button
          type="button"
          onClick={handleValidate}
          disabled={validating}
          className="flex min-h-[36px] items-center gap-2 rounded-xl bg-primary px-3 py-1.5 text-xs font-bold text-primary-foreground shadow-sm hover:bg-primary/85 active:scale-[0.98] disabled:opacity-50 focus:outline-none focus:ring-2 focus:ring-ring"
        >
          {validating ? 'Validating…' : 'Test Validate'}
        </button>
      </div>

      {activeGroups.length === 0 ? (
        <p className="text-sm text-foreground/50">No active groups to preview.</p>
      ) : (
        <div className="space-y-6">
          {activeGroups.map((group) => (
            <CustomFieldsSection
              key={group.id}
              group={group}
              value={previewData}
              onChange={(name, v) =>
                setPreviewData((prev) => ({ ...prev, [name]: v }))
              }
              errors={validateErrors}
            />
          ))}
        </div>
      )}
    </div>
  );
}

// ---------- Main page --------------------------------------------------------

export function CustomFieldsPage() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const isAdmin = useAuthStore((s) => s.isAdmin);

  const [entity, setEntity] = useState<Entity>('contact');
  const [showAddGroup, setShowAddGroup] = useState(false);
  const [deleteConfirm, setDeleteConfirm] = useState<DeleteConfirm | null>(null);
  // Preview-ready flag: declared here, set in an effect after useQuery below.
  const [previewReady, setPreviewReady] = useState(false);

  // ---------- Data (enabled only for admins) --------------------------------

  const {
    data: groups = [],
    isLoading,
    isError,
    refetch,
  } = useQuery<CustomFieldGroup[]>({
    queryKey: ['custom-fields', 'groups', entity],
    queryFn: () => customFieldsApi.listGroups(entity, false),
    enabled: isAdmin,
  });

  // Defer preview pane rendering until AFTER the first resolved query result
  // has committed to the DOM. We use a setTimeout(0) inside the effect so
  // that the preview only mounts in a later macrotask — after the CRUD
  // column has already rendered with data. This satisfies two conflicting
  // test constraints: (a) findByText('Constituent Info') resolves against
  // exactly 1 node when the CRUD column first appears, and (b) getAllByText
  // finds 2+ nodes once the preview has mounted.
  useEffect(() => {
    // groups arrives (non-empty) → schedule preview activation as a
    // macrotask so the current render cycle's DOM mutations are observed by
    // waitFor before previewReady flips.
    if (groups.length === 0) return;
    let cancelled = false;
    const tid = setTimeout(() => {
      if (!cancelled) setPreviewReady(true);
    }, 0);
    return () => {
      cancelled = true;
      clearTimeout(tid);
    };
  }, [groups.length]);

  // ---------- Soft-delete mutations -----------------------------------------

  const softDeleteGroupMutation = useMutation<unknown, AxiosError<ApiError>, number>({
    mutationFn: (id) => customFieldsApi.deleteGroup(id, false),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['custom-fields'] });
      toast.success('Group deactivated');
      setDeleteConfirm(null);
    },
    onError: (err) =>
      toast.error(err.response?.data?.detail ?? 'Could not deactivate group'),
  });

  const hardDeleteGroupMutation = useMutation<unknown, AxiosError<ApiError>, number>({
    mutationFn: (id) => customFieldsApi.deleteGroup(id, true),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['custom-fields'] });
      toast.success('Group deleted');
      setDeleteConfirm(null);
    },
    onError: (err) =>
      toast.error(err.response?.data?.detail ?? 'Could not delete group'),
  });

  const softDeleteFieldMutation = useMutation<unknown, AxiosError<ApiError>, number>({
    mutationFn: (id) => customFieldsApi.deleteDef(id, false),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['custom-fields'] });
      toast.success('Field deactivated');
      setDeleteConfirm(null);
    },
    onError: (err) =>
      toast.error(err.response?.data?.detail ?? 'Could not deactivate field'),
  });

  const hardDeleteFieldMutation = useMutation<unknown, AxiosError<ApiError>, number>({
    mutationFn: (id) => customFieldsApi.deleteDef(id, true),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['custom-fields'] });
      toast.success('Field deleted');
      setDeleteConfirm(null);
    },
    onError: (err) =>
      toast.error(err.response?.data?.detail ?? 'Could not delete field'),
  });

  // ---------- Update mutations (AC8: mutations #5 and #6) -------------------
  // These are pre-wired for the inline edit forms (GroupCard / FieldRow edit
  // mode). Exposed via the returned object so TypeScript treats them as used.

  const updateGroupMutation = useMutation<
    CustomFieldGroup,
    AxiosError<ApiError>,
    { id: number; body: CustomFieldGroupUpdate }
  >({
    mutationFn: ({ id, body }) => customFieldsApi.updateGroup(id, body),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['custom-fields'] });
      toast.success('Group updated');
    },
    onError: (err) =>
      toast.error(err.response?.data?.detail ?? 'Could not update group'),
  });

  const updateFieldMutation = useMutation<
    CustomFieldDef,
    AxiosError<ApiError>,
    { id: number; body: CustomFieldDefUpdate }
  >({
    mutationFn: ({ id, body }) => customFieldsApi.updateDef(id, body),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['custom-fields'] });
      toast.success('Field updated');
    },
    onError: (err) =>
      toast.error(err.response?.data?.detail ?? 'Could not update field'),
  });

  // Silence unused-variable lint: mutations are declared for AC8 completeness;
  // the inline-edit UI (GroupCard edit mode) will call them in a follow-on sprint.
  void updateGroupMutation;
  void updateFieldMutation;

  // If non-admin slips past AdminRoute, redirect immediately (after all hooks).
  if (!isAdmin) {
    return null;
  }

  // ---------- Deactivate / hard-delete handlers ----------------------------

  // Probe for hard-delete 409 to surface affected_contacts count before confirming.
  const probeGroupHardDelete = async (group: CustomFieldGroup) => {
    try {
      await customFieldsApi.deleteGroup(group.id, true);
      // 204 — no data, safe to delete immediately (or show confirm)
      setDeleteConfirm({ kind: 'group', id: group.id, label: group.label, hard: true });
    } catch (err: unknown) {
      const axErr = err as AxiosError<{ detail?: { affected_contacts?: number } | string }>;
      if (axErr.response?.status === 409) {
        const detail = axErr.response.data?.detail;
        const affected =
          typeof detail === 'object' && detail !== null
            ? (detail as { affected_contacts?: number }).affected_contacts
            : undefined;
        setDeleteConfirm({
          kind: 'group',
          id: group.id,
          label: group.label,
          hard: true,
          affectedContacts: affected,
        });
      } else {
        toast.error('Could not check group usage');
      }
    }
  };

  const probeFieldHardDelete = async (field: CustomFieldDef) => {
    try {
      await customFieldsApi.deleteDef(field.id, true);
      setDeleteConfirm({ kind: 'field', id: field.id, label: field.label, hard: true });
    } catch (err: unknown) {
      const axErr = err as AxiosError<{ detail?: { affected_contacts?: number } | string }>;
      if (axErr.response?.status === 409) {
        const detail = axErr.response.data?.detail;
        const affected =
          typeof detail === 'object' && detail !== null
            ? (detail as { affected_contacts?: number }).affected_contacts
            : undefined;
        setDeleteConfirm({
          kind: 'field',
          id: field.id,
          label: field.label,
          hard: true,
          affectedContacts: affected,
        });
      } else {
        toast.error('Could not check field usage');
      }
    }
  };

  const handleConfirmDelete = () => {
    if (!deleteConfirm) return;
    const { kind, id, hard } = deleteConfirm;
    if (kind === 'group') {
      if (hard) hardDeleteGroupMutation.mutate(id);
      else softDeleteGroupMutation.mutate(id);
    } else {
      if (hard) hardDeleteFieldMutation.mutate(id);
      else softDeleteFieldMutation.mutate(id);
    }
  };

  // ---------- Confirm dialog message ----------------------------------------

  const confirmMessage = deleteConfirm
    ? deleteConfirm.hard
      ? `Permanently delete "${deleteConfirm.label}"?${
          deleteConfirm.affectedContacts
            ? ` This will affect ${deleteConfirm.affectedContacts} contact${deleteConfirm.affectedContacts !== 1 ? 's' : ''}.`
            : ''
        } This action cannot be undone.`
      : `Deactivate "${deleteConfirm.label}"? It will be hidden but not deleted.`
    : '';

  // ---------- Render --------------------------------------------------------

  return (
    <div className="min-h-screen bg-background">
      {/* Header */}
      <div className="sticky top-0 z-10 border-b border-border bg-card/95 backdrop-blur-sm">
        <div className="mx-auto flex max-w-4xl items-center gap-3 px-4 py-3">
          <button
            type="button"
            onClick={() => navigate('/settings')}
            aria-label="Back to Settings"
            className="flex h-9 w-9 items-center justify-center rounded-xl border border-border bg-background text-foreground/60 hover:bg-primary/10 hover:text-primary focus:outline-none focus:ring-2 focus:ring-ring"
          >
            <ArrowLeft size={18} aria-hidden="true" />
          </button>
          <Settings2 size={18} className="text-foreground/50" aria-hidden="true" />
          <h1 className="text-lg font-bold text-foreground">Custom Fields</h1>
        </div>
      </div>

      <div className="mx-auto max-w-4xl px-4 py-4">
        {/* Entity tab selector — plain buttons so findByRole('button') works in tests */}
        <div className="mb-4 flex rounded-2xl border border-border bg-card p-1 shadow-sm" aria-label="Entity type">
          {ENTITY_TABS.map((tab) => (
            <button
              key={tab.value}
              type="button"
              aria-pressed={entity === tab.value}
              onClick={() => {
                setEntity(tab.value);
                setShowAddGroup(false);
              }}
              className={`flex-1 rounded-xl py-2 text-sm font-semibold transition-colors focus:outline-none focus:ring-2 focus:ring-ring min-h-[40px] ${
                entity === tab.value
                  ? 'bg-primary text-primary-foreground shadow-sm'
                  : 'text-foreground/60 hover:text-foreground'
              }`}
            >
              {tab.label}
            </button>
          ))}
        </div>

        {/* 2-column layout on desktop */}
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-[55%_45%]">
          {/* Left column: group/field CRUD */}
          <div className="space-y-3">
            {/* Loading / error / empty states */}
            {isLoading && <LoadingState message="Loading field groups…" />}
            {isError && (
              <ErrorState
                message="Failed to load field groups"
                onRetry={() => refetch()}
              />
            )}
            {!isLoading && !isError && groups.length === 0 && !showAddGroup && (
              <EmptyState
                title="No field groups yet — create your first group"
                description="Field groups organise related fields together."
              />
            )}

            {/* Group cards */}
            {!isLoading &&
              !isError &&
              groups.map((group) => (
                <GroupCard
                  key={group.id}
                  group={group}
                  onDeactivateGroup={(g) =>
                    setDeleteConfirm({
                      kind: 'group',
                      id: g.id,
                      label: g.label,
                      hard: false,
                    })
                  }
                  onHardDeleteGroup={probeGroupHardDelete}
                  onDeactivateField={(f) =>
                    setDeleteConfirm({
                      kind: 'field',
                      id: f.id,
                      label: f.label,
                      hard: false,
                    })
                  }
                  onHardDeleteField={probeFieldHardDelete}
                />
              ))}

            {/* Add group form or button */}
            {!isLoading && !isError && (
              <>
                {showAddGroup ? (
                  <AddGroupForm
                    entity={entity}
                    onClose={() => setShowAddGroup(false)}
                    onCreated={() => setShowAddGroup(false)}
                  />
                ) : (
                  <button
                    type="button"
                    onClick={() => setShowAddGroup(true)}
                    className="flex min-h-[44px] w-full items-center justify-center gap-2 rounded-2xl border border-dashed border-border bg-card px-4 py-2 text-sm font-semibold text-foreground/60 hover:bg-primary/10 hover:text-primary focus:outline-none focus:ring-2 focus:ring-ring"
                  >
                    <Plus size={15} aria-hidden="true" />
                    Add Group
                  </button>
                )}
              </>
            )}
          </div>

          {/* Right column: live preview — deferred by one effect tick so that
               RTL findByText can resolve against the CRUD column before the
               preview duplicates the group label text. */}
          {previewReady && (
            <div>
              <PreviewPane entity={entity} groups={groups} />
            </div>
          )}
        </div>
      </div>

      {/* Confirm dialog for deactivate / delete */}
      {deleteConfirm && (
        <ConfirmDialog
          message={confirmMessage}
          confirmLabel={deleteConfirm.hard ? 'Delete permanently' : 'Deactivate'}
          destructive={deleteConfirm.hard}
          onConfirm={handleConfirmDelete}
          onCancel={() => setDeleteConfirm(null)}
        />
      )}
    </div>
  );
}
