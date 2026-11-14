import { FieldTypeUnit } from './unit';
import { unit as anyrecord } from './any_record';
import { unit as boolean } from './boolean';
import { unit as choice } from './choice';
import { unit as color } from './color';
import { unit as date } from './date';
import { unit as datetime } from './datetime';
import { unit as decimal } from './decimal';
import { unit as duration } from './duration';
import { unit as email } from './email';
import { unit as hostname } from './hostname';
import { unit as icon } from './icon';
import { unit as integer } from './integer';
import { unit as json } from './json';
import { unit as markdown } from './markdown';
import { unit as percent } from './percent';
import { unit as phone } from './phone';
import { unit as reference } from './reference';
import { unit as secret } from './secret';
import { unit as string } from './string';
import { unit as text } from './text';
import { unit as translatedtext } from './translated_text';
import { unit as url } from './url';

/** Every field type the app knows, by name; a field's record names its type. */
export const FIELD_TYPES: Record<string, FieldTypeUnit> = {
  any_record: anyrecord,
  boolean: boolean,
  choice: choice,
  color: color,
  date: date,
  datetime: datetime,
  decimal: decimal,
  duration: duration,
  email: email,
  hostname: hostname,
  icon: icon,
  integer: integer,
  json: json,
  markdown: markdown,
  percent: percent,
  phone: phone,
  reference: reference,
  secret: secret,
  string: string,
  text: text,
  translated_text: translatedtext,
  url: url,
};

/** A type the app doesn't know yet is edited as text. */
export function unitOf(type: string): FieldTypeUnit {
  return FIELD_TYPES[type] ?? FIELD_TYPES['string']!;
}
