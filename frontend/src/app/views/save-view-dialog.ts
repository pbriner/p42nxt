import { Component, inject } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { MatButtonModule } from '@angular/material/button';
import { MatCheckboxModule } from '@angular/material/checkbox';
import { MAT_DIALOG_DATA, MatDialogModule, MatDialogRef } from '@angular/material/dialog';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatInputModule } from '@angular/material/input';

import { I18n } from '../core/i18n';

export interface SaveView {
  name: string;
  isDefault: boolean;
}

/** Naming the list as it is now, to open it like this again. */
@Component({
  selector: 'app-save-view-dialog',
  imports: [
    FormsModule,
    MatButtonModule,
    MatCheckboxModule,
    MatDialogModule,
    MatFormFieldModule,
    MatInputModule,
  ],
  template: `
    <h2 mat-dialog-title>{{ i18n.t('view.save_title') }}</h2>
    <form (ngSubmit)="save()">
      <mat-dialog-content>
        <p class="muted">{{ i18n.t('view.hint') }}</p>
        <mat-form-field appearance="outline" class="field">
          <mat-label>{{ i18n.t('view.name') }}</mat-label>
          <input matInput name="name" maxlength="100" required [(ngModel)]="name" cdkFocusInitial />
        </mat-form-field>
        <mat-checkbox name="isDefault" [(ngModel)]="isDefault">{{
          i18n.t('view.default')
        }}</mat-checkbox>
      </mat-dialog-content>
      <mat-dialog-actions align="end">
        <button mat-button type="button" mat-dialog-close>{{ i18n.t('common.cancel') }}</button>
        <button mat-flat-button type="submit" [disabled]="!name.trim()">
          {{ i18n.t('common.save') }}
        </button>
      </mat-dialog-actions>
    </form>
  `,
  styles: `
    .field {
      display: block;
      width: min(380px, 80vw);
    }
  `,
})
export class SaveViewDialog {
  protected readonly i18n = inject(I18n);
  private readonly data = inject<{ name: string }>(MAT_DIALOG_DATA);
  private readonly ref = inject(MatDialogRef<SaveViewDialog, SaveView>);
  protected name = this.data.name;
  protected isDefault = false;

  protected save(): void {
    if (this.name.trim()) {
      this.ref.close({ name: this.name.trim(), isDefault: this.isDefault });
    }
  }
}
