import { FieldTypeUnit } from '../unit';
import { NumberEditor } from '../integer/number-editor';

/** The `formula` field type in the app: a number the server works out from the record's own
 *  fields on every save; shown like an amount when it has a currency, never typed. */
export const unit: FieldTypeUnit = {
  editor: NumberEditor,
};
