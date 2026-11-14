import { FieldTypeUnit } from '../unit';
import { TextEditor } from '../string/text-editor';

/** The `phone` field type in the app. */
export const unit: FieldTypeUnit = {
  editor: TextEditor,
  link: (v) => (v ? `tel:${String(v).replace(/\s/g, '')}` : null),
};
