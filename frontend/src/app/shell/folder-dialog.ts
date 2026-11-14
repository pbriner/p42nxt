import { Component, inject } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { MatButtonModule } from '@angular/material/button';
import { MAT_DIALOG_DATA, MatDialogModule, MatDialogRef } from '@angular/material/dialog';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatInputModule } from '@angular/material/input';

import { I18n } from '../core/i18n';

/** Naming a favorites folder: a new one, or renaming. */
@Component({
  selector: 'app-folder-dialog',
  imports: [FormsModule, MatButtonModule, MatDialogModule, MatFormFieldModule, MatInputModule],
  template: `
    <h2 mat-dialog-title>{{ data.label ? i18n.t('fav.rename') : i18n.t('fav.new_folder') }}</h2>
    <form (ngSubmit)="done()">
      <mat-dialog-content>
        <mat-form-field appearance="outline" class="wide">
          <mat-label>{{ i18n.t('fav.folder_name') }}</mat-label>
          <input
            matInput
            name="label"
            [(ngModel)]="label"
            maxlength="60"
            required
            cdkFocusInitial
          />
        </mat-form-field>
      </mat-dialog-content>
      <mat-dialog-actions align="end">
        <button mat-button type="button" mat-dialog-close>{{ i18n.t('common.cancel') }}</button>
        <button mat-flat-button type="submit" [disabled]="!label.trim()">
          {{ data.label ? i18n.t('fav.rename') : i18n.t('common.create') }}
        </button>
      </mat-dialog-actions>
    </form>
  `,
  styles: `
    .wide {
      width: min(360px, 70vw);
    }
  `,
})
export class FolderDialog {
  protected readonly i18n = inject(I18n);
  protected readonly data = inject<{ label: string | null }>(MAT_DIALOG_DATA);
  private readonly ref = inject(MatDialogRef<FolderDialog, string>);
  protected label = this.data.label ?? '';

  done(): void {
    if (this.label.trim()) {
      this.ref.close(this.label.trim());
    }
  }
}
