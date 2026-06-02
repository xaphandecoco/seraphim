import { Component, type ErrorInfo, type ReactNode } from 'react';

interface Props {
  children: ReactNode;
}

interface State {
  hasError: boolean;
  error?: Error;
}

/**
 * Catches render-time errors anywhere in the tree and shows a friendly fallback
 * with a reload button, instead of a blank white screen.
 */
export class ErrorBoundary extends Component<Props, State> {
  state: State = { hasError: false };

  static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    // Surface to the console; a real deployment can forward this to Sentry.
    console.error('Unhandled UI error:', error, info);
  }

  render() {
    if (this.state.hasError) {
      return (
        <div className="flex min-h-screen flex-col items-center justify-center bg-background p-6 text-center">
          <h1 className="text-lg font-bold text-foreground">Something went wrong</h1>
          <p className="mt-2 max-w-sm text-sm text-foreground/60">
            The app hit an unexpected error. Reloading usually fixes it. If it keeps happening,
            contact your administrator.
          </p>
          <button
            onClick={() => window.location.reload()}
            className="mt-5 rounded-xl bg-primary px-5 py-2.5 text-sm font-bold text-primary-foreground shadow-sm transition-all hover:bg-primary/85 active:scale-[0.98]"
          >
            Reload
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}
