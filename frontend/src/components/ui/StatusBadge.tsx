/* eslint-disable react-refresh/only-export-components */
export type Tone = 'active' | 'warning' | 'danger' | 'error' | 'muted';

const toneClasses: Record<Tone, string> = {
  active: 'bg-primary/10 text-primary',
  warning: 'bg-amber-500/10 text-amber-600 dark:text-amber-400',
  danger: 'bg-orange-500/10 text-orange-600 dark:text-orange-400',
  error: 'bg-red-500/10 text-red-600 dark:text-red-400',
  muted: 'bg-muted text-foreground/50',
};

interface StatusBadgeProps {
  label: string;
  tone: Tone;
}

export function StatusBadge({ label, tone }: StatusBadgeProps) {
  return (
    <span
      className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${toneClasses[tone]}`}
    >
      {label}
    </span>
  );
}

/**
 * Maps a contact tier string (case-insensitive) to a display tone.
 *
 * tier0  → active
 * tier1  → warning
 * tier2  → danger
 * tier3  → error
 * inactive / null / undefined → muted
 */
export function getTierTone(tier: string | null | undefined): Tone {
  if (!tier) return 'muted';
  switch (tier.toLowerCase()) {
    case 'tier0':
      return 'active';
    case 'tier1':
      return 'warning';
    case 'tier2':
      return 'danger';
    case 'tier3':
      return 'error';
    default:
      return 'muted';
  }
}
