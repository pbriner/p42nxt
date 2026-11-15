import { Component, inject, input, signal } from '@angular/core';
import { MatButtonModule } from '@angular/material/button';
import { MatCardModule } from '@angular/material/card';

import { Api, describe } from '../core/api';
import { I18n } from '../core/i18n';
import { Tables, path } from '../core/tables';

interface Entry {
  at: string;
  operation: string;
  by: string;
  by_name: string | null;
  channel: string;
  changes: Record<string, unknown>;
}

const OPERATIONS: Record<
  string,
  'history.insert' | 'history.update' | 'history.delete' | 'history.restore'
> = {
  insert: 'history.insert',
  update: 'history.update',
  delete: 'history.delete',
  restore: 'history.restore',
};

interface Change {
  label: string;
  before: string;
  after: string;
}

/** A record's changes from the audit log, loaded when asked for: who changed what, when. */
@Component({
  selector: 'app-record-history',
  imports: [MatButtonModule, MatCardModule],
  template: `
    <mat-card appearance="outlined" class="section">
      <mat-card-header>
        <mat-card-title>{{ i18n.t('history.title') }}</mat-card-title>
      </mat-card-header>
      <mat-card-content>
        @if (entries(); as list) {
          @for (e of list; track $index) {
            <div class="entry">
              <p class="line">
                <strong>{{ operation(e.operation) }}</strong>
                <span class="muted"
                  >· {{ when(e.at) }} · {{ e.by_name ?? i18n.t('form.the_system') }}</span
                >
              </p>
              @if (changes(e); as shown) {
                <ul>
                  @for (c of shown; track c.label) {
                    <li>
                      {{ c.label }}: <span class="muted">{{ c.before }}</span> → {{ c.after }}
                    </li>
                  }
                </ul>
              }
            </div>
          } @empty {
            <p class="muted">{{ i18n.t('history.none') }}</p>
          }
        } @else {
          <button mat-stroked-button type="button" [disabled]="busy()" (click)="load()">
            {{ i18n.t('history.show') }}
          </button>
        }
        @if (problem(); as said) {
          <p role="alert">{{ said }}</p>
        }
      </mat-card-content>
    </mat-card>
  `,
  styles: `
    .entry + .entry {
      border-top: 1px solid var(--mat-sys-outline-variant);
      padding-top: 8px;
    }
    .line {
      margin: 8px 0 4px;
    }
    ul {
      margin: 0 0 8px;
      padding-left: 20px;
    }
  `,
})
export class RecordHistory {
  protected readonly i18n = inject(I18n);
  private readonly api = inject(Api);
  private readonly tables = inject(Tables);
  readonly table = input.required<string>();
  readonly id = input.required<string>();
  protected readonly entries = signal<Entry[] | null>(null);
  private labels = new Map<string, string>();
  protected readonly busy = signal(false);
  protected readonly problem = signal<string | null>(null);

  protected async load(): Promise<void> {
    this.busy.set(true);
    try {
      const [info, { data }] = await Promise.all([
        this.tables.describe(this.table()),
        this.api.get<{ entries: Entry[] }>(`${path(this.table())}/${this.id()}/history`),
      ]);
      this.labels = new Map(info.fields.map((f) => [f.name, f.label]));
      this.entries.set(data.entries);
    } catch (error) {
      this.problem.set(describe(error));
    } finally {
      this.busy.set(false);
    }
  }

  protected operation(name: string): string {
    const key = OPERATIONS[name];
    return key ? this.i18n.t(key) : name;
  }

  protected when(at: string): string {
    return new Date(at).toLocaleString(this.i18n.language(), {
      dateStyle: 'medium',
      timeStyle: 'short',
    });
  }

  protected changes(e: Entry): Change[] {
    return Object.entries(e.changes).map(([name, value]) => {
      const pair = Array.isArray(value) ? value : ['', value];
      return {
        label: this.labels.get(name) ?? name,
        before: this.text(pair[0]),
        after: this.text(pair[1]),
      };
    });
  }

  private text(value: unknown): string {
    if (value === null || value === undefined || value === '') {
      return '—';
    }
    return typeof value === 'object' ? JSON.stringify(value) : String(value);
  }
}
