import { Component, effect, inject, input, output, signal, untracked } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { MatButtonModule } from '@angular/material/button';
import { MatCardModule } from '@angular/material/card';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatInputModule } from '@angular/material/input';
import { MatSnackBar } from '@angular/material/snack-bar';

import { Api, describe } from '../core/api';
import { I18n } from '../core/i18n';
import { path } from '../core/tables';

export interface ApprovalInfo {
  sys_id: string;
  title: string;
  state: 'pending' | 'approved' | 'rejected' | 'withdrawn';
  step: number;
  steps: string[];
  requested_by: string;
  created: string;
  decided: string | null;
  note: string | null;
  approvers: { name: string; for: string | null }[];
  decisions: {
    step: number;
    name: string;
    for: string | null;
    decision: 'approve' | 'reject';
    note: string | null;
    when: string;
  }[];
  may_decide: boolean;
  may_withdraw: boolean;
}

/** A record's approval waiting (or the last one decided): its steps, who decides, what they
 *  decided; Approve, Reject (saying why) or Withdraw for those who may. */
@Component({
  selector: 'app-record-approvals',
  imports: [FormsModule, MatButtonModule, MatCardModule, MatFormFieldModule, MatInputModule],
  template: `
    @if (shown(); as a) {
      <mat-card appearance="outlined" class="approval" [attr.data-state]="a.state">
        <mat-card-header>
          <span mat-card-avatar class="material-symbols-outlined icon" aria-hidden="true">{{
            a.state === 'pending'
              ? 'hourglass_top'
              : a.state === 'approved'
                ? 'check_circle'
                : 'cancel'
          }}</span>
          <mat-card-title>{{ stateLabel(a.state) }}</mat-card-title>
          <mat-card-subtitle>{{
            i18n.t('approval.asked', { name: a.requested_by, date: moment(a.created) })
          }}</mat-card-subtitle>
        </mat-card-header>
        <mat-card-content>
          <ol class="steps">
            @for (s of a.steps; track $index; let i = $index) {
              <li [class.current]="a.state === 'pending' && i === a.step">
                <strong>{{ s }}</strong>
                @for (d of decisionsOf(a, i); track d.when) {
                  <span class="muted">
                    ·
                    {{
                      i18n.t(d.decision === 'approve' ? 'approval.yes_by' : 'approval.no_by', {
                        name: d.name,
                      })
                    }}
                    @if (d.for) {
                      ({{ i18n.t('approval.for', { name: d.for }) }})
                    }
                    @if (d.note) {
                      : “{{ d.note }}”
                    }
                  </span>
                }
                @if (a.state === 'pending' && i === a.step) {
                  <span class="muted">
                    · {{ i18n.t('approval.waiting_for', { names: waitingFor(a) }) }}</span
                  >
                }
              </li>
            }
          </ol>
          @if (a.may_decide) {
            <mat-form-field appearance="outline" class="note">
              <mat-label>{{ i18n.t('approval.note') }}</mat-label>
              <textarea matInput rows="2" maxlength="2000" [(ngModel)]="note"></textarea>
              <mat-hint>{{ i18n.t('approval.note_hint') }}</mat-hint>
            </mat-form-field>
          }
        </mat-card-content>
        @if (a.may_decide || a.may_withdraw) {
          <mat-card-actions>
            @if (a.may_decide) {
              <button mat-flat-button type="button" [disabled]="busy()" (click)="act(a, 'approve')">
                {{ i18n.t('approval.approve') }}
              </button>
              <button
                mat-stroked-button
                type="button"
                [disabled]="busy()"
                (click)="act(a, 'reject')"
              >
                {{ i18n.t('approval.reject') }}
              </button>
            }
            @if (a.may_withdraw) {
              <button mat-button type="button" [disabled]="busy()" (click)="act(a, 'withdraw')">
                {{ i18n.t('approval.withdraw') }}
              </button>
            }
          </mat-card-actions>
        }
      </mat-card>
    }
  `,
  styles: `
    .approval {
      margin-bottom: 16px;
      border-left: 4px solid var(--mat-sys-primary);
    }
    .approval[data-state='rejected'] {
      border-left-color: var(--mat-sys-error);
    }
    .approval[data-state='approved'],
    .approval[data-state='withdrawn'] {
      border-left-color: var(--mat-sys-outline-variant);
    }
    .icon {
      font-size: 32px;
      color: var(--mat-sys-primary);
    }
    .steps {
      margin: 8px 0;
      padding-left: 20px;
    }
    .steps li {
      margin: 4px 0;
      overflow-wrap: anywhere;
    }
    .current strong {
      color: var(--mat-sys-primary);
    }
    .note {
      width: 100%;
      margin-top: 8px;
    }
  `,
})
export class RecordApprovals {
  protected readonly i18n = inject(I18n);
  private readonly api = inject(Api);
  private readonly snack = inject(MatSnackBar);
  readonly table = input.required<string>();
  readonly id = input.required<string>();
  /** The record's version: an approval asked through its button shows at once. */
  readonly version = input<unknown>(null);
  readonly changed = output<void>();
  protected readonly shown = signal<ApprovalInfo | null>(null);
  protected readonly busy = signal(false);
  protected note = '';

  constructor() {
    effect(() => {
      const table = this.table();
      const id = this.id();
      this.version();
      untracked(() => void this.load(table, id));
    });
  }

  private async load(table: string, id: string): Promise<void> {
    try {
      const { data } = await this.api.get<{ approvals: ApprovalInfo[] }>(
        `${path(table, id)}/approvals`,
        { quiet: true },
      );
      // The one waiting; else the last decision, if it was a no (the reason matters).
      const waiting = data.approvals.find((a) => a.state === 'pending');
      const last = data.approvals[0];
      this.shown.set(waiting ?? (last?.state === 'rejected' ? last : null));
    } catch {
      this.shown.set(null);
    }
  }

  protected decisionsOf(a: ApprovalInfo, step: number): ApprovalInfo['decisions'] {
    return a.decisions.filter((d) => d.step === step);
  }

  protected waitingFor(a: ApprovalInfo): string {
    return a.approvers
      .map((p) => (p.for ? `${p.name} (${this.i18n.t('approval.for', { name: p.for })})` : p.name))
      .join(', ');
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

  protected stateLabel(state: ApprovalInfo['state']): string {
    return this.i18n.t(
      state === 'pending'
        ? 'approval.pending'
        : state === 'approved'
          ? 'approval.approved'
          : state === 'rejected'
            ? 'approval.rejected'
            : 'approval.withdrawn',
    );
  }

  protected moment(value: string): string {
    return new Date(value).toLocaleString(this.i18n.language(), {
      dateStyle: 'medium',
      timeStyle: 'short',
    });
  }

  protected async act(a: ApprovalInfo, decision: 'approve' | 'reject' | 'withdraw'): Promise<void> {
    this.busy.set(true);
    try {
      await this.api.post(`/api/v1/approvals/${a.sys_id}/${decision}`, {
        note: this.note.trim() || null,
      });
      this.note = '';
      this.snack.open(this.doneLabel(decision), undefined, { duration: 3000 });
      await this.load(this.table(), this.id());
      this.changed.emit();
    } catch (error) {
      this.snack.open(describe(error), this.i18n.t('common.close'));
    } finally {
      this.busy.set(false);
    }
  }
}
