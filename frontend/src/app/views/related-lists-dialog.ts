import { Component, inject, signal } from '@angular/core';
import { MatButtonModule } from '@angular/material/button';
import { MAT_DIALOG_DATA, MatDialogModule, MatDialogRef } from '@angular/material/dialog';

import { I18n } from '../core/i18n';
import { BucketItem, Slushbucket } from './slushbucket';

export interface RelatedChoice {
  items: BucketItem[];
  shown: string[];
}

/** Choosing and ordering a form's related lists, for oneself: the shown keys, or 'reset'. */
@Component({
  selector: 'app-related-lists-dialog',
  imports: [MatButtonModule, MatDialogModule, Slushbucket],
  template: `
    <h2 mat-dialog-title>{{ i18n.t('related.choose_title') }}</h2>
    <mat-dialog-content class="content">
      <p class="muted">{{ i18n.t('related.hint') }}</p>
      <app-slushbucket [items]="data.items" [(shown)]="shown" />
    </mat-dialog-content>
    <mat-dialog-actions>
      <button mat-button type="button" (click)="ref.close('reset')">
        {{ i18n.t('slush.reset') }}
      </button>
      <span class="spacer"></span>
      <button mat-button type="button" mat-dialog-close>{{ i18n.t('common.cancel') }}</button>
      <button mat-flat-button type="button" (click)="ref.close(shown())">
        {{ i18n.t('slush.apply') }}
      </button>
    </mat-dialog-actions>
  `,
  styles: `
    .content {
      width: min(760px, 90vw);
    }
    .spacer {
      flex: 1;
    }
  `,
})
export class RelatedListsDialog {
  protected readonly i18n = inject(I18n);
  protected readonly data = inject<RelatedChoice>(MAT_DIALOG_DATA);
  protected readonly ref = inject(MatDialogRef<RelatedListsDialog, string[] | 'reset'>);
  protected readonly shown = signal([...this.data.shown]);
}
