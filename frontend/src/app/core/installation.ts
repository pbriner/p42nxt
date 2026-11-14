import { Injectable, inject, signal } from '@angular/core';
import { Title } from '@angular/platform-browser';

import { Api } from './api';

export interface InstallationInfo {
  name: string;
  language: string;
  setup_open: boolean;
}

/** The installation itself: its name (shown everywhere), its language, whether it's set up. */
@Injectable({ providedIn: 'root' })
export class Installation {
  private readonly api = inject(Api);
  private readonly title = inject(Title);
  readonly name = signal('P42cloud');
  readonly language = signal('en');
  readonly setupOpen = signal(false);

  async load(): Promise<void> {
    try {
      const { data } = await this.api.get<InstallationInfo>('/api/v1/installation', {
        quiet: true,
      });
      this.name.set(data.name);
      this.language.set(data.language);
      this.setupOpen.set(data.setup_open);
      this.title.setTitle(data.name);
    } catch {
      // The built-in name stays.
    }
  }
}
