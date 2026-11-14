import { FieldTypeUnit } from '../unit';
import { ReferenceEditor } from './reference-editor';

/** The `reference` field type in the app. */
export const unit: FieldTypeUnit = {
  editor: ReferenceEditor,
  link: (v, f) =>
    v && f.options.table && f.options.table !== '-' ? `/form/${f.options.table}/${v}` : null,
};
