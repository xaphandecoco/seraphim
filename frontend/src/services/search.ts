import { api } from './api';
import type {
  FieldRegistryResponse,
  SearchRequest,
  SearchResponse,
  SavedSearch,
  SavedSearchCreate,
  SavedSearchUpdate,
  GroupResponse,
  GroupCreate,
  GroupUpdate,
  CriteriaNode,
} from '@/types/search';
import type { Paginated, ContactListItem } from '@/types';

export const searchApi = {
  // ---- Field registry -------------------------------------------------------
  getFields: (): Promise<FieldRegistryResponse> =>
    api.get('/search/fields').then((r) => r.data),

  // ---- Search ---------------------------------------------------------------
  search: (body: SearchRequest): Promise<SearchResponse> =>
    api.post('/search', body).then((r) => r.data),

  validate: (criteria: CriteriaNode): Promise<void> =>
    api.post('/search/validate', { criteria }).then(() => undefined),

  // ---- Saved searches -------------------------------------------------------
  listSavedSearches: (): Promise<SavedSearch[]> =>
    api.get('/search/saved').then((r) => r.data),

  createSavedSearch: (body: SavedSearchCreate): Promise<SavedSearch> =>
    api.post('/search/saved', body).then((r) => r.data),

  getSavedSearch: (id: number): Promise<SavedSearch> =>
    api.get(`/search/saved/${id}`).then((r) => r.data),

  updateSavedSearch: (id: number, body: SavedSearchUpdate): Promise<SavedSearch> =>
    api.patch(`/search/saved/${id}`, body).then((r) => r.data),

  deleteSavedSearch: (id: number): Promise<void> =>
    api.delete(`/search/saved/${id}`).then(() => undefined),

  runSavedSearch: (
    id: number,
    page?: number,
    page_size?: number,
  ): Promise<SearchResponse> =>
    api.post(`/search/saved/${id}/run`, { page, page_size }).then((r) => r.data),

  // ---- Groups ---------------------------------------------------------------
  listGroups: (params?: { type?: string; entity?: string }): Promise<GroupResponse[]> =>
    api.get('/groups', { params }).then((r) => r.data),

  createGroup: (body: GroupCreate): Promise<GroupResponse> =>
    api.post('/groups', body).then((r) => r.data),

  getGroup: (id: number): Promise<GroupResponse> =>
    api.get(`/groups/${id}`).then((r) => r.data),

  updateGroup: (id: number, body: GroupUpdate): Promise<GroupResponse> =>
    api.patch(`/groups/${id}`, body).then((r) => r.data),

  deleteGroup: (id: number): Promise<void> =>
    api.delete(`/groups/${id}`).then(() => undefined),

  getGroupMembers: (
    id: number,
    page?: number,
    page_size?: number,
  ): Promise<Paginated<ContactListItem>> =>
    api
      .get(`/groups/${id}/members`, { params: { page, page_size } })
      .then((r) => r.data),
};
