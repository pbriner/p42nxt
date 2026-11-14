import { Component, DestroyRef, computed, inject, signal } from '@angular/core';
import { MatButtonModule } from '@angular/material/button';
import { MatMenuModule } from '@angular/material/menu';
import { MatTooltipModule } from '@angular/material/tooltip';
import { Router, RouterLink } from '@angular/router';

import { Api } from '../core/api';
import { I18n } from '../core/i18n';
import { path } from '../core/tables';

const TABLE = 'sys.notification';
const EVERY = 60_000;
const SHOWN = 10;
const UNREAD = JSON.stringify({ field: 'read', op: 'eq', value: false });
const ICONS: Record<string, string> = {
  info: 'info',
  success: 'check_circle',
  warning: 'warning',
  error: 'error',
};

interface Note {
  sys_id: string;
  title: string;
  message: string | null;
  link: string | null;
  severity: string;
  created: string;
}

/** The bell: the person's unread notifications, asked for once a minute. */
@Component({
  selector: 'app-bell',
  imports: [MatButtonModule, MatMenuModule, MatTooltipModule, RouterLink],
  templateUrl: './bell.html',
  styleUrl: './bell.scss',
})
export class Bell {
  protected readonly i18n = inject(I18n);
  private readonly api = inject(Api);
  private readonly router = inject(Router);

  protected readonly notes = signal<Note[]>([]);
  protected readonly unread = signal(0);
  protected readonly failed = signal(false);
  protected readonly badge = computed(() => (this.unread() > 99 ? '99+' : String(this.unread())));
  protected readonly label = computed(() =>
    this.unread()
      ? this.i18n.t('bell.label_unread', { n: this.unread() })
      : this.i18n.t('bell.label'),
  );
  protected readonly icons = ICONS;

  constructor() {
    void this.load();
    const timer = setInterval(() => void this.load(), EVERY);
    inject(DestroyRef).onDestroy(() => clearInterval(timer));
  }

  async load(): Promise<void> {
    try {
      const { data } = await this.api.get<{ records: Note[]; total: number | null }>(path(TABLE), {
        query: {
          where: UNREAD,
          sort: '-created',
          limit: SHOWN,
          fields: 'title,message,link,severity,created',
        },
        background: true,
      });
      this.notes.set(data.records);
      this.unread.set(data.total ?? data.records.length);
      this.failed.set(false);
    } catch {
      this.failed.set(true);
    }
  }

  protected when(created: string): string {
    const minutes = Math.floor((Date.now() - new Date(created).getTime()) / 60_000);
    if (minutes < 1) {
      return this.i18n.t('bell.just_now');
    }
    if (minutes < 60) {
      return this.i18n.t('bell.minutes', { n: minutes });
    }
    if (minutes < 24 * 60) {
      return this.i18n.t('bell.hours', { n: Math.floor(minutes / 60) });
    }
    return new Date(created).toLocaleDateString(this.i18n.language());
  }

  /** Opening one marks it read, then follows its link. */
  protected async open(note: Note): Promise<void> {
    this.notes.update((all) => all.filter((n) => n.sys_id !== note.sys_id));
    this.unread.update((n) => Math.max(0, n - 1));
    try {
      await this.api.post(`${path(TABLE, note.sys_id)}/action/sys.mark_read`, { inputs: {} });
    } finally {
      if (note.link?.startsWith('/') && !note.link.startsWith('//')) {
        await this.router.navigateByUrl(note.link);
      }
      void this.load();
    }
  }

  protected async markAll(): Promise<void> {
    const ids = this.notes().map((n) => n.sys_id);
    if (!ids.length) {
      return;
    }
    await this.api.post(`${path(TABLE)}/action/sys.mark_read`, { ids, inputs: {} });
    await this.load();
  }
}
