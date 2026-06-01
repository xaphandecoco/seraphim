import { Check, Pencil, UserPlus, X } from 'lucide-react';
import type { Task } from '@/types';

interface TaskCardProps {
  task: Task;
  onConfirm: (taskId: number) => void;
  onEdit: (task: Task) => void;
  onAdd: (task: Task) => void;
  onSkip: (taskId: number) => void;
}

function getTierColor(tier: Task['tier']) {
  switch (tier) {
    case '100':
      return 'bg-green-100 text-green-700 border-green-200';
    case '91-99':
      return 'bg-amber-100 text-amber-700 border-amber-200';
    case 'below90':
      return 'bg-red-100 text-red-600 border-red-200';
    default:
      return 'bg-gray-100 text-gray-600 border-gray-200';
  }
}

function getTierLabel(tier: Task['tier']) {
  switch (tier) {
    case '100':
      return 'Auto';
    case '91-99':
      return '1-Vol';
    case 'below90':
      return '2-Vol';
    default:
      return 'Unknown';
  }
}

export function TaskCard({ task, onConfirm, onEdit, onAdd, onSkip }: TaskCardProps) {
  const imageUrl = task.face_thumbnail_path
    ? `/api${task.face_thumbnail_path}`
    : undefined;

  return (
    <div className="mx-3 mb-3 rounded-2xl border border-border bg-card p-4 shadow-sm transition-transform active:scale-[0.99]">
      <div className="flex gap-3">
        {/* Face thumbnail */}
        <div className="shrink-0">
          {imageUrl ? (
            <img
              src={imageUrl}
              alt="Face"
              className="h-20 w-20 rounded-xl object-cover"
              loading="lazy"
            />
          ) : (
            <div className="flex h-20 w-20 items-center justify-center rounded-xl bg-background">
              <span className="text-xs text-foreground/40">No image</span>
            </div>
          )}
        </div>

        {/* Info */}
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <h3 className="truncate text-base font-bold text-foreground">
              {task.matched_name || 'Unknown'}
            </h3>
            <span
              className={`shrink-0 rounded-full border px-2 py-0.5 text-[10px] font-bold uppercase ${getTierColor(
                task.tier
              )}`}
            >
              {getTierLabel(task.tier)}
            </span>
          </div>

          {typeof task.confidence === 'number' && (
            <p className="mt-0.5 text-xs text-foreground/50">
              Similarity: {(task.confidence * 100).toFixed(1)}%
            </p>
          )}

          <p className="mt-1 text-xs text-foreground/50">
            {task.camera_name}
          </p>
          <p className="text-xs text-foreground/50">
            {task.detected_at ? new Date(task.detected_at).toLocaleString() : '—'}
          </p>
        </div>
      </div>

      {/* Actions */}
      <div className="mt-3 grid grid-cols-4 gap-2">
        <button
          onClick={() => onConfirm(task.id)}
          className="flex min-h-[44px] items-center justify-center gap-1 rounded-xl bg-primary text-sm font-bold text-foreground shadow-sm transition-all hover:bg-primary/85 active:scale-[0.98]"
        >
          <Check size={16} />
          <span className="text-xs">Confirm</span>
        </button>
        <button
          onClick={() => onEdit(task)}
          className="flex min-h-[44px] items-center justify-center gap-1 rounded-xl bg-background text-sm font-semibold text-foreground border border-border transition-all hover:bg-primary/20 active:scale-[0.98]"
        >
          <Pencil size={16} />
          <span className="text-xs">Edit</span>
        </button>
        <button
          onClick={() => onAdd(task)}
          className="flex min-h-[44px] items-center justify-center gap-1 rounded-xl bg-green-50 text-sm font-semibold text-green-700 border border-green-200 transition-all hover:bg-green-100 active:scale-[0.98]"
        >
          <UserPlus size={16} />
          <span className="text-xs">Add</span>
        </button>
        <button
          onClick={() => onSkip(task.id)}
          className="flex min-h-[44px] items-center justify-center gap-1 rounded-xl bg-gray-50 text-sm font-semibold text-gray-500 border border-gray-200 transition-all hover:bg-gray-100 active:scale-[0.98]"
        >
          <X size={16} />
          <span className="text-xs">Skip</span>
        </button>
      </div>
    </div>
  );
}
