import { useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import { ArrowLeft, UserPlus, UserX, RotateCcw, Copy, Shield, User } from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import { api } from '@/services/api';
import { ConfirmDialog } from '@/components/ui/ConfirmDialog';

interface UserRecord {
  id: number;
  email: string;
  name: string | null;
  role: 'admin' | 'volunteer';
  is_active: boolean;
}

interface AddForm {
  email: string;
  name: string;
  role: 'volunteer' | 'admin';
  temporary_password: string;
  confirm_password: string;
}

const PASSWORD_RULES = [
  { label: 'At least 12 characters', test: (v: string) => v.length >= 12 },
  { label: 'One uppercase letter', test: (v: string) => /[A-Z]/.test(v) },
  { label: 'One lowercase letter', test: (v: string) => /[a-z]/.test(v) },
  { label: 'One number', test: (v: string) => /[0-9]/.test(v) },
  { label: 'One special character', test: (v: string) => /[!@#$%^&*()_+\-=[\]{}|;':",./<>?]/.test(v) },
];

export function UserManagementPage() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [showAddForm, setShowAddForm] = useState(false);
  const [adding, setAdding] = useState(false);
  const [resetLinks, setResetLinks] = useState<Record<number, string>>({});
  const [confirmDeactivate, setConfirmDeactivate] = useState<{ id: number; email: string } | null>(null);
  const [form, setForm] = useState<AddForm>({
    email: '', name: '', role: 'volunteer', temporary_password: '', confirm_password: '',
  });

  const { data: users = [], isLoading } = useQuery<UserRecord[]>({
    queryKey: ['users'],
    queryFn: () => api.get('/auth/users').then((r) => r.data),
  });

  const passwordValid = PASSWORD_RULES.every((r) => r.test(form.temporary_password));
  const passwordsMatch = form.temporary_password === form.confirm_password;

  const addUser = async () => {
    if (!passwordValid || !passwordsMatch) return;
    setAdding(true);
    try {
      await api.post('/auth/add-volunteer', {
        email: form.email,
        name: form.name,
        role: form.role,
        temporary_password: form.temporary_password,
      });
      toast.success('Volunteer added');
      setForm({ email: '', name: '', role: 'volunteer', temporary_password: '', confirm_password: '' });
      setShowAddForm(false);
      queryClient.invalidateQueries({ queryKey: ['users'] });
    } catch (err: any) {
      toast.error(err.response?.data?.detail || 'Failed to add volunteer');
    } finally {
      setAdding(false);
    }
  };

  const deactivate = async (userId: number) => {
    try {
      await api.post(`/auth/deactivate/${userId}`);
      setConfirmDeactivate(null);
      toast.success('Account deactivated');
      queryClient.invalidateQueries({ queryKey: ['users'] });
    } catch (err: any) {
      toast.error(err.response?.data?.detail || 'Failed to deactivate');
    }
  };

  const generateReset = async (userId: number, email: string) => {
    try {
      const res = await api.post('/auth/reset-password', { email });
      const link = res.data.reset_link;
      setResetLinks((prev) => ({ ...prev, [userId]: link }));
      toast.success('Reset link generated');
    } catch (err: any) {
      toast.error(err.response?.data?.detail || 'Failed to generate reset link');
    }
  };

  const inputClass = 'h-11 w-full rounded-xl border border-border bg-background px-4 text-sm text-foreground focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/30 placeholder:text-foreground/40';

  return (
    <div className="flex h-screen flex-col pb-20">
      <header className="border-b border-border bg-card/95 px-4 py-3 backdrop-blur-sm">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <button onClick={() => navigate('/settings')} aria-label="Back to settings" className="text-foreground/50 hover:text-foreground">
              <ArrowLeft size={20} aria-hidden="true" />
            </button>
            <h1 className="text-lg font-bold text-foreground">Manage Users</h1>
          </div>
          <button
            onClick={() => setShowAddForm(!showAddForm)}
            className="flex items-center gap-1.5 rounded-xl bg-primary px-3 py-1.5 text-xs font-bold text-primary-foreground shadow-sm transition-all hover:bg-primary/85 active:scale-[0.98]"
          >
            <UserPlus size={14} aria-hidden="true" />
            Add Volunteer
          </button>
        </div>
      </header>

      <main className="flex-1 overflow-y-auto px-3 pt-3">
        {/* Add volunteer form */}
        {showAddForm && (
          <div className="mb-4 rounded-2xl border border-border bg-card p-4 shadow-sm">
            <h2 className="mb-3 text-sm font-bold text-foreground">New Volunteer</h2>
            <div className="space-y-3">
              <input type="text" placeholder="Full name" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} className={inputClass} aria-label="Full name" />
              <input type="email" placeholder="Email address" value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} className={inputClass} aria-label="Email" />
              <select
                value={form.role}
                onChange={(e) => setForm({ ...form, role: e.target.value as 'volunteer' | 'admin' })}
                className={inputClass}
                aria-label="Role"
              >
                <option value="volunteer">Volunteer</option>
                <option value="admin">Admin</option>
              </select>
              <input type="password" placeholder="Temporary password" value={form.temporary_password} onChange={(e) => setForm({ ...form, temporary_password: e.target.value })} className={inputClass} aria-label="Temporary password" />
              <input type="password" placeholder="Confirm password" value={form.confirm_password} onChange={(e) => setForm({ ...form, confirm_password: e.target.value })} className={inputClass} aria-label="Confirm password" />

              {form.temporary_password && (
                <div className="rounded-xl bg-background p-3">
                  <div className="space-y-1">
                    {PASSWORD_RULES.map((rule) => (
                      <div key={rule.label} className={`flex items-center gap-2 text-xs ${rule.test(form.temporary_password) ? 'text-green-600' : 'text-foreground/40'}`}>
                        <span className={`h-1.5 w-1.5 rounded-full ${rule.test(form.temporary_password) ? 'bg-green-500' : 'bg-foreground/20'}`} aria-hidden="true" />
                        {rule.label}
                      </div>
                    ))}
                    {form.confirm_password && !passwordsMatch && (
                      <p className="text-xs text-red-500 mt-1">Passwords do not match</p>
                    )}
                  </div>
                </div>
              )}

              <div className="flex gap-2">
                <button onClick={() => setShowAddForm(false)} className="flex h-10 flex-1 items-center justify-center rounded-xl border border-border bg-card text-xs font-semibold text-foreground hover:bg-background">Cancel</button>
                <button
                  onClick={addUser}
                  disabled={adding || !passwordValid || !passwordsMatch || !form.email || !form.name}
                  className="flex h-10 flex-1 items-center justify-center rounded-xl bg-primary text-xs font-bold text-primary-foreground shadow-sm disabled:opacity-50 hover:bg-primary/85"
                >
                  {adding ? 'Adding…' : 'Add Volunteer'}
                </button>
              </div>
            </div>
          </div>
        )}

        {isLoading ? (
          <div className="py-16 text-center text-sm text-foreground/50">Loading…</div>
        ) : (
          <div className="space-y-2 pb-4">
            {users.map((u) => (
              <div key={u.id} className={`rounded-2xl border bg-card p-4 shadow-sm ${u.is_active ? 'border-border' : 'border-border/40 opacity-60'}`}>
                <div className="flex items-center gap-3">
                  <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-primary/10">
                    {u.role === 'admin' ? <Shield size={18} className="text-primary" aria-hidden="true" /> : <User size={18} className="text-foreground/40" aria-hidden="true" />}
                  </div>
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-sm font-bold text-foreground">{u.name || u.email}</p>
                    <p className="truncate text-xs text-foreground/50">{u.email}</p>
                    <div className="mt-0.5 flex items-center gap-2">
                      <span className={`rounded-full px-2 py-0.5 text-[10px] font-bold uppercase ${u.role === 'admin' ? 'bg-primary/10 text-primary' : 'bg-background text-foreground/50'}`}>{u.role}</span>
                      {!u.is_active && <span className="rounded-full bg-red-100 px-2 py-0.5 text-[10px] font-bold text-red-600">Deactivated</span>}
                    </div>
                  </div>
                  <div className="flex gap-1.5">
                    <button
                      onClick={() => generateReset(u.id, u.email)}
                      aria-label={`Generate reset link for ${u.email}`}
                      className="flex h-8 w-8 items-center justify-center rounded-xl border border-border bg-background text-foreground/50 hover:bg-primary/10 hover:text-primary"
                      title="Generate password reset link"
                    >
                      <RotateCcw size={14} aria-hidden="true" />
                    </button>
                    {u.is_active && (
                      <button
                        onClick={() => setConfirmDeactivate({ id: u.id, email: u.email })}
                        aria-label={`Deactivate ${u.email}`}
                        className="flex h-8 w-8 items-center justify-center rounded-xl border border-red-200 bg-red-50 text-red-500 hover:bg-red-100"
                        title="Deactivate account"
                      >
                        <UserX size={14} aria-hidden="true" />
                      </button>
                    )}
                  </div>
                </div>

                {resetLinks[u.id] && (
                  <div className="mt-3 flex items-center gap-2 rounded-xl bg-background p-2">
                    <p className="flex-1 truncate font-mono text-xs text-foreground/70">{window.location.origin}{resetLinks[u.id]}</p>
                    <button
                      onClick={() => {
                        navigator.clipboard.writeText(`${window.location.origin}${resetLinks[u.id]}`);
                        toast.success('Copied!');
                      }}
                      aria-label="Copy reset link"
                      className="shrink-0"
                    >
                      <Copy size={14} className="text-foreground/40 hover:text-foreground" aria-hidden="true" />
                    </button>
                  </div>
                )}
              </div>
            ))}
          </div>
        )}
      </main>

      {confirmDeactivate && (
        <ConfirmDialog
          message={`Deactivate ${confirmDeactivate.email}? Their account will be disabled but history is retained.`}
          confirmLabel="Deactivate"
          destructive
          onConfirm={() => deactivate(confirmDeactivate.id)}
          onCancel={() => setConfirmDeactivate(null)}
        />
      )}
    </div>
  );
}
