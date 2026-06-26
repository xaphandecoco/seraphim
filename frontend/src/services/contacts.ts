import { api } from '@/services/api';
import type {
  ContactListItem,
  ContactDetail,
  ContactAttendanceItem,
  Paginated,
} from '@/types';

export interface ContactFilters {
  search?: string;
  contact_type?: string;
  tier?: string;
  is_regular?: boolean;
  include_deleted?: boolean;
  page?: number;
  page_size?: number;
}

export interface ContactAttendanceFilters {
  page?: number;
  page_size?: number;
  source?: string;
  event_type?: string;
}

export interface ContactCreate {
  first_name: string;
  last_name: string;
  nickname?: string | null;
  suffix?: string | null;
  contact_type?: string;
  contact_subtype?: string | null;
  gender?: string | null;
  birth_date?: string | null;
  phone?: string | null;
  email?: string | null;
  street_address?: string | null;
  custom_data?: Record<string, unknown>;
}

export interface ContactUpdate extends Partial<ContactCreate> {}

export const contactsApi = {
  list: (filters: ContactFilters = {}): Promise<Paginated<ContactListItem>> =>
    api
      .get<Paginated<ContactListItem>>('/members', { params: filters })
      .then((r) => r.data),

  get: (id: number): Promise<ContactDetail> =>
    api.get<ContactDetail>(`/members/${id}`).then((r) => r.data),

  create: (body: ContactCreate): Promise<ContactDetail> =>
    api.post<ContactDetail>('/members', body).then((r) => r.data),

  update: (id: number, body: ContactUpdate): Promise<ContactDetail> =>
    api.patch<ContactDetail>(`/members/${id}`, body).then((r) => r.data),

  delete: (id: number): Promise<void> =>
    api.delete(`/members/${id}`).then(() => undefined),

  restore: (id: number): Promise<ContactDetail> =>
    api.post<ContactDetail>(`/members/${id}/restore`).then((r) => r.data),

  getAttendance: (
    id: number,
    filters: ContactAttendanceFilters = {},
  ): Promise<Paginated<ContactAttendanceItem>> =>
    api
      .get<Paginated<ContactAttendanceItem>>(`/members/${id}/attendance`, {
        params: filters,
      })
      .then((r) => r.data),
};

// ---------- Named function exports (F06 spec) ---------------------------------

export const listContacts = (params: ContactFilters = {}): Promise<Paginated<ContactListItem>> =>
  contactsApi.list(params);

export const getContact = (id: number): Promise<ContactDetail> =>
  contactsApi.get(id);

export const createContact = (payload: ContactCreate): Promise<ContactDetail> =>
  contactsApi.create(payload);

export const updateContact = (id: number, payload: ContactUpdate): Promise<ContactDetail> =>
  contactsApi.update(id, payload);

export const deleteContact = (id: number): Promise<void> =>
  contactsApi.delete(id);

export const restoreContact = (id: number): Promise<ContactDetail> =>
  contactsApi.restore(id);

export const getContactAttendance = (
  id: number,
  params: ContactAttendanceFilters = {},
): Promise<Paginated<ContactAttendanceItem>> =>
  contactsApi.getAttendance(id, params);
