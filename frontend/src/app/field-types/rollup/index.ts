import { FieldTypeUnit } from '../unit';
import { NumberEditor } from '../integer/number-editor';

/** The `rollup` field type in the app: the sum or count of the records pointing to this one,
 *  kept right by the server; shown like an amount when it has a currency, never typed. */
export const unit: FieldTypeUnit = {
  editor: NumberEditor,
};
