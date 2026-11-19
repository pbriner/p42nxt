import { FieldTypeUnit } from '../unit';
import { TextEditor } from '../string/text-editor';

/** The `number` field type in the app: a record number the database gives; never edited. */
export const unit: FieldTypeUnit = {
  editor: TextEditor,
};
