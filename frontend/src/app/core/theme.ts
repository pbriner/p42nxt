import { DOCUMENT, Injectable, inject, signal } from '@angular/core';

import { Api } from './api';

export interface ThemeColors {
  [token: string]: { light: string; dark: string };
}

export interface Theme {
  name: string | null;
  label?: string;
  description?: string | null;
  colors: ThemeColors;
  radius: number;
  cards: 'outlined' | 'raised';
  font: 'roboto' | 'system';
}

export type Mode = 'system' | 'light' | 'dark';

const HEX = /^#[0-9a-fA-F]{6}$/;
const TOKEN = /^[a-z][a-z0-9-]{0,40}$/;
const FRAME = new Set([
  'page-background',
  'card',
  'toolbar',
  'nav-background',
  'nav-background-end',
  'on-nav',
  'on-nav-variant',
  'nav-accent',
  'nav-active',
  'on-nav-active',
  'message-information',
  'on-message-information',
  'message-warning',
  'on-message-warning',
  'message-critical',
  'on-message-critical',
]);
const FONTS = {
  roboto: 'Roboto, system-ui, sans-serif',
  system: 'system-ui, -apple-system, "Segoe UI", Roboto, sans-serif',
};

function variable(token: string): string {
  if (FRAME.has(token)) {
    return `--app-${token}`;
  }
  return token.startsWith('chart-') ? `--${token}` : `--mat-sys-${token}`;
}

/** Puts a theme's colours and look on the page; the built-in ones stay if it can't load. */
@Injectable({ providedIn: 'root' })
export class Themes {
  private readonly api = inject(Api);
  private readonly root = inject(DOCUMENT).documentElement;
  private applied: string[] = [];
  readonly current = signal<Theme | null>(null);

  async load(name: string | null): Promise<void> {
    try {
      const { data } = await this.api.get<Theme>('/api/v1/theme', {
        query: { name },
        quiet: true,
      });
      this.apply(data);
    } catch {
      // Keep whatever colours are on the page.
    }
  }

  apply(theme: Theme): void {
    for (const name of this.applied) {
      this.root.style.removeProperty(name);
    }
    this.applied = [];
    for (const [token, pair] of Object.entries(theme.colors ?? {})) {
      // Only plain colours on plain names: nothing else reaches the page's styles.
      if (!TOKEN.test(token) || !HEX.test(pair.light) || !HEX.test(pair.dark)) {
        continue;
      }
      const name = variable(token);
      this.root.style.setProperty(name, `light-dark(${pair.light}, ${pair.dark})`);
      this.applied.push(name);
    }
    const radius = Math.max(0, Math.min(24, Math.round(Number(theme.radius) || 0)));
    this.root.style.setProperty('--app-radius', `${radius}px`);
    this.root.style.setProperty('--app-font', FONTS[theme.font] ?? FONTS.roboto);
    this.applied.push('--app-radius', '--app-font');
    this.root.dataset['cards'] = theme.cards === 'raised' ? 'raised' : 'outlined';
    this.current.set(theme);
  }

  mode(mode: Mode): void {
    this.root.classList.toggle('theme-light', mode === 'light');
    this.root.classList.toggle('theme-dark', mode === 'dark');
  }
}
