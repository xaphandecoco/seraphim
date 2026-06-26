import type { ReactNode } from 'react';

/** Shared input class for consistent styling across all form controls. */
export const inputClass =
  'w-full rounded-xl border border-border bg-background px-3 py-2 text-sm text-foreground placeholder:text-foreground/40 focus:outline-none focus:ring-2 focus:ring-ring disabled:opacity-50';

/** Shared label class for consistent styling across all form labels. */
export const labelClass = 'block text-sm font-medium text-foreground';

interface FormFieldProps {
  label: string;
  htmlFor: string;
  required?: boolean;
  error?: string;
  hint?: string;
  children: ReactNode;
}

export function FormField({
  label,
  htmlFor,
  required,
  error,
  hint,
  children,
}: FormFieldProps) {
  return (
    <div className="flex flex-col gap-1">
      <label htmlFor={htmlFor} className={labelClass}>
        {label}
        {required && (
          <span className="ml-0.5 text-red-500" aria-hidden="true">
            *
          </span>
        )}
      </label>

      {/* Control slot */}
      {children}

      {hint && !error && (
        <p className="text-xs text-foreground/50">{hint}</p>
      )}

      {error && (
        <p className="text-xs text-red-500" role="alert">
          {error}
        </p>
      )}
    </div>
  );
}
