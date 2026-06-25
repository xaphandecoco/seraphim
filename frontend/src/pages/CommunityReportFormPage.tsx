import { useState } from 'react';
import { useNavigate, Link } from 'react-router-dom';
import { toast } from 'sonner';
import { Loader2, ArrowLeft } from 'lucide-react';
import { createCommunityReport } from '@/services/communityReports';
import type { CommunityReportCreate } from '@/types/nameMatch';

export function CommunityReportFormPage() {
  const navigate = useNavigate();

  const [formData, setFormData] = useState<{
    event_title: string;
    date_of_activity: string;
    zone: string;
    attendee_count: string;
    event_leader_name: string;
    attendee_names_text: string;
    topics: string;
    prayer_items: string;
    remarks: string;
  }>({
    event_title: '',
    date_of_activity: '',
    zone: '',
    attendee_count: '',
    event_leader_name: '',
    attendee_names_text: '',
    topics: '',
    prayer_items: '',
    remarks: '',
  });

  const [submitting, setSubmitting] = useState(false);
  const [errors, setErrors] = useState<Record<string, string>>({});

  const validate = (): boolean => {
    const newErrors: Record<string, string> = {};
    if (!formData.date_of_activity) {
      newErrors.date_of_activity = 'Date of activity is required.';
    }
    if (!formData.event_title && !formData.attendee_names_text.trim()) {
      newErrors.event_title = 'Provide an event title or an attendee list.';
    }
    setErrors(newErrors);
    return Object.keys(newErrors).length === 0;
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!validate()) return;

    const attendeeLines = formData.attendee_names_text
      .split('\n')
      .map((l) => l.trim())
      .filter(Boolean);

    const payload: CommunityReportCreate = {
      event_title: formData.event_title || undefined,
      date_of_activity: formData.date_of_activity,
      zone: formData.zone || undefined,
      attendee_count: formData.attendee_count !== '' ? Number(formData.attendee_count) : undefined,
      event_leader_name: formData.event_leader_name || undefined,
      attendee_names: attendeeLines.length > 0 ? attendeeLines : undefined,
      topics: formData.topics || undefined,
      prayer_items: formData.prayer_items || undefined,
      remarks: formData.remarks || undefined,
    };

    setSubmitting(true);
    try {
      const report = await createCommunityReport(payload);
      toast.success('Report submitted');
      navigate(`/community-reports/${report.id}`);
    } catch (err: unknown) {
      const axiosErr = err as { response?: { status?: number; data?: { detail?: string | Array<{ msg: string }> } } };
      if (axiosErr.response?.status === 422) {
        const detail = axiosErr.response.data?.detail;
        if (Array.isArray(detail)) {
          toast.error(detail.map((d) => d.msg).join(', '));
        } else {
          toast.error(detail ?? 'Validation error. Please check your inputs.');
        }
      } else {
        const detail = axiosErr.response?.data?.detail;
        toast.error(typeof detail === 'string' ? detail : 'Failed to submit report');
      }
    } finally {
      setSubmitting(false);
    }
  };

  const setField = (field: keyof typeof formData) => (
    e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>
  ) => {
    setFormData((prev) => ({ ...prev, [field]: e.target.value }));
    if (errors[field]) setErrors((prev) => ({ ...prev, [field]: '' }));
  };

  return (
    <div className="flex min-h-screen flex-col bg-background">
      <header className="border-b border-border bg-card/95 px-4 py-3 backdrop-blur-sm">
        <div className="mx-auto flex max-w-2xl items-center gap-3">
          <Link
            to="/community-reports"
            aria-label="Back to community reports"
            className="flex h-9 w-9 items-center justify-center rounded-xl border border-border bg-background text-foreground/60 hover:bg-primary/10 hover:text-primary focus:outline-none focus:ring-2 focus:ring-ring"
          >
            <ArrowLeft size={18} aria-hidden="true" />
          </Link>
          <h1 className="text-lg font-bold text-foreground">New Community Report</h1>
        </div>
      </header>

      <main className="mx-auto w-full max-w-2xl flex-1 p-4 pb-24">
        <form onSubmit={handleSubmit} noValidate className="space-y-6">
          {/* Section A: Event info */}
          <section aria-label="Event information" className="rounded-2xl border border-border bg-card p-5">
            <h2 className="mb-4 text-sm font-bold uppercase tracking-wide text-muted-foreground">Event</h2>
            <div className="space-y-4">
              <div>
                <label htmlFor="event-title" className="mb-1 block text-sm font-semibold text-foreground">
                  Event title
                </label>
                <p className="mb-1.5 text-xs text-muted-foreground">Or select from events if applicable</p>
                <input
                  id="event-title"
                  type="text"
                  value={formData.event_title}
                  onChange={setField('event_title')}
                  placeholder="e.g. Sunday Celebration"
                  className={`w-full rounded-xl border px-3 py-2 text-sm text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring ${errors.event_title ? 'border-red-400' : 'border-border bg-background'}`}
                />
                {errors.event_title && (
                  <p className="mt-1 text-xs text-red-600" role="alert">{errors.event_title}</p>
                )}
              </div>

              <div>
                <label htmlFor="date-of-activity" className="mb-1 block text-sm font-semibold text-foreground">
                  Date of activity <span aria-hidden="true" className="text-red-500">*</span>
                </label>
                <input
                  id="date-of-activity"
                  type="date"
                  value={formData.date_of_activity}
                  onChange={setField('date_of_activity')}
                  required
                  aria-required="true"
                  className={`w-full rounded-xl border px-3 py-2 text-sm text-foreground focus:outline-none focus:ring-2 focus:ring-ring ${errors.date_of_activity ? 'border-red-400' : 'border-border bg-background'}`}
                />
                {errors.date_of_activity && (
                  <p className="mt-1 text-xs text-red-600" role="alert">{errors.date_of_activity}</p>
                )}
              </div>

              <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                <div>
                  <label htmlFor="zone" className="mb-1 block text-sm font-semibold text-foreground">Zone</label>
                  <input
                    id="zone"
                    type="text"
                    value={formData.zone}
                    onChange={setField('zone')}
                    placeholder="e.g. North"
                    className="w-full rounded-xl border border-border bg-background px-3 py-2 text-sm text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring"
                  />
                </div>
                <div>
                  <label htmlFor="attendee-count" className="mb-1 block text-sm font-semibold text-foreground">Attendee count</label>
                  <input
                    id="attendee-count"
                    type="number"
                    min="0"
                    value={formData.attendee_count}
                    onChange={setField('attendee_count')}
                    placeholder="0"
                    className="w-full rounded-xl border border-border bg-background px-3 py-2 text-sm text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring"
                  />
                </div>
              </div>

              <div>
                <label htmlFor="event-leader" className="mb-1 block text-sm font-semibold text-foreground">Event leader name</label>
                <input
                  id="event-leader"
                  type="text"
                  value={formData.event_leader_name}
                  onChange={setField('event_leader_name')}
                  placeholder="Full name"
                  className="w-full rounded-xl border border-border bg-background px-3 py-2 text-sm text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring"
                />
              </div>
            </div>
          </section>

          {/* Section B: Attendees */}
          <section aria-label="Attendee list" className="rounded-2xl border border-border bg-card p-5">
            <h2 className="mb-1 text-sm font-bold uppercase tracking-wide text-muted-foreground">Attendees</h2>
            <p className="mb-3 text-xs text-muted-foreground">Enter one name per line. Leave blank if no digital attendee list.</p>
            <div>
              <label htmlFor="attendee-names" className="sr-only">Attendee names (one per line)</label>
              <textarea
                id="attendee-names"
                rows={8}
                value={formData.attendee_names_text}
                onChange={setField('attendee_names_text')}
                placeholder="Juan dela Cruz&#10;Maria Santos&#10;Pedro Reyes"
                className="w-full rounded-xl border border-border bg-background px-3 py-2 text-sm text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring"
              />
            </div>
          </section>

          {/* Section C: Qualitative */}
          <section aria-label="Qualitative information" className="rounded-2xl border border-border bg-card p-5">
            <h2 className="mb-4 text-sm font-bold uppercase tracking-wide text-muted-foreground">Notes</h2>
            <div className="space-y-4">
              <div>
                <label htmlFor="topics" className="mb-1 block text-sm font-semibold text-foreground">Topics</label>
                <textarea
                  id="topics"
                  rows={3}
                  value={formData.topics}
                  onChange={setField('topics')}
                  placeholder="Topics covered in the activity…"
                  className="w-full rounded-xl border border-border bg-background px-3 py-2 text-sm text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring"
                />
              </div>
              <div>
                <label htmlFor="prayer-items" className="mb-1 block text-sm font-semibold text-foreground">Prayer items</label>
                <textarea
                  id="prayer-items"
                  rows={3}
                  value={formData.prayer_items}
                  onChange={setField('prayer_items')}
                  placeholder="Prayer requests and praises…"
                  className="w-full rounded-xl border border-border bg-background px-3 py-2 text-sm text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring"
                />
              </div>
              <div>
                <label htmlFor="remarks" className="mb-1 block text-sm font-semibold text-foreground">Remarks</label>
                <textarea
                  id="remarks"
                  rows={3}
                  value={formData.remarks}
                  onChange={setField('remarks')}
                  placeholder="Any additional remarks…"
                  className="w-full rounded-xl border border-border bg-background px-3 py-2 text-sm text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring"
                />
              </div>
            </div>
          </section>

          {/* Submit */}
          <div className="flex items-center justify-end gap-3">
            <Link
              to="/community-reports"
              className="rounded-xl border border-border px-5 py-2 text-sm font-semibold text-foreground hover:bg-muted focus:outline-none focus:ring-2 focus:ring-ring"
            >
              Cancel
            </Link>
            <button
              type="submit"
              disabled={submitting}
              className="flex min-h-[40px] items-center gap-2 rounded-xl bg-primary px-6 py-2 text-sm font-semibold text-primary-foreground hover:bg-primary/90 disabled:opacity-50 focus:outline-none focus:ring-2 focus:ring-ring"
            >
              {submitting ? (
                <>
                  <Loader2 size={14} className="animate-spin" aria-hidden="true" />
                  Submitting…
                </>
              ) : (
                'Submit Report'
              )}
            </button>
          </div>
        </form>
      </main>
    </div>
  );
}
