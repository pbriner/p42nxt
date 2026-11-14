import { FieldTypeUnit } from '../unit';
import { TextareaEditor } from './textarea-editor';

/** The `text` field type in the app. */
export const unit: FieldTypeUnit = {
  editor: TextareaEditor,
  wide: true,
};
