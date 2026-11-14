import { FieldTypeUnit } from '../unit';
import { TextEditor } from '../string/text-editor';

/** The `email` field type in the app. */
export const unit: FieldTypeUnit = {
  editor: TextEditor,
  link: (v) => (v ? `mailto:${v}` : null),
};
