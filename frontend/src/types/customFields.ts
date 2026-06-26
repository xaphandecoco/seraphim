export type DataType =
  | 'text'
  | 'textarea'
  | 'select'
  | 'multiselect'
  | 'date'
  | 'number'
  | 'checkbox'
  | 'contact_reference';

export interface OptionItem {
  value: string;
  label: string;
}

export interface CustomFieldDef {
  id: number;
  group_id: number;
  name: string;
  label: string;
  data_type: DataType;
  options: OptionItem[];
  is_required: boolean;
  is_multi: boolean;
  weight: number;
  is_active: boolean;
  help_text: string | null;
}

export interface CustomFieldGroup {
  id: number;
  name: string;
  label: string;
  entity: string; // lowercase: "contact" | "event" | "activity"
  weight: number;
  is_active: boolean;
  fields: CustomFieldDef[];
}

export interface CustomFieldSchema {
  entity: string;
  groups: CustomFieldGroup[];
}

// contact_reference: single=number, multi=number[] (MASTER C13)
export type CustomDataValue = string | number | boolean | string[] | number[] | null;

export interface CustomFieldGroupCreate {
  name: string;
  label: string;
  entity?: 'contact' | 'event' | 'activity';
  weight?: number;
}

export interface CustomFieldGroupUpdate {
  label?: string;
  weight?: number;
  is_active?: boolean;
}

export interface CustomFieldDefCreate {
  group_id: number;
  name: string;
  label: string;
  data_type: DataType;
  options?: OptionItem[];
  is_required?: boolean;
  is_multi?: boolean;
  weight?: number;
  help_text?: string | null;
}

export interface CustomFieldDefUpdate {
  label?: string;
  options?: OptionItem[];
  is_required?: boolean;
  is_multi?: boolean;
  weight?: number;
  is_active?: boolean;
  help_text?: string | null;
}
