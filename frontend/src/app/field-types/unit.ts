import { InputSignal, ModelSignal, Type } from '@angular/core';

import { FieldInfo } from '../core/format';

/** What every field editor takes: the field, its value (two-way), and what's wrong with it. */
export interface FieldEditor {
  field: InputSignal<FieldInfo>;
  value: ModelSignal<unknown>;
  error: InputSignal<string | null>;
  /** The value's display text where the record gives one (a reference's name). */
  shown: InputSignal<string | null>;
}

/** One field type in the app: its editor, and how its value is shown when it can't be edited. */
export interface FieldTypeUnit {
  editor: Type<FieldEditor>;
  /** Takes the form's whole width (long text). */
  wide?: boolean;
  /** Where a value leads when shown (mailto:, tel:, a page of the app). */
  link?: (value: unknown, field: FieldInfo) => string | null;
}
