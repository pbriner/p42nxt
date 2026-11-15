import { Component, inject, input, signal } from '@angular/core';
import { MatButtonModule } from '@angular/material/button';
import { MatCardModule } from '@angular/material/card';

import { Api, describe } from '../core/api';
import { I18n } from '../core/i18n';

interface Made {
  link: string;
  expires: string;
}

/** How a person gets in: administrators make a one-time link the person uses to set their own
 * password and second factor (an administrator never chooses anyone's password), or reset their
 * sign-in, which first ends their sessions and takes away everything they signed in with. */
@Component({
  selector: 'app-person-sign-in',
  imports: [MatButtonModule, MatCardModule],
  template: `
    <mat-card appearance="outlined" class="section">
      <mat-card-header>
        <mat-card-title>{{ i18n.t('access.title') }}</mat-card-title>
      </mat-card-header>
      <mat-card-content>
        <p class="muted">{{ i18n.t('access.explain') }}</p>
        <div class="row">
          <button mat-stroked-button type="button" [disabled]="busy()" (click)="make(false)">
            {{ i18n.t('access.make') }}
          </button>
          <button mat-stroked-button type="button" [disabled]="busy()" (click)="make(true)">
            {{ i18n.t('access.reset') }}
          </button>
        </div>
        @if (made(); as m) {
          <div class="link">
            <code>{{ m.link }}</code>
            <button mat-button type="button" (click)="copy(m.link)">
              {{ copied() ? i18n.t('common.copied') : i18n.t('common.copy') }}
            </button>
          </div>
          <p class="muted">{{ i18n.t('access.valid', { until: until(m.expires) }) }}</p>
        }
        @if (said(); as text) {
          <p role="alert">{{ text }}</p>
        }
      </mat-card-content>
    </mat-card>
  `,
  styles: `
    .row {
      display: flex;
      gap: 12px;
      flex-wrap: wrap;
    }
    .link {
      display: flex;
      align-items: center;
      gap: 8px;
      flex-wrap: wrap;
      margin-top: 12px;
    }
    code {
      overflow-wrap: anywhere;
      font-family: var(--app-mono);
    }
  `,
})
export class PersonSignIn {
  protected readonly i18n = inject(I18n);
  private readonly api = inject(Api);
  readonly person = input.required<string>();
  protected readonly busy = signal(false);
  protected readonly made = signal<Made | null>(null);
  protected readonly copied = signal(false);
  protected readonly said = signal<string | null>(null);

  protected async make(reset: boolean): Promise<void> {
    if (reset && !confirm(this.i18n.t('access.reset_confirm'))) {
      return;
    }
    this.busy.set(true);
    this.said.set(null);
    this.made.set(null);
    this.copied.set(false);
    try {
      const { data } = await this.api.post<Made>(
        `/api/v1/people/${encodeURIComponent(this.person())}/invitation`,
        { reset },
      );
      this.made.set(data);
    } catch (error) {
      this.said.set(describe(error));
    } finally {
      this.busy.set(false);
    }
  }

  protected async copy(link: string): Promise<void> {
    try {
      await navigator.clipboard.writeText(link);
      this.copied.set(true);
    } catch {
      this.copied.set(false);
    }
  }

  protected until(expires: string): string {
    return new Date(expires).toLocaleString(this.i18n.language(), {
      dateStyle: 'medium',
      timeStyle: 'short',
    });
  }
}
