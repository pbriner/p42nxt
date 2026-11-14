import { FieldTypeUnit } from '../unit';
import { SecretEditor } from './secret-editor';

/** The `secret` field type in the app. */
export const unit: FieldTypeUnit = {
  editor: SecretEditor,
};
