import { FieldTypeUnit } from '../unit';
import { CurrencyEditor } from './currency-editor';

/** The `currency` field type in the app: a code, picked from the offered currencies. */
export const unit: FieldTypeUnit = {
  editor: CurrencyEditor,
};
