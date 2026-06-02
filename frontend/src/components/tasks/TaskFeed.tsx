import { useCallback, useEffect, useRef, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import { SSEClient } from '@/services/sse';
import { api } from '@/services/api';
import { useTaskStore } from '@/store/taskStore';
import { useAuthStore } from '@/store/authStore';
import { TaskCard } from './TaskCard';
import { MemberSearchModal } from './MemberSearchModal';
import type { Task, Member } from '@/types';

async function fetchTasks(): Promise<Task[]> {
  const res = await api.get('/tasks');
  return res.data.items || [];
}

export function TaskFeed() {
  const queryClient = useQueryClient();
  const setPendingCount = useTaskStore((s) => s.setPendingCount);
  const getToken = useAuthStore((s) => s.token);
  const [selectedTask, setSelectedTask] = useState<Task | null>(null);
  const [modalMode, setModalMode] = useState<'edit' | 'add' | null>(null);
  const sseRef = useRef<SSEClient | null>(null);

  const { data: tasks = [] } = useQuery({
    queryKey: ['tasks'],
    queryFn: fetchTasks,
    // Fallback polling: self-heals if SSE connection drops (e.g. proxy timeout)
    refetchInterval: 15_000,
    refetchIntervalInBackground: false,
  });

  useEffect(() => {
    const sse = new SSEClient('/api/tasks/feed', () => useAuthStore.getState().token);
    sseRef.current = sse;

    sse.on('message', (event) => {
      try {
        const parsed = JSON.parse(event.data);
        // new_task: a new task arrived
        if (parsed.type === 'new_task' && parsed.data) {
          queryClient.setQueryData<Task[]>(['tasks'], (old) => {
            if (!old) return [parsed.data];
            return [parsed.data, ...old];
          });
        }
        // task_update: a task status changed
        else if (parsed.type === 'task_update') {
          queryClient.invalidateQueries({ queryKey: ['tasks'] });
        }
        // pending_count: explicit count update
        else if (parsed.type === 'pending_count') {
          setPendingCount(parsed.pending_count ?? 0);
        }
      } catch {
        // ignore parse errors
      }
    });

    sse.connect();

    return () => {
      sse.close();
    };
  }, [queryClient, setPendingCount]);

  const handleConfirm = useCallback(
    async (taskId: number) => {
      try {
        await api.post(`/tasks/${taskId}/confirm`);
        queryClient.setQueryData<Task[]>(['tasks'], (old) => {
          if (!old) return [];
          return old.filter((t) => t.id !== taskId);
        });
      } catch (err: any) {
        toast.error(err.response?.data?.detail || 'Could not confirm task');
      }
    },
    [queryClient]
  );

  const handleSkip = useCallback(
    async (taskId: number) => {
      try {
        await api.post(`/tasks/${taskId}/skip`);
        queryClient.setQueryData<Task[]>(['tasks'], (old) => {
          if (!old) return [];
          return old.filter((t) => t.id !== taskId);
        });
      } catch (err: any) {
        toast.error(err.response?.data?.detail || 'Could not skip task');
      }
    },
    [queryClient]
  );

  const handleEdit = useCallback((task: Task) => {
    setSelectedTask(task);
    setModalMode('edit');
  }, []);

  const handleAdd = useCallback((task: Task) => {
    setSelectedTask(task);
    setModalMode('add');
  }, []);

  const handleMemberSelect = useCallback(
    async (member: Member) => {
      if (!selectedTask || !modalMode) return;
      const endpoint = modalMode === 'edit' ? 'edit' : 'add';
      try {
        await api.post(`/tasks/${selectedTask.id}/${endpoint}`, {
          member_id: member.contact_id,
        });
        queryClient.setQueryData<Task[]>(['tasks'], (old) => {
          if (!old) return [];
          return old.filter((t) => t.id !== selectedTask.id);
        });
        setSelectedTask(null);
        setModalMode(null);
      } catch (err: any) {
        toast.error(err.response?.data?.detail || 'Could not update task');
      }
    },
    [selectedTask, modalMode, queryClient]
  );

  const handleModalClose = useCallback(() => {
    setSelectedTask(null);
    setModalMode(null);
  }, []);

  return (
    <div className="h-full overflow-y-auto scroll-smooth pb-4">
      {tasks.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-16 text-foreground/50">
          <p className="text-sm font-medium">No pending tasks</p>
          <p className="mt-1 text-xs">New recognition tasks will appear here</p>
        </div>
      ) : (
        <div className="pt-2">
          {tasks.map((task) => (
            <TaskCard
              key={task.id}
              task={task}
              onConfirm={handleConfirm}
              onEdit={handleEdit}
              onAdd={handleAdd}
              onSkip={handleSkip}
            />
          ))}
        </div>
      )}

      {modalMode && selectedTask && (
        <MemberSearchModal
          mode={modalMode}
          onSelect={handleMemberSelect}
          onClose={handleModalClose}
        />
      )}
    </div>
  );
}
