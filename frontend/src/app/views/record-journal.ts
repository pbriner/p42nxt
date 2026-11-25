import { Component, computed, effect, inject, input, signal, untracked } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { MatButtonModule } from '@angular/material/button';
import { MatButtonToggleModule } from '@angular/material/button-toggle';
import { MatCardModule } from '@angular/material/card';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatIconModule } from '@angular/material/icon';
import { MatInputModule } from '@angular/material/input';

import { Api, describe } from '../core/api';
import { FieldInfo, shown } from '../core/format';
import { I18n } from '../core/i18n';
import { Tables, path } from '../core/tables';

type Kind = 'comment' | 'work_note';
type Show = 'all' | 'said' | 'changes';

interface Said {
  sys_id: string;
  text: string;
  created: string;
  by_name: string | null;
  mine: boolean;
  kind: Kind;
}

interface Logged {
  at: string;
  operation: string;
  by_name: string | null;
  /** How the changed values read: a reference's name (null: not theirs to read), a label. */
  display?: Record<string, unknown[]>;
  changes: Record<string, unknown>;
}

interface Change {
  label: string;
  /** Null for a new record: it had no values before. */
  before: string | null;
  after: string;
}

/** One entry of the journal: something said (a comment or a work note) or a change. */
type Entry =
  | { type: 'said'; at: string; said: Said }
  | { type: 'change'; at: string; operation: string; by: string | null; changes: Change[] };

const OPERATIONS: Record<
  string,
  'history.insert' | 'history.update' | 'history.delete' | 'history.restore'
> = {
  insert: 'history.insert',
  update: 'history.update',
  delete: 'history.delete',
  restore: 'history.restore',
};

/** A record's journal: its comments, its work notes (only for those who may change it) and its
 *  changes from the audit log, in one timeline, newest first. Everything said is shown as
 *  plain text, never as HTML. */
