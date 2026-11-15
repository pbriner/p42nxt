import { Injectable, inject, signal } from '@angular/core';

import { Api } from './api';

export interface Language {
  code: string;
  name: string;
  offered: boolean;
}

const BUILT_IN: Language[] = [
  { code: 'en', name: 'English', offered: true },
  { code: 'de', name: 'Deutsch', offered: true },
];

/** The installation's languages, asked for once; the built-in two until they've come. */
@Injectable({ providedIn: 'root' })
export class Languages {
  private readonly api = inject(Api);
  private asked = false;
  readonly all = signal<Language[]>(BUILT_IN);

  load(): void {
    if (this.asked) {
      return;
    }
    this.asked = true;
    void this.api
      .get<{ languages: Language[] }>('/api/v1/languages', { quiet: true, background: true })
      .then(({ data }) => this.all.set(data.languages.length ? data.languages : BUILT_IN))
      .catch(() => undefined);
  }
}
