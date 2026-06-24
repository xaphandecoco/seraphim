import { api } from '@/services/api';
import type {
  CustomFieldSchema,
  CustomFieldGroup,
  CustomFieldDef,
  CustomFieldGroupCreate,
  CustomFieldGroupUpdate,
  CustomFieldDefCreate,
  CustomFieldDefUpdate,
} from '@/types/customFields';

export const customFieldsApi = {
  getSchema: (entity = 'contact') =>
    api
      .get<CustomFieldSchema>(`/custom-fields/schema?entity=${entity}`)
      .then((r) => r.data),

  listGroups: (entity?: string, includeInactive = false) =>
    api
      .get<CustomFieldGroup[]>(
        `/custom-fields/groups?${entity ? `entity=${entity}&` : ''}include_inactive=${includeInactive}`,
      )
      .then((r) => r.data),

  createGroup: (body: CustomFieldGroupCreate) =>
    api.post<CustomFieldGroup>('/custom-fields/groups', body).then((r) => r.data),

  updateGroup: (id: number, body: CustomFieldGroupUpdate) =>
    api.patch<CustomFieldGroup>(`/custom-fields/groups/${id}`, body).then((r) => r.data),

  deleteGroup: (id: number, hard = false) =>
    api.delete(`/custom-fields/groups/${id}?hard=${hard}`),

  listDefs: (groupId?: number, entity?: string, includeInactive = false) =>
    api
      .get<CustomFieldDef[]>(
        `/custom-fields/defs?${groupId ? `group_id=${groupId}&` : ''}${entity ? `entity=${entity}&` : ''}include_inactive=${includeInactive}`,
      )
      .then((r) => r.data),

  createDef: (body: CustomFieldDefCreate) =>
    api.post<CustomFieldDef>('/custom-fields/defs', body).then((r) => r.data),

  updateDef: (id: number, body: CustomFieldDefUpdate) =>
    api.patch<CustomFieldDef>(`/custom-fields/defs/${id}`, body).then((r) => r.data),

  deleteDef: (id: number, hard = false) =>
    api.delete(`/custom-fields/defs/${id}?hard=${hard}`),

  validate: (entity: string, customData: Record<string, unknown>) =>
    api
      .post('/custom-fields/validate', { entity, custom_data: customData })
      .then((r) => r.data),
};