@Component({
  selector: 'app-record-journal',
  imports: [
    FormsModule,
    MatButtonModule,
    MatButtonToggleModule,
    MatCardModule,
    MatFormFieldModule,
    MatIconModule,
    MatInputModule,
  ],
  template: `
    <mat-card appearance="outlined" class="section">
      <mat-card-header>
        <mat-card-title>{{ i18n.t('journal.title') }}</mat-card-title>
      </mat-card-header>
      <mat-card-content>
        @if (kinds().length) {
          <form class="write" (submit)="$event.preventDefault(); add()">
            @if (kinds().length > 1) {
              <mat-button-toggle-group
                class="kinds"
                hideSingleSelectionIndicator
                [attr.aria-label]="i18n.t('journal.kind')"
                [value]="kind()"
                (change)="pick($event.value)"
              >
                <mat-button-toggle value="work_note">{{
                  i18n.t('journal.work_note')
                }}</mat-button-toggle>
                <mat-button-toggle value="comment">{{
                  i18n.t('journal.comment')
                }}</mat-button-toggle>
              </mat-button-toggle-group>
            }
            <mat-form-field
              appearance="outline"
              class="box"
              [class.noting]="kind() === 'work_note'"
            >
              <mat-label>{{
                kind() === 'work_note' ? i18n.t('journal.write_note') : i18n.t('comments.write')
              }}</mat-label>
              <textarea
                matInput
                id="record-comment-text"
                rows="2"
                maxlength="5000"
                name="text"
                [(ngModel)]="draft"
              ></textarea>
              @if (kind() === 'work_note') {
                <mat-hint>{{ i18n.t('journal.note_hint') }}</mat-hint>
              }
            </mat-form-field>
            <button mat-flat-button type="submit" [disabled]="busy() || !draft.trim()">
              {{ kind() === 'work_note' ? i18n.t('journal.add_note') : i18n.t('comments.add') }}
            </button>
          </form>
        }
        @if (conversation()) {
          <mat-button-toggle-group
            class="show"
            hideSingleSelectionIndicator
            [attr.aria-label]="i18n.t('journal.show')"
            [value]="show()"
            (change)="show.set($event.value)"
          >
            <mat-button-toggle value="all">{{ i18n.t('journal.all') }}</mat-button-toggle>
            <mat-button-toggle value="said">{{ i18n.t('journal.said') }}</mat-button-toggle>
            <mat-button-toggle value="changes">{{ i18n.t('journal.changes') }}</mat-button-toggle>
          </mat-button-toggle-group>
        }
        @if (loaded()) {
          @for (e of shown(); track $index) {
            @if (e.type === 'said') {
              <div class="entry said" [class.note]="e.said.kind === 'work_note'">
                <p class="who">
                  @if (e.said.kind === 'work_note') {
                    <span class="badge">{{ i18n.t('journal.work_note') }}</span>
                  }
                  <strong>{{ e.said.by_name ?? i18n.t('form.the_system') }}</strong>
                  <span class="muted"> · {{ when(e.at) }}</span>
                  @if (e.said.mine) {
                    <button
                      mat-icon-button
                      type="button"
                      class="remove"
                      [disabled]="busy()"
                      [attr.aria-label]="
                        e.said.kind === 'work_note'
                          ? i18n.t('journal.remove_note')
                          : i18n.t('comments.remove')
                      "
                      (click)="remove(e.said)"
                    >
                      <mat-icon>delete</mat-icon>
                    </button>
                  }
                </p>
                <p class="text">{{ e.said.text }}</p>
              </div>
            } @else {
              <div class="entry change">
                <p class="who">
                  <mat-icon class="small" aria-hidden="true">edit_note</mat-icon>
                  <strong>{{ operation(e.operation) }}</strong>
                  <span class="muted">
                    · {{ when(e.at) }} · {{ e.by ?? i18n.t('form.the_system') }}</span
                  >
                </p>
                @if (e.changes.length) {
                  <ul>
                    @for (c of e.changes; track c.label) {
                      <li>
                        @if (c.before === null) {
                          {{ c.label }}: {{ c.after }}
                        } @else {
                          {{ c.label }}: <span class="muted">{{ c.before }}</span> → {{ c.after }}
                        }
                      </li>
                    }
                  </ul>
                }
              </div>
            }
          } @empty {
            <p class="muted">{{ i18n.t('journal.none') }}</p>
          }
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
    .kinds,
    .show {
      flex: 0 0 auto;
      max-width: 100%;
      overflow-x: auto;
    }
    .show {
      margin: 8px 0;
    }
    .box {
      flex: 1 1 260px;
      min-width: 0;
    }
    .entry {
      padding: 0 8px;
      border-radius: 8px;
    }
    .entry + .entry {
      margin-top: 4px;
    }
    .note {
      background: var(--mat-sys-surface-container-high);
      border-left: 4px solid var(--mat-sys-tertiary);
    }
    .badge {
      font: var(--mat-sys-label-small);
      color: var(--mat-sys-on-tertiary-container);
      background: var(--mat-sys-tertiary-container);
      border-radius: 4px;
      padding: 2px 6px;
    }
    .who {
      display: flex;
      align-items: center;
      flex-wrap: wrap;
      gap: 4px;
      margin: 8px 0 0;
    }
    .small {
      font-size: 18px;
      width: 18px;
      height: 18px;
      color: var(--mat-sys-on-surface-variant);
    }
    .said {
      position: relative;
    }
    .said .who {
      padding-right: 44px;
    }
    .remove {
      position: absolute;
      top: 0;
      right: 0;
    }
    .text {
      white-space: pre-wrap;
      overflow-wrap: anywhere;
      margin: 4px 0 8px;
    }
    ul {
      margin: 4px 0 8px;
      padding-left: 28px;
      overflow-wrap: anywhere;
    }
  `,
})
export class RecordJournal {
  protected readonly i18n = inject(I18n);
  private readonly api = inject(Api);
  private readonly tables = inject(Tables);
  readonly table = input.required<string>();
  readonly id = input.required<string>();
  /** Its records have comments. */
  readonly comments = input(false);
  /** Its records have work notes. */
  readonly workNotes = input(false);
  /** The person may change this record (and so reads and adds its work notes). */
  readonly mayChange = input(false);
  /** Nothing is said on a deleted record; its changes still show. */
  readonly deleted = input(false);
  /** The record's version: when it changes (saved, moved on), the journal is read again. */
  readonly version = input<unknown>(null);

