import { api } from '@/services/api';
import type {
  Event,
  EventDetail,
  EventSeries,
  EventParticipant,
  Paginated,
} from '@/types';

// ---------- Filter / payload interfaces --------------------------------------

export interface EventFilters {
  type?: string;
  date_from?: string;
  date_to?: string;
  series_id?: number;
  is_active?: boolean;
  page?: number;
  page_size?: number;
}

export interface EventCreate {
  title: string;
  start_at?: string | null;
  end_at?: string | null;
  external_id?: number | null;
  event_type?: string | null;
  session_time?: string | null;
  occurrence_date?: string | null;
  location?: string | null;
  recurring_series_id?: number | null;
  is_active?: boolean;
}

export interface EventUpdate {
  title?: string;
  start_at?: string | null;
  end_at?: string | null;
  external_id?: number | null;
  event_type?: string | null;
  session_time?: string | null;
  occurrence_date?: string | null;
  location?: string | null;
  recurring_series_id?: number | null;
  is_active?: boolean | null;
}

export interface ParticipantFilters {
  page?: number;
  page_size?: number;
}

export interface ParticipantAdd {
  contact_id: number;
  status?: string;
  role?: string | null;
  source?: string;
}

export interface ParticipantStatusUpdate {
  status: string;
  role?: string | null;
}

export interface EventSeriesFilters {
  is_active?: boolean;
}

export interface EventSeriesCreate {
  name: string;
  event_type: string;
  default_session_time?: string | null;
  default_location?: string | null;
  is_active?: boolean;
}

export interface EventSeriesUpdate {
  name?: string;
  event_type?: string;
  default_session_time?: string | null;
  default_location?: string | null;
  is_active?: boolean | null;
}

export interface GenerateOccurrencesPayload {
  target_date: string;
}

export interface GenerateOccurrencesResult {
  created: number;
  skipped: number;
  events: EventDetail[];
}

// ---------- Named function exports -------------------------------------------

export const listEvents = (
  params: EventFilters = {},
): Promise<Paginated<Event>> =>
  api.get<Paginated<Event>>('/events', { params }).then((r) => r.data);

export const createEvent = (payload: EventCreate): Promise<EventDetail> =>
  api.post<EventDetail>('/events', payload).then((r) => r.data);

export const getEvent = (id: number): Promise<EventDetail> =>
  api.get<EventDetail>(`/events/${id}`).then((r) => r.data);

export const updateEvent = (
  id: number,
  payload: EventUpdate,
): Promise<Event> =>
  api.patch<Event>(`/events/${id}`, payload).then((r) => r.data);

export const deleteEvent = (id: number): Promise<{ id: number; is_active: boolean }> =>
  api
    .delete<{ id: number; is_active: boolean }>(`/events/${id}`)
    .then((r) => r.data);

export const listParticipants = (
  eventId: number,
  params: ParticipantFilters = {},
): Promise<Paginated<EventParticipant>> =>
  api
    .get<Paginated<EventParticipant>>(`/events/${eventId}/participants`, {
      params,
    })
    .then((r) => r.data);

export const addParticipant = (
  eventId: number,
  payload: ParticipantAdd,
): Promise<EventParticipant> =>
  api
    .post<EventParticipant>(`/events/${eventId}/participants`, payload)
    .then((r) => r.data);

export const updateParticipantStatus = (
  eventId: number,
  participantId: number,
  payload: ParticipantStatusUpdate,
): Promise<EventParticipant> =>
  api
    .patch<EventParticipant>(
      `/events/${eventId}/participants/${participantId}`,
      payload,
    )
    .then((r) => r.data);

export const listEventSeries = (
  params: EventSeriesFilters = {},
): Promise<EventSeries[]> =>
  api.get<EventSeries[]>('/event-series', { params }).then((r) => r.data);

export const createEventSeries = (
  payload: EventSeriesCreate,
): Promise<EventSeries> =>
  api.post<EventSeries>('/event-series', payload).then((r) => r.data);

export const updateEventSeries = (
  id: number,
  payload: EventSeriesUpdate,
): Promise<EventSeries> =>
  api.patch<EventSeries>(`/event-series/${id}`, payload).then((r) => r.data);

export const deleteEventSeries = (
  id: number,
): Promise<{ id: number; is_active: boolean }> =>
  api
    .delete<{ id: number; is_active: boolean }>(`/event-series/${id}`)
    .then((r) => r.data);

export const generateSeriesOccurrences = (
  seriesId: number,
  payload: GenerateOccurrencesPayload,
): Promise<GenerateOccurrencesResult> =>
  api
    .post<GenerateOccurrencesResult>(
      `/event-series/${seriesId}/generate`,
      payload,
    )
    .then((r) => r.data);
