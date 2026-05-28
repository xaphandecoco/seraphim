import { create } from 'zustand';
import type { User } from '@/types';

interface AuthState {
  user: User | null;
  token: string | null;
  isAdmin: boolean;
  isAuthenticated: boolean;
  login: (user: User, token: string) => void;
  logout: () => void;
  setUser: (user: User | null) => void;
}

export const useAuthStore = create<AuthState>((set) => ({
  user: null,
  token: null,
  isAdmin: false,
  isAuthenticated: false,
  login: (user, token) => {
    set({ user, token, isAdmin: user.role === 'admin', isAuthenticated: true });
  },
  logout: () => {
    set({ user: null, token: null, isAdmin: false, isAuthenticated: false });
  },
  setUser: (user) => {
    set({ user, isAdmin: user?.role === 'admin', isAuthenticated: !!user });
  },
}));
