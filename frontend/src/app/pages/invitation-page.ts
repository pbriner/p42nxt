import { Component, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { MatButtonModule } from '@angular/material/button';
import { MatCardModule } from '@angular/material/card';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatInputModule } from '@angular/material/input';
import { MatProgressBarModule } from '@angular/material/progress-bar';
import { Router } from '@angular/router';

import { describe } from '../core/api';
import { I18n } from '../core/i18n';
import { SignIn } from '../core/sign-in';

const MIN_PASSWORD = 12;

/** An invitation or reset link: choose a password, then set up the second factor. */
@Component({
  selector: 'app-invitation-page',
  imports: [
    FormsModule,
    MatButtonModule,
    MatCardModule,
    MatFormFieldModule,
    MatInputModule,
    MatProgressBarModule,
  ],
  template: `
    <main class="screen">
      <mat-card class="card" appearance="outlined">
        @if (busy()) {
          <mat-progress-bar mode="indeterminate" class="progress" />
        }
        <h1>{{ i18n.t('invite.title') }}</h1>
        @if (!token) {
          <p class="error-text" role="alert">{{ i18n.t('invite.no_link') }}</p>
        } @else {
          <p class="muted">{{ i18n.t('invite.hint') }}</p>
          <form (ngSubmit)="submit()">
            <mat-form-field appearance="outline" class="full">
              <mat-label>{{ i18n.t('invite.password') }}</mat-label>
              <input
                matInput
                name="password"
                type="password"
                autocomplete="new-password"
                [(ngModel)]="password"
                required
              />
            </mat-form-field>
            <mat-form-field appearance="outline" class="full">
              <mat-label>{{ i18n.t('invite.repeat') }}</mat-label>
              <input
                matInput
                name="repeat"
                type="password"
                autocomplete="new-password"
                [(ngModel)]="repeat"
                required
              />
              @if (repeat && repeat !== password) {
                <mat-hint class="error-text">{{ i18n.t('invite.mismatch') }}</mat-hint>
              }
            </mat-form-field>
            @if (error(); as message) {
              <p class="error-text" role="alert">{{ message }}</p>
            }
            <button
              mat-flat-button
              class="full"
              type="submit"
              [disabled]="busy() || password.length < min || password !== repeat"
            >
              {{ i18n.t('invite.button') }}
            </button>
          </form>
        }
      </mat-card>
    </main>
  `,
  styles: `
    .screen {
      min-height: 100dvh;
      display: flex;
      align-items: center;
      justify-content: center;
      padding: 16px;
      box-sizing: border-box;
      background: var(--mat-sys-surface-container-low);
    }
    .card {
      position: relative;
      width: 100%;
      max-width: 400px;
      padding: 24px;
      box-sizing: border-box;
      overflow: hidden;
    }
    .progress {
      position: absolute;
      top: 0;
      left: 0;
      right: 0;
    }
    h1 {
      font: var(--mat-sys-headline-small);
      margin-top: 0;
    }
    .full {
      width: 100%;
      margin-top: 8px;
    }
  `,
})
export class InvitationPage {
  protected readonly i18n = inject(I18n);
  private readonly signIn = inject(SignIn);
  private readonly router = inject(Router);
  protected readonly busy = signal(false);
  protected readonly error = signal<string | null>(null);
  protected readonly min = MIN_PASSWORD;
  // After '#': the link's secret never reaches a server's logs.
  protected readonly token = window.location.hash.slice(1);
  protected password = '';
  protected repeat = '';

  protected async submit(): Promise<void> {
    this.busy.set(true);
    this.error.set(null);
    try {
      const started = await this.signIn.invitation(this.token, this.password);
      history.replaceState(null, '', window.location.pathname);
      await this.router.navigate(['/sign-in'], { state: { stage: started.stage } });
    } catch (error) {
      this.error.set(describe(error));
    } finally {
      this.busy.set(false);
    }
  }
}
