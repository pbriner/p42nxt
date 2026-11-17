import { Component, inject, input, OnInit, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { MatButtonModule } from '@angular/material/button';
import { MatCardModule } from '@angular/material/card';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatIconModule } from '@angular/material/icon';
import { MatInputModule } from '@angular/material/input';

import { Api, describe } from '../core/api';
import { I18n } from '../core/i18n';
import { path } from '../core/tables';

interface Said {
  sys_id: string;
  text: string;
  created: string;
  by_name: string | null;
  mine: boolean;
}

/** A record's conversation: everyone who may read the record reads and adds comments; authors
 *  remove their own. Comments are shown as plain text, never as HTML. */
@Component({
  selector: 'app-record-comments',
  imports: [
    FormsModule,
    MatButtonModule,
    MatCardModule,
    MatFormFieldModule,
    MatIconModule,
    MatInputModule,
  ],
  template: `
    <mat-card appearance="outlined" class="section">
      <mat-card-header>
        <mat-card-title>{{ i18n.t('comments.title') }}</mat-card-title>
      </mat-card-header>
      <mat-card-content>
        <form class="write" (submit)="$event.preventDefault(); add()">
          <mat-form-field appearance="outline" class="box">
            <mat-label>{{ i18n.t('comments.write') }}</mat-label>
            <textarea
              matInput
              id="record-comment-text"
              rows="2"
              maxlength="5000"
              name="text"
              [(ngModel)]="draft"
            ></textarea>
          </mat-form-field>
          <button mat-flat-button type="submit" [disabled]="busy() || !draft.trim()">
            {{ i18n.t('comments.add') }}
          </button>
        </form>
        @for (c of comments(); track c.sys_id) {
          <div class="said">
            <p class="who">
              <strong>{{ c.by_name ?? i18n.t('form.the_system') }}</strong>
              <span class="muted"> · {{ when(c.created) }}</span>
              @if (c.mine) {
                <button
                  mat-icon-button
                  type="button"
                  class="remove"
                  [disabled]="busy()"
                  [attr.aria-label]="i18n.t('comments.remove')"
                  (click)="remove(c)"
                >
                  <mat-icon>delete</mat-icon>
                </button>
              }
            </p>
            <p class="text">{{ c.text }}</p>
          </div>
        } @empty {
          <p class="muted">{{ i18n.t('comments.none') }}</p>
        }
        @if (problem(); as said) {
          <p role="alert">{{ said }}</p>
        }
      </mat-card-content>
    </mat-card>
  `,
  styles: `
    .write {
      display: flex;
      gap: 8px;
      align-items: flex-start;
      flex-wrap: wrap;
    }
    .box {
      flex: 1 1 260px;
      min-width: 0;
    }
    .said + .said {
      border-top: 1px solid var(--mat-sys-outline-variant);
    }
    .who {
      display: flex;
      align-items: center;
      gap: 4px;
      margin: 8px 0 0;
    }
    .remove {
      margin-left: auto;
    }
    .text {
      white-space: pre-wrap;
      overflow-wrap: anywhere;
      margin: 4px 0 8px;
    }
  `,
})
export class RecordComments implements OnInit {
  protected readonly i18n = inject(I18n);
  private readonly api = inject(Api);
  readonly table = input.required<string>();
  readonly id = input.required<string>();
  protected readonly comments = signal<Said[]>([]);
  protected readonly busy = signal(false);
  protected readonly problem = signal<string | null>(null);
  protected draft = '';

  async ngOnInit(): Promise<void> {
    await this.load();
  }

  private async load(): Promise<void> {
    try {
      const { data } = await this.api.get<{ comments: Said[] }>(
        `${path(this.table(), this.id())}/comments`,
      );
      this.comments.set(data.comments);
    } catch (error) {
      this.problem.set(describe(error));
    }
  }

  protected when(at: string): string {
    return new Date(at).toLocaleString(this.i18n.language(), {
      dateStyle: 'medium',
      timeStyle: 'short',
    });
  }

  protected async add(): Promise<void> {
    const text = this.draft.trim();
    if (!text) return;
    this.busy.set(true);
    this.problem.set(null);
    try {
      await this.api.post(`${path(this.table(), this.id())}/comments`, { text });
      this.draft = '';
      await this.load();
    } catch (error) {
      this.problem.set(describe(error));
    } finally {
      this.busy.set(false);
    }
  }

  protected async remove(c: Said): Promise<void> {
    this.busy.set(true);
    this.problem.set(null);
    try {
      await this.api.delete(`/api/v1/comments/${c.sys_id}`, '');
      await this.load();
    } catch (error) {
      this.problem.set(describe(error));
    } finally {
      this.busy.set(false);
    }
  }
}
