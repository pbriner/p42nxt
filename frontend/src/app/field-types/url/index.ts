import { FieldTypeUnit } from '../unit';
import { TextEditor } from '../string/text-editor';

/** The `url` field type in the app. */
export const unit: FieldTypeUnit = {
  editor: TextEditor,
  link: (v) => (typeof v === 'string' && /^https?:\/\//.test(v) ? v : null),
};
