import { create } from 'zustand';
import type { User } from '@/types';

/**
 * Inline base64url JWT payload decoder — no external dependency.
 * Returns the `role` field from the payload, or `undefined` on any error
 * (malformed token, invalid JSON, missing field). Never throws.
 */
function decodeJwtRole(token: string): string | undefined {
  try {
    const parts = token.split('.');
    if (parts.length !== 3) return undefined;
    // Normalize base64url → base64 (pad to multiple of 4)
    const b64 = parts[1].replace(/-/g, '+').replace(/_/g, '/');
    const padded = b64 + '='.repeat((4 - (b64.length % 4)) % 4);
    const payload = JSON.parse(atob(padded));
    const role = payload?.role;
    return typeof role === 'string' ? role : undefined;
  } catch {
    return undefined;
  }
}

/** True for admin and volunteer roles; false for viewer and unauthenticated. */
function deriveIsVolunteer(role: string | undefined): boolean {
  return role === 'admin' || role === 'volunteer';
}

interface AuthState {
  user: User | null;
  token: string | null;
  isAdmin: boolean;
  /** True for admin and volunteer roles; false for viewer and unauthenticated. */
  isVolunteer: boolean;
  isAuthenticated: boolean;
  /** false until the initial /auth/refresh attempt completes; route guards wait for this */
  authReady: boolean;
  login: (user: User, token: string) => void;
  logout: () => void;
  setUser: (user: User | null) => void;
  setToken: (token: string) => void;
  setAuthReady: () => void;
}

export const useAuthStore = create<AuthState>((set) => ({
  user: null,
  token: null,
  isAdmin: false,
  isVolunteer: false,
  isAuthenticated: false,
  authReady: false,
  login: (user, token) => {
    set({
      user,
      token,
      isAdmin: user.role === 'admin',
      isVolunteer: deriveIsVolunteer(user.role),
      isAuthenticated: true,
      authReady: true,
    });
  },
  logout: () => {
    set({
      user: null,
      token: null,
      isAdmin: false,
      isVolunteer: false,
      isAuthenticated: false,
      authReady: true,
    });
  },
  setUser: (user) => {
    set({
      user,
      isAdmin: user?.role === 'admin',
      isVolunteer: deriveIsVolunteer(user?.role),
      isAuthenticated: !!user,
    });
  },
  setToken: (token) => {
    const role = decodeJwtRole(token);
    set({ token, isAuthenticated: true, isAdmin: role === 'admin', isVolunteer: deriveIsVolunteer(role) });
  },
  setAuthReady: () => {
    set({ authReady: true });
  },
}));
