import { Component, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { MatButtonModule } from '@angular/material/button';
import { MatDialogModule, MatDialogRef } from '@angular/material/dialog';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatInputModule } from '@angular/material/input';

import { Api, describe } from '../core/api';
import { I18n } from '../core/i18n';

/** The second factor again, for a change that needs it to be recent (access, tokens). */
@Component({
  selector: 'app-verify-dialog',
  imports: [FormsModule, MatButtonModule, MatDialogModule, MatFormFieldModule, MatInputModule],
  template: `
    <h2 mat-dialog-title>{{ i18n.t('verify.title') }}</h2>
    <form (ngSubmit)="verify()">
      <mat-dialog-content>
        <p>{{ i18n.t('verify.text') }}</p>
        <mat-form-field appearance="outline" class="wide">
          <mat-label>{{ i18n.t('sign_in.code') }}</mat-label>
          <input
            matInput
            name="code"
            [(ngModel)]="code"
            autocomplete="one-time-code"
            inputmode="numeric"
            required
            cdkFocusInitial
          />
          <mat-hint>{{ i18n.t('sign_in.code_hint') }}</mat-hint>
        </mat-form-field>
        @if (problem(); as said) {
          <p class="problem" role="alert">{{ said }}</p>
        }
      </mat-dialog-content>
      <mat-dialog-actions align="end">
        <button mat-button type="button" [mat-dialog-close]="false">
          {{ i18n.t('common.cancel') }}
        </button>
        <button mat-flat-button type="submit" [disabled]="!code.trim() || busy()">
          {{ i18n.t('sign_in.verify') }}
        </button>
      </mat-dialog-actions>
    </form>
  `,
  styles: `
    .wide {
      width: min(360px, 70vw);
    }
    .problem {
      color: var(--mat-sys-error);
      margin: 8px 0 0;
    }
  `,
})
export class VerifyDialog {
  protected readonly i18n = inject(I18n);
  private readonly api = inject(Api);
  private readonly ref = inject(MatDialogRef<VerifyDialog, boolean>);
  protected code = '';
  protected readonly busy = signal(false);
  protected readonly problem = signal<string | null>(null);

  protected async verify(): Promise<void> {
    this.busy.set(true);
    this.problem.set(null);
    try {
      await this.api.post('/api/v1/sign-in/verify', { code: this.code.trim() }, { quiet: true });
      this.ref.close(true);
    } catch (error) {
      this.problem.set(describe(error));
      this.code = '';
    } finally {
      this.busy.set(false);
    }
  }
}
