import { DOCUMENT, Injectable, computed, inject, signal } from '@angular/core';

import { DE } from '../i18n/de';
import { EN, TextKey } from '../i18n/en';
import { Api } from './api';

export const LANGUAGES = ['en', 'de'] as const;
export type Language = (typeof LANGUAGES)[number];
const TEXTS: Record<Language, Record<TextKey, string>> = { en: EN, de: DE };

function browserLanguage(): Language {
  const asked = (navigator.languages ?? [navigator.language]).map((l) => l.slice(0, 2));
  return (asked.find((l) => (LANGUAGES as readonly string[]).includes(l)) as Language) ?? 'en';
}

/** The app's texts in the person's language: their preference, else the browser's. */
@Injectable({ providedIn: 'root' })
export class I18n {
  private readonly api = inject(Api);
  private readonly document = inject(DOCUMENT);
  private readonly chosen = signal<Language | null>(null);
  readonly language = computed<Language>(() => this.chosen() ?? browserLanguage());

  constructor() {
    this.apply();
  }

  /** null: follow the browser. */
  choose(language: string | null): void {
    this.chosen.set(
      (LANGUAGES as readonly string[]).includes(language ?? '') ? (language as Language) : null,
    );
    this.apply();
  }

  private apply(): void {
    this.api.language.set(this.language());
    this.document.documentElement.lang = this.language();
  }

  t(key: TextKey, params: Record<string, string | number> = {}): string {
    const text = TEXTS[this.language()][key] ?? EN[key];
    return text.replace(/\{(\w+)\}/g, (_, name: string) => String(params[name] ?? `{${name}}`));
  }
}
