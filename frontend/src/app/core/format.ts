/** A translated text (`{"en": …, "de": …}`) in the language, else English, else any. */
export function textIn(value: unknown, language: string): string {
  if (value && typeof value === 'object' && !Array.isArray(value)) {
    const texts = value as Record<string, string>;
    return texts[language] ?? texts['en'] ?? Object.values(texts)[0] ?? '';
  }
  return value === null || value === undefined ? '' : String(value);
}

/** Values as people read them in lists. */
export interface ChoiceValue {
  value: string;
  label: string;
  tone: 'primary' | 'tertiary' | 'error' | 'muted' | null;
}

export interface FieldInfo {
  name: string;
  type: string;
  /** Which filter operators apply; null: not filtered or sorted. */
  kind: string | null;
  label: string;
  help: string | null;
  required: boolean;
  default: unknown;
  system: boolean;
  read: boolean;
  create: boolean;
  write: boolean;
  options: { values?: ChoiceValue[]; table?: string; [name: string]: unknown };
}

export interface TableInfo {
  table: string;
  label: string;
  label_plural: string | null;
  display: string | null;
  personal: boolean;
  operations: Record<string, boolean>;
  fields: FieldInfo[];
  /** Tables pointing to this one, through fields the person may read. */
  referenced_by: { table: string; field: string; label: string; field_label: string }[];
}

export const NUMBER_TYPES = new Set(['integer', 'decimal', 'percent', 'duration']);

export function shown(
  record: Record<string, unknown>,
  field: FieldInfo | undefined,
  name: string,
  language: string,
  t: (key: 'common.yes' | 'common.no' | 'common.no_access') => string,
): string {
  const display = (record['_display'] as Record<string, unknown> | undefined)?.[name];
  if (display !== undefined && display !== null) {
    return String(display);
  }
  if (!(name in record)) {
    return field && !field.read ? t('common.no_access') : '—';
  }
  const value = record[name];
  if (value === null || value === undefined || value === '') {
    return '—';
  }
  switch (field?.type) {
    case 'boolean':
      return value ? t('common.yes') : t('common.no');
    case 'date':
      return new Date(`${value}T00:00:00`).toLocaleDateString(language, { dateStyle: 'medium' });
    case 'datetime':
      return new Date(String(value)).toLocaleString(language, {
        dateStyle: 'medium',
        timeStyle: 'short',
      });
    case 'percent':
      return `${(Number(value) * 100).toLocaleString(language)} %`;
    case 'integer':
    case 'decimal':
      return Number(value).toLocaleString(language);
    case 'secret':
      return '••••••••';
    case 'translated_text':
      return textIn(value, language);
    default:
      if (typeof value === 'object') {
        return Array.isArray(value) ? value.join(', ') : JSON.stringify(value);
      }
      return String(value);
  }
}
