import { Component, DestroyRef, computed, inject, signal } from '@angular/core';
import { MatButtonModule } from '@angular/material/button';
import { MatTooltipModule } from '@angular/material/tooltip';

import { Api } from '../core/api';
import { I18n } from '../core/i18n';
import { remember, remembered } from '../core/storage';
import { path } from '../core/tables';

const TABLE = 'sys.announcement';
const EVERY = 60_000;
const CLOSED = 'p42.announcements.closed';
const RANK: Record<string, number> = { critical: 0, warning: 1, information: 2 };
const ICONS: Record<string, string> = {
  critical: 'error',
  warning: 'warning',
  information: 'info',
};

interface Announcement {
  sys_id: string;
  kind: string;
  subject: string;
  text: string | null;
  valid_from: string;
}

/** The messages for the person (everyone's and their groups'), in a band under the top bar: most severe first, then newest.
 * Information can be closed; warnings and critical messages stay while they are valid. */
@Component({
  selector: 'app-announcements',
  imports: [MatButtonModule, MatTooltipModule],
  template: `
    @for (a of shown(); track a.sys_id) {
      <div
        class="message"
        [class]="'message ' + a.kind"
        [attr.role]="a.kind === 'information' ? 'status' : 'alert'"
      >
        <span class="material-symbols-outlined icon" aria-hidden="true">{{ icons[a.kind] }}</span>
        <p class="words">
          <strong>{{ a.subject }}</strong>
          @if (a.text) {
            <span class="text">{{ a.text }}</span>
          }
        </p>
        @if (a.kind === 'information') {
          <button
            mat-icon-button
            type="button"
            [attr.aria-label]="i18n.t('messages.close', { subject: a.subject })"
            [matTooltip]="i18n.t('common.close')"
            (click)="close(a.sys_id)"
          >
            <span class="material-symbols-outlined" aria-hidden="true">close</span>
          </button>
        }
      </div>
    }
  `,
  styles: `
    :host {
      display: block;
    }
    .message {
      display: flex;
      align-items: center;
      gap: 12px;
      padding: 4px 16px;
      min-height: 44px;
      box-sizing: border-box;
    }
    .information {
      background: var(--app-message-information);
      color: var(--app-on-message-information);
    }
    .warning {
      background: var(--app-message-warning);
      color: var(--app-on-message-warning);
    }
    .critical {
      background: var(--app-message-critical);
      color: var(--app-on-message-critical);
    }
    .words {
      flex: 1;
      margin: 6px 0;
      overflow-wrap: anywhere;
    }
    .text {
      margin-left: 8px;
    }
    button {
      color: inherit;
    }
  `,
})
export class Announcements {
  protected readonly i18n = inject(I18n);
  private readonly api = inject(Api);
  protected readonly icons = ICONS;
  private readonly all = signal<Announcement[]>([]);
  private readonly closed = signal(new Set(remembered(CLOSED)));
  protected readonly shown = computed(() =>
    this.all()
      .filter((a) => a.kind !== 'information' || !this.closed().has(a.sys_id))
      .sort(
        (a, b) =>
          (RANK[a.kind] ?? 3) - (RANK[b.kind] ?? 3) || b.valid_from.localeCompare(a.valid_from),
      ),
  );

  constructor() {
    void this.load();
    const timer = setInterval(() => void this.load(), EVERY);
    inject(DestroyRef).onDestroy(() => clearInterval(timer));
  }

  async load(): Promise<void> {
    const now = new Date().toISOString();
    const where = {
      and: [
        { field: 'deliver', op: 'in', value: ['band', 'both'] },
        { field: 'valid_from', op: 'lte', value: now },
        {
          or: [
            { field: 'valid_until', op: 'empty' },
            { field: 'valid_until', op: 'gt', value: now },
          ],
        },
      ],
    };
    try {
      const { data } = await this.api.get<{ records: Announcement[] }>(path(TABLE), {
        query: {
          where: JSON.stringify(where),
          sort: '-valid_from',
          limit: 20,
          count: false,
          fields: 'kind,subject,text,valid_from',
        },
        background: true,
      });
      this.all.set(data.records);
      // Forget closed messages that are no longer shown, so the list stays small.
      const live = new Set(data.records.map((a) => a.sys_id));
      const kept = [...this.closed()].filter((id) => live.has(id));
      if (kept.length !== this.closed().size) {
        this.closed.set(new Set(kept));
        remember(CLOSED, kept);
      }
    } catch {
      // The band is extra: if it can't load, the page works without it.
    }
  }

  protected close(id: string): void {
    const next = new Set(this.closed()).add(id);
    this.closed.set(next);
    remember(CLOSED, next);
  }
}