  protected readonly kinds = computed<Kind[]>(() => {
    if (this.deleted()) return [];
    const found: Kind[] = [];
    if (this.workNotes() && this.mayChange()) found.push('work_note');
    if (this.comments()) found.push('comment');
    return found;
  });
  protected readonly conversation = computed(
    () => !this.deleted() && (this.comments() || (this.workNotes() && this.mayChange())),
  );
  protected readonly kind = signal<Kind>('comment');
  private picked = false; // the person chose what to add: kept
  protected readonly show = signal<Show>('all');
  private readonly said = signal<Said[]>([]);
  private readonly logged = signal<Logged[]>([]);
  private fields = new Map<string, FieldInfo>();
  protected readonly loaded = signal(false);
  protected readonly busy = signal(false);
  protected readonly problem = signal<string | null>(null);
  protected draft = '';

  protected readonly entries = computed<Entry[]>(() => {
    const said: Entry[] = this.said().map((s) => ({ type: 'said', at: s.created, said: s }));
    const changes: Entry[] = this.logged().map((l) => ({
      type: 'change',
      at: l.at,
      operation: l.operation,
      by: l.by_name,
      changes: this.changes(l),
    }));
    // Newest first; at the same moment what was said comes before the change it explains.
    return [...said, ...changes].sort((a, b) => Date.parse(b.at) - Date.parse(a.at));
  });
  protected readonly shown = computed(() => {
    const show = this.show();
    return show === 'all'
      ? this.entries()
      : this.entries().filter((e) => (e.type === 'said') === (show === 'said'));
  });

  constructor() {
    effect(() => {
      this.version();
      this.id();
      void untracked(() => this.load());
    });
    effect(() => {
      // Work notes first where there are any: what the team says stays with the team by default.
      const first = this.kinds()[0] ?? 'comment';
      untracked(() => {
        if (!this.picked || !this.kinds().includes(this.kind())) this.kind.set(first);
      });
    });
  }

  protected pick(kind: Kind): void {
    this.picked = true;
    this.kind.set(kind);
  }

  private async load(): Promise<void> {
    try {
      const [info, history, said] = await Promise.all([
        this.tables.describe(this.table()),
        this.api.get<{ entries: Logged[] }>(`${path(this.table())}/${this.id()}/history`),
        this.conversation()
          ? this.api.get<{ comments: Said[] }>(`${path(this.table(), this.id())}/comments`)
          : Promise.resolve({ data: { comments: [] as Said[] } }),
      ]);
      this.fields = new Map(info.fields.map((f) => [f.name, f]));
      this.logged.set(history.data.entries);
      this.said.set(said.data.comments);
      this.loaded.set(true);
    } catch (error) {
      this.problem.set(describe(error));
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

  private changes(entry: Logged): Change[] {
    return Object.entries(entry.changes).map(([name, value]) => {
      const pair = Array.isArray(value) ? value : [null, value];
      const named = entry.display?.[name];
      return {
        label: this.fields.get(name)?.label ?? name,
        before: entry.operation === 'insert' ? null : this.text(name, pair[0], named?.[0]),
        after: this.text(name, pair[1], named?.[1]),
      };
    });
  }

  /** A changed value as lists show it: names for references, labels for choices, dates and
   *  numbers in the person's language. */
  private text(name: string, value: unknown, named: unknown): string {
    if (value === null || value === undefined || value === '') {
      return '—';
    }
    if (named !== undefined) {
      return named === null ? this.i18n.t('ref.hidden') : String(named);
    }
    return shown({ [name]: value }, this.fields.get(name), name, this.i18n.language(), (key) =>
      this.i18n.t(key),
    );
  }

  protected async add(): Promise<void> {
    const text = this.draft.trim();
    if (!text) return;
    this.busy.set(true);
    this.problem.set(null);
    try {
      await this.api.post(`${path(this.table(), this.id())}/comments`, { text, kind: this.kind() });
      this.draft = '';
      await this.load();
    } catch (error) {
      this.problem.set(describe(error));
    } finally {
      this.busy.set(false);
    }
  }

  protected async remove(said: Said): Promise<void> {
    this.busy.set(true);
    this.problem.set(null);
    try {
      await this.api.delete(`/api/v1/comments/${said.sys_id}`, '');
      await this.load();
    } catch (error) {
      this.problem.set(describe(error));
    } finally {
      this.busy.set(false);
    }
  }
}
