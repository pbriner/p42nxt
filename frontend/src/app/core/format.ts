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
  referenced_by: {
    table: string;
    field: string;
    label: string;
    field_label: string;
    /** A link to any record: what its table field must be as well. */
    also?: Record<string, string>;
  }[];
}

export const NUMBER_TYPES = new Set(['integer', 'decimal', 'percent', 'duration']);

export function shown(
  record: Record<string, unknown>,
  field: FieldInfo | undefined,
  name: string,
  language: string,
  t: (
    key: 'common.yes' | 'common.no' | 'common.no_access' | 'ref.hidden' | 'ref.retired',
  ) => string,
): string {
  const display = (record['_display'] as Record<string, unknown> | undefined)?.[name];
  // A reference never shows an ID: retired ones keep their name, marked; one the person may not
  // read says so.
  const state = (record['_reference_state'] as Record<string, string> | undefined)?.[name];
  if (state === 'hidden') {
    return t('ref.hidden');
  }
  if (display !== undefined && display !== null) {
    return state === 'retired' ? `${String(display)} (${t('ref.retired')})` : String(display);
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
      return Number(value).toLocaleString(language);
    case 'decimal': {
      // As many decimals as the field keeps (the browser's default stops at 3, so 0.0001 BTC
      // read as 0), without trailing zeros.
      const scale = field?.options['scale'];
      const digits = typeof scale === 'number' ? Math.min(Math.max(scale, 0), 20) : 20;
      return Number(value).toLocaleString(language, { maximumFractionDigits: digits });
    }
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
