import { Component, inject } from '@angular/core';
import { MatButtonModule } from '@angular/material/button';
import { MAT_DIALOG_DATA, MatDialogModule } from '@angular/material/dialog';

import { I18n } from '../core/i18n';

export interface Confirm {
  title: string;
  text: string;
  /** The specific verb: Delete, Remove, Reset… */
  action: string;
}

/** Asking before something that can't simply be taken back. */
@Component({
  selector: 'app-confirm-dialog',
  imports: [MatButtonModule, MatDialogModule],
  template: `
    <h2 mat-dialog-title>{{ data.title }}</h2>
    <mat-dialog-content>{{ data.text }}</mat-dialog-content>
    <mat-dialog-actions align="end">
      <button mat-button type="button" [mat-dialog-close]="false">
        {{ i18n.t('common.cancel') }}
      </button>
      <button mat-flat-button type="button" [mat-dialog-close]="true" cdkFocusInitial>
        {{ data.action }}
      </button>
    </mat-dialog-actions>
  `,
})
export class ConfirmDialog {
  protected readonly i18n = inject(I18n);
  protected readonly data = inject<Confirm>(MAT_DIALOG_DATA);
}
