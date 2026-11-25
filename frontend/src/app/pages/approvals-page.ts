import { Component, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { MatButtonModule } from '@angular/material/button';
import { MatCardModule } from '@angular/material/card';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatInputModule } from '@angular/material/input';
import { MatSnackBar } from '@angular/material/snack-bar';
import { RouterLink } from '@angular/router';

import { Api, describe } from '../core/api';
import { I18n } from '../core/i18n';

interface Waiting {
  sys_id: string;
  title: string;
  link: string;
  requested_by: string;
  for: string | null;
  since: string;
}

/** What waits for the person's approval, oldest first: approve or reject each right here. */
@Component({
  selector: 'app-approvals-page',
  imports: [
    FormsModule,
    MatButtonModule,
    MatCardModule,
    MatFormFieldModule,
    MatInputModule,
    RouterLink,
  ],
  template: `
    <div class="page">
      <header class="page-header">
        <h1>{{ i18n.t('approval.mine') }}</h1>
      </header>
      @for (w of waiting(); track w.sys_id) {
        <mat-card appearance="outlined" class="item">
          <mat-card-header>
            <mat-card-title
              ><a [routerLink]="w.link">{{ w.title }}</a></mat-card-title
            >
            <mat-card-subtitle
              >{{ i18n.t('approval.asked', { name: w.requested_by, date: moment(w.since) }) }}
              @if (w.for) {
                · {{ i18n.t('approval.for', { name: w.for }) }}
              }
            </mat-card-subtitle>
          </mat-card-header>
          <mat-card-content>
            <mat-form-field appearance="outline" class="note">
              <mat-label>{{ i18n.t('approval.note') }}</mat-label>
              <input matInput maxlength="2000" [(ngModel)]="notes[w.sys_id]" />
              <mat-hint>{{ i18n.t('approval.note_hint') }}</mat-hint>
            </mat-form-field>
          </mat-card-content>
          <mat-card-actions>
            <button mat-flat-button type="button" [disabled]="busy()" (click)="act(w, 'approve')">
              {{ i18n.t('approval.approve') }}
            </button>
            <button mat-stroked-button type="button" [disabled]="busy()" (click)="act(w, 'reject')">
              {{ i18n.t('approval.reject') }}
            </button>
          </mat-card-actions>
        </mat-card>
      } @empty {
        @if (loaded()) {
          <p class="muted">{{ i18n.t('approval.none') }}</p>
        }
      }
    </div>
  `,
  styles: `
    .item {
      margin-bottom: 12px;
    }
    .item a {
      color: inherit;
      overflow-wrap: anywhere;
    }
    .note {
      width: min(100%, 520px);
    }
  `,
})
export class ApprovalsPage {
  protected readonly i18n = inject(I18n);
  private readonly api = inject(Api);
  private readonly snack = inject(MatSnackBar);
  protected readonly waiting = signal<Waiting[]>([]);
  protected readonly loaded = signal(false);
  protected readonly busy = signal(false);
  protected notes: Record<string, string> = {};

  constructor() {
    void this.load();
  }

  private async load(): Promise<void> {
    const { data } = await this.api.get<{ approvals: Waiting[] }>('/api/v1/approvals/mine');
    this.waiting.set(data.approvals);
    this.loaded.set(true);
  }

  protected doneLabel(decision: 'approve' | 'reject' | 'withdraw'): string {
    return this.i18n.t(
      decision === 'approve'
        ? 'approval.done_approve'
        : decision === 'reject'
          ? 'approval.done_reject'
          : 'approval.done_withdraw',
    );
  }

  protected moment(value: string): string {
    return new Date(value).toLocaleString(this.i18n.language(), {
      dateStyle: 'medium',
      timeStyle: 'short',
    });
  }

  protected async act(w: Waiting, decision: 'approve' | 'reject'): Promise<void> {
    this.busy.set(true);
    try {
      await this.api.post(`/api/v1/approvals/${w.sys_id}/${decision}`, {
        note: (this.notes[w.sys_id] ?? '').trim() || null,
      });
      this.snack.open(this.doneLabel(decision), undefined, { duration: 3000 });
      await this.load();
    } catch (error) {
      this.snack.open(describe(error), this.i18n.t('common.close'));
    } finally {
      this.busy.set(false);
    }
  }
}
