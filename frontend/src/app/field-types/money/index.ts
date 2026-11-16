import { FieldTypeUnit } from '../unit';
import { NumberEditor } from '../integer/number-editor';

/** The `money` field type in the app: an exact amount; its currency is another field of the
 *  record, or fixed (then shown beside the amount). */
export const unit: FieldTypeUnit = {
  editor: NumberEditor,
};
