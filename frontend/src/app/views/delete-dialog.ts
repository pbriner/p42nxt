import { Component, computed, inject, signal } from '@angular/core';
import { MatButtonModule } from '@angular/material/button';
import {
  MAT_DIALOG_DATA,
  MatDialog,
  MatDialogModule,
  MatDialogRef,
} from '@angular/material/dialog';
import { MatProgressBarModule } from '@angular/material/progress-bar';
import { RouterLink } from '@angular/router';
import { firstValueFrom } from 'rxjs';

import { Api, describe } from '../core/api';
import { I18n } from '../core/i18n';
import { filterQuery } from '../core/navigation';
import { path } from '../core/tables';

export interface DeleteOf {
  table: string;
  id: string;
  /** The table's label (Partner) and the record's name (Shop). */
  item: string;
  name: string;
}

interface Group {
  table: string;
  label: string;
  label_one: string;
  field: string;
  field_label: string;
  count: number;
  records: { sys_id: string; name: unknown }[];
  may_delete: boolean;
}

interface Preview {
  fingerprint: string;
  changes: number;
  cascade: Group[];
  clear: Group[];
  blocked: Group[];
  kept: Group[];
}

/** Asks before deleting, showing what else the delete changes; closes with the confirmation to
 * send (the preview's fingerprint), or nothing. */
@Component({
  selector: 'app-delete-dialog',
  imports: [MatButtonModule, MatDialogModule, MatProgressBarModule, RouterLink],
  template: `
    <h2 mat-dialog-title>{{ i18n.t('list.delete_title', { item: of.item.toLowerCase() }) }}</h2>
    <mat-dialog-content>
      @if (busy()) {
        <mat-progress-bar mode="indeterminate" />
      } @else if (failed(); as why) {
        <p role="alert">{{ why }}</p>
      } @else if (preview(); as p) {
        @if (p.blocked.length) {
          <p role="alert">{{ i18n.t('delete.blocked', { name: of.name }) }}</p>
          <ul>
            @for (g of p.blocked; track g.table + g.field) {
              <li>
                <a [routerLink]="['/list', g.table]" [queryParams]="pointing(g)" mat-dialog-close>
                  {{ counted(g) }}
                </a>
                ({{ g.field_label }})
              </li>
            }
          </ul>
        } @else {
          <p>{{ i18n.t('list.delete_text', { name: of.name }) }}</p>
          @if (p.cascade.length) {
            <h3>{{ i18n.t('delete.also') }}</h3>
            <ul>
              @for (g of p.cascade; track g.table + g.field) {
                <li>
                  <strong>{{ counted(g) }}</strong>
                  @if (g.records.length) {
                    : {{ names(g) }}
                  }
                  @if (!g.may_delete) {
                    <span class="warn">— {{ i18n.t('delete.not_yours') }}</span>
                  }
                </li>
              }
            </ul>
          }
          @if (p.clear.length) {
            <h3>{{ i18n.t('delete.emptied') }}</h3>
            <ul>
              @for (g of p.clear; track g.table + g.field) {
                <li>
                  {{ i18n.t('delete.emptied_in', { field: g.field_label, what: counted(g) }) }}
                </li>
              }
            </ul>
          }
          @if (p.kept.length) {
            <h3>{{ i18n.t('delete.kept') }}</h3>
            <ul>
              @for (g of p.kept; track g.table + g.field) {
                <li>{{ counted(g) }} ({{ g.field_label }})</li>
              }
            </ul>
          }
          @if (p.changes) {
            <p class="hint">{{ i18n.t('delete.restore') }}</p>
          }
        }
      }
    </mat-dialog-content>
    <mat-dialog-actions align="end">
      <button mat-button type="button" [mat-dialog-close]="null">
        {{ i18n.t('common.cancel') }}
      </button>
      @if (allowed()) {
        <button mat-flat-button type="button" (click)="confirm()" cdkFocusInitial>
          {{ i18n.t('list.delete') }}
        </button>
      }
    </mat-dialog-actions>
  `,
  styles: `
    h3 {
      font: var(--mat-sys-title-small);
      margin: 12px 0 4px;
    }
    ul {
      margin: 0;
      padding-inline-start: 20px;
    }
    .warn {
      color: var(--mat-sys-error);
    }
    .hint {
      color: var(--mat-sys-on-surface-variant);
      margin-top: 12px;
    }
  `,
})
export class DeleteDialog {
  protected readonly i18n = inject(I18n);
  private readonly api = inject(Api);
  private readonly ref = inject(MatDialogRef<DeleteDialog, string | null>);
  protected readonly of = inject<DeleteOf>(MAT_DIALOG_DATA);

  protected readonly busy = signal(true);
  protected readonly failed = signal<string | null>(null);
  protected readonly preview = signal<Preview | null>(null);
  protected readonly allowed = computed(() => {
    const p = this.preview();
    return !!p && !p.blocked.length && p.cascade.every((g) => g.may_delete);
  });

  constructor() {
    void this.load();
  }

  private async load(): Promise<void> {
    try {
      const { data } = await this.api.get<Preview>(`${path(this.of.table, this.of.id)}/deletion`);
      this.preview.set(data);
    } catch (error) {
      this.failed.set(describe(error));
    } finally {
      this.busy.set(false);
    }
  }

  protected counted(g: Group): string {
    return `${g.count} ${g.count === 1 ? g.label_one : g.label}`;
  }

  protected names(g: Group): string {
    const shown = g.records.map((r) => String(r.name ?? '')).filter((n) => n);
    const more = g.count - shown.length;
    return more > 0
      ? `${shown.join(', ')} ${this.i18n.t('delete.more', { count: more })}`
      : shown.join(', ');
  }

  protected pointing(g: Group): Record<string, string> {
    return filterQuery({ field: g.field, op: 'eq', value: this.of.id });
  }

  protected confirm(): void {
    this.ref.close(this.preview()!.fingerprint);
  }
}

/** The confirmation to send with the delete, or null when they cancelled. */
export async function askToDelete(dialog: MatDialog, of: DeleteOf): Promise<string | null> {
  const ref = dialog.open<DeleteDialog, DeleteOf, string | null>(DeleteDialog, {
    data: of,
    width: '560px',
    maxWidth: '100vw',
  });
  return (await firstValueFrom(ref.afterClosed())) ?? null;
}
