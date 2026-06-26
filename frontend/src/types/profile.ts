import type { OptionItem } from './customFields';

// ---------- Settings ----------------------------------------------------------

export interface ProfileSettings {
  contact_subtype_default?: string | null;
  submit_label: string;
  success_message: string;
  redirect_after_submit?: string | null;
  notify_google_chat: boolean;
  notify_gmail: boolean;
  prayer_request_field?: string | null;
  invited_by_field?: string | null;
  consolidated_by_field?: string | null;
}

export const DEFAULT_PROFILE_SETTINGS: ProfileSettings = {
  submit_label: 'Submit',
  success_message: 'Thank you! Your information has been recorded.',
  notify_google_chat: true,
  notify_gmail: true,
};

// ---------- Field descriptors (admin builder) ----------------------------------

export interface ProfileFieldDescriptor {
  id: string;
  field_type: 'core' | 'custom';
  core_field?: string | null;
  custom_field_name?: string | null;
  label_override?: string | null;
  placeholder?: string | null;
  default_value?: unknown;
  is_required: boolean;
  weight: number;
  section?: string | null;
}

// ---------- Resolved fields (render output) -----------------------------------

export interface ResolvedField {
  id: string;
  field_type: 'core' | 'custom';
  label: string;
  placeholder?: string | null;
  is_required: boolean;
  weight: number;
  section?: string | null;
  /** data_type drives rendering: text | textarea | select | multiselect | contact_reference | checkbox | number | date */
  data_type?: string;
  options?: OptionItem[] | string[];
  core_field?: string | null;
  custom_field_name?: string | null;
  default_value?: unknown;
}

// ---------- CRUD schemas -------------------------------------------------------

export interface ProfileCreate {
  name: string;
  entity: string;
  fields: ProfileFieldDescriptor[];
  settings: Partial<ProfileSettings>;
  is_public: boolean;
}

export interface ProfileUpdate {
  name?: string;
  fields?: ProfileFieldDescriptor[];
  settings?: Partial<ProfileSettings>;
  is_public?: boolean;
}

export interface ProfileResponse {
  id: number;
  name: string;
  entity: string;
  fields: ProfileFieldDescriptor[];
  settings: ProfileSettings;
  is_public: boolean;
  owner_id?: number | null;
  created_at: string;
  updated_at: string;
}

export interface PaginatedProfileResponse {
  items: ProfileResponse[];
  total: number;
  page: number;
  page_size: number;
}

// ---------- Render / public schemas -------------------------------------------

export interface ProfileRenderResponse {
  profile_id: number;
  name: string;
  fields: ResolvedField[];
  settings: ProfileSettings;
}

export interface PublicProfileSchema {
  name: string;
  settings: ProfileSettings;
  fields: ResolvedField[];
}

// ---------- Public newcomer submission ----------------------------------------

export interface NewcomerSubmission {
  website?: string;
  first_name: string;
  last_name: string;
  phone?: string | null;
  gender?: string | null;
  birth_date?: string | null;
  street_address?: string | null;
  facebook_name?: string | null;
  new_friend_add_date?: string | null;
  service_time?: string | null;
  invited_by?: string | null;
  consolidated_by?: string | null;
  prayer_request?: string | null;
  season?: string | null;
  [key: string]: unknown;
}

export interface NewcomerResult {
  contact_id: number;
  status: 'created' | 'duplicate';
  message: string;
  invited_by_resolved?: boolean | null;
  consolidated_by_resolved?: boolean | null;
}

// ---------- Filters -----------------------------------------------------------

export interface ProfileFilters {
  entity?: string;
  is_public?: boolean | string;
  page?: number;
  page_size?: number;
}
