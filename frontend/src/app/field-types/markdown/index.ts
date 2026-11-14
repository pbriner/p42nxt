import { FieldTypeUnit } from '../unit';
import { TextareaEditor } from '../text/textarea-editor';

/** The `markdown` field type in the app. */
export const unit: FieldTypeUnit = {
  editor: TextareaEditor,
  wide: true,
};
