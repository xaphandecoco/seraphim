import type { ContactListItem } from './index';

// ---------- Field registry ---------------------------------------------------

export type FieldKind = 'core' | 'derived' | 'custom';
export type FieldType =
  | 'string'
  | 'int'
  | 'number'
  | 'bool'
  | 'date'
  | 'datetime'
  | 'enum'
  | 'multiselect';

export interface FieldOption {
  value: string;
  label: string;
}

export interface FieldSpec {
  key: string;
  label: string;
  kind: FieldKind;
  type: FieldType;
  ops: string[];
  options?: FieldOption[];
  nullable: boolean;
}

export interface FieldRegistryResponse {
  fields: FieldSpec[];
}

// ---------- Criteria tree ----------------------------------------------------

export interface CriteriaLeaf {
  field: string;
  op: string;
  value?: unknown;
}

export interface CriteriaGroup {
  logic: 'and' | 'or';
  conditions: CriteriaNode[];
}

export type CriteriaNode = CriteriaGroup | CriteriaLeaf;

export function isCriteriaGroup(node: CriteriaNode): node is CriteriaGroup {
  return 'logic' in node;
}

export function isCriteriaLeaf(node: CriteriaNode): node is CriteriaLeaf {
  return 'field' in node;
}

// ---------- Search request / response ----------------------------------------

export interface SearchRequest {
  criteria: CriteriaNode;
  page?: number;
  page_size?: number;
  sort?: string;
  include_deleted?: boolean;
}

export interface SearchResponse {
  total: number;
  page: number;
  page_size: number;
  items: ContactListItem[];
}

// ---------- Saved searches ---------------------------------------------------

export interface SavedSearch {
  id: number;
  name: string;
  criteria: CriteriaNode;
  entity?: string | null;
  owner_id: number;
  created_at: string;
  updated_at: string;
}

export interface SavedSearchCreate {
  name: string;
  criteria: CriteriaNode;
  entity?: string;
}

export interface SavedSearchUpdate {
  name?: string;
  criteria?: CriteriaNode;
  entity?: string;
}

// ---------- Groups -----------------------------------------------------------

export type GroupType = 'smart' | 'static';

export interface GroupResponse {
  id: number;
  name: string;
  group_type: GroupType;
  criteria?: CriteriaNode | null;
  entity?: string | null;
  owner_id: number;
  member_count?: number | null;
  created_at: string;
  updated_at: string;
}

export interface GroupCreate {
  name: string;
  group_type: GroupType;
  criteria?: CriteriaNode;
  entity?: string;
}

export interface GroupUpdate {
  name?: string;
  criteria?: CriteriaNode;
}
