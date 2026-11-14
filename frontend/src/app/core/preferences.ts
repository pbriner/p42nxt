import { Injectable, inject, signal } from '@angular/core';

import { Api } from './api';
import { I18n } from './i18n';
import { Mode, Themes } from './theme';

export interface Preference {
  sys_id: string;
  _etag: string;
  mode: Mode;
  theme: string | null;
  page_size: number;
  language: string | null;
}

export const PAGE_SIZES = [10, 20, 30, 40, 50, 100, 500, 5000];
const DEFAULTS = { mode: 'system' as Mode, theme: null, page_size: 20, language: null };

/** The person's preferences: one personal record, made the first time they change one. */
@Injectable({ providedIn: 'root' })
export class Preferences {
  private readonly api = inject(Api);
  private readonly i18n = inject(I18n);
  private readonly themes = inject(Themes);
  private record: Preference | null = null;
  readonly values = signal<Omit<Preference, 'sys_id' | '_etag'>>(DEFAULTS);

  async load(): Promise<void> {
    const { data } = await this.api.get<{ records: Preference[] }>('/api/v1/sys/preference', {
      query: { limit: 1, count: false },
    });
    this.record = data.records[0] ?? null;
    this.show();
    await this.themes.load(this.values().theme);
  }

  /** Before signing in: the browser's language, the default theme. */
  async loadSignedOut(): Promise<void> {
    this.record = null;
    this.show();
    await this.themes.load(null);
  }

  async change(values: Partial<Omit<Preference, 'sys_id' | '_etag'>>): Promise<void> {
    const answer = this.record
      ? await this.api.patch<Preference>(
          `/api/v1/sys/preference/${this.record.sys_id}`,
          values,
          this.record._etag,
        )
      : await this.api.post<Preference>('/api/v1/sys/preference', values);
    const themeChanged = 'theme' in values;
    this.record = answer.data;
    this.show();
    if (themeChanged) {
      await this.themes.load(this.values().theme);
    }
  }

  private show(): void {
    const found = this.record ?? DEFAULTS;
    this.values.set({
      mode: found.mode,
      theme: found.theme,
      page_size: found.page_size,
      language: found.language,
    });
    this.themes.mode(found.mode);
    this.i18n.choose(found.language);
  }
}
