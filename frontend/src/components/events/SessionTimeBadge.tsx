/**
 * SessionTimeBadge — displays a bg-accent/text-accent-foreground pill for session_time.
 * Spec §5.2: bg-accent text-accent-foreground.
 */

const SESSION_TIME_LABELS: Record<string, string> = {
  '8AM': '8AM',
  '10AM': '10AM',
  '3PM': '3PM',
};

export interface SessionTimeBadgeProps {
  sessionTime: string;
}

export function SessionTimeBadge({ sessionTime }: SessionTimeBadgeProps) {
  const label = SESSION_TIME_LABELS[sessionTime] ?? sessionTime;

  return (
    <span
      data-session-time={sessionTime}
      className="inline-flex items-center rounded-full bg-accent px-2 py-0.5 text-xs font-medium text-accent-foreground"
    >
      {label}
    </span>
  );
}
