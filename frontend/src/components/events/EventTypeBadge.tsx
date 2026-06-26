/**
 * EventTypeBadge — displays a colour-coded pill for event_type.
 * Colours follow spec §5.2 design tokens.
 */

// Spec §5.2 event type values and their display labels
const EVENT_TYPE_LABELS: Record<string, string> = {
  'Sunday Celebration': 'Sunday Celebration',
  'Prayer Meeting': 'Prayer Meeting',
  'Powerhouse': 'Powerhouse',
  'Community Meeting': 'Community Meeting',
  'Conference': 'Conference',
  'Event': 'Event',
};

// Spec §5.2 Tailwind classes per type
const EVENT_TYPE_CLASSES: Record<string, string> = {
  'Sunday Celebration': 'bg-primary/20 text-primary',
  'Prayer Meeting': 'bg-blue-500/20 text-blue-700 dark:text-blue-300',
  'Powerhouse': 'bg-purple-500/20 text-purple-700 dark:text-purple-300',
  'Community Meeting': 'bg-green-500/20 text-green-700 dark:text-green-300',
  'Conference': 'bg-muted text-muted-foreground',
  'Event': 'bg-muted text-muted-foreground',
};

export interface EventTypeBadgeProps {
  eventType: string;
}

export function EventTypeBadge({ eventType }: EventTypeBadgeProps) {
  const label = EVENT_TYPE_LABELS[eventType] ?? eventType;
  const cls = EVENT_TYPE_CLASSES[eventType] ?? 'bg-muted text-muted-foreground';

  return (
    <span
      data-event-type={eventType}
      className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${cls}`}
    >
      {label}
    </span>
  );
}
