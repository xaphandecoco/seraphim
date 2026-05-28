import { create } from 'zustand';

interface TaskState {
  pendingCount: number;
  setPendingCount: (count: number) => void;
}

export const useTaskStore = create<TaskState>((set) => ({
  pendingCount: 0,
  setPendingCount: (count) => set({ pendingCount: count }),
}));
