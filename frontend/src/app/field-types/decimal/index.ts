import { FieldTypeUnit } from '../unit';
import { NumberEditor } from '../integer/number-editor';

/** The `decimal` field type in the app. */
export const unit: FieldTypeUnit = {
  editor: NumberEditor,
};
