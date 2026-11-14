import { Component, inject, signal } from '@angular/core';
import { MatButtonToggleModule } from '@angular/material/button-toggle';
import { MatCardModule } from '@angular/material/card';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatSelectModule } from '@angular/material/select';
import { MatSnackBar } from '@angular/material/snack-bar';

import { Api, describe } from '../core/api';
import { textIn } from '../core/format';
import { I18n } from '../core/i18n';
import { PAGE_SIZES, Preference, Preferences } from '../core/preferences';

interface ThemeChoice {
  name: string;
  label: unknown;
  description: unknown;
}

/** The person's own settings, saved as soon as they change one. */
@Component({
  selector: 'app-profile',
  imports: [MatButtonToggleModule, MatCardModule, MatFormFieldModule, MatSelectModule],
  template: `
    <div class="page">
      <header class="page-header">
        <h1>{{ i18n.t('profile.title') }}</h1>
      </header>
      <div class="cards">
        <mat-card appearance="outlined">
          <mat-card-header>
            <mat-card-title>{{ i18n.t('profile.appearance') }}</mat-card-title>
          </mat-card-header>
          <mat-card-content>
            <p class="label" id="mode-label">{{ i18n.t('profile.mode') }}</p>
            <mat-button-toggle-group
              aria-labelledby="mode-label"
              [value]="preferences.values().mode"
              (change)="save({ mode: $event.value })"
            >
              <mat-button-toggle value="system">
                <span class="material-symbols-outlined" aria-hidden="true">brightness_auto</span>
                {{ i18n.t('profile.mode_system') }}
              </mat-button-toggle>
              <mat-button-toggle value="light">
                <span class="material-symbols-outlined" aria-hidden="true">light_mode</span>
                {{ i18n.t('profile.mode_light') }}
              </mat-button-toggle>
              <mat-button-toggle value="dark">
                <span class="material-symbols-outlined" aria-hidden="true">dark_mode</span>
                {{ i18n.t('profile.mode_dark') }}
              </mat-button-toggle>
            </mat-button-toggle-group>

            <mat-form-field appearance="outline" class="field">
              <mat-label>{{ i18n.t('profile.theme') }}</mat-label>
              <mat-select
                [value]="preferences.values().theme ?? ''"
                (selectionChange)="save({ theme: $event.value || null })"
              >
                <mat-option value="">{{
                  i18n.t('profile.theme_default', { label: defaultLabel() })
                }}</mat-option>
                @for (theme of themes(); track theme.name) {
                  <mat-option [value]="theme.name">{{ text(theme.label) }}</mat-option>
                }
              </mat-select>
              @if (description(); as text) {
                <mat-hint>{{ text }}</mat-hint>
              }
            </mat-form-field>

            <mat-form-field appearance="outline" class="field">
              <mat-label>{{ i18n.t('profile.language') }}</mat-label>
              <mat-select
                [value]="preferences.values().language ?? ''"
                (selectionChange)="save({ language: $event.value || null })"
              >
                <mat-option value="">{{ i18n.t('profile.language_browser') }}</mat-option>
                <mat-option value="en">English</mat-option>
                <mat-option value="de">Deutsch</mat-option>
              </mat-select>
            </mat-form-field>
          </mat-card-content>
        </mat-card>

        <mat-card appearance="outlined">
          <mat-card-header>
            <mat-card-title>{{ i18n.t('profile.lists') }}</mat-card-title>
          </mat-card-header>
          <mat-card-content>
            <mat-form-field appearance="outline" class="field">
              <mat-label>{{ i18n.t('profile.page_size') }}</mat-label>
              <mat-select
                [value]="preferences.values().page_size"
                (selectionChange)="save({ page_size: $event.value })"
              >
                @for (size of sizes; track size) {
                  <mat-option [value]="size">{{
                    size === 5000 ? i18n.t('profile.all') : size
                  }}</mat-option>
                }
              </mat-select>
            </mat-form-field>
          </mat-card-content>
        </mat-card>
      </div>
    </div>
  `,
  styles: `
    .cards {
      display: grid;
      gap: 16px;
      grid-template-columns: 1fr;
    }
    .label {
      font: var(--mat-sys-label-large);
      margin: 8px 0;
    }
    .field {
      display: block;
      width: min(100%, 360px);
      margin-top: 20px;
    }
    mat-button-toggle-group {
      flex-wrap: wrap;
    }
    @media (min-width: 1100px) {
      .cards {
        grid-template-columns: 1fr 1fr;
        align-items: start;
      }
    }
  `,
})
export class Profile {
  protected readonly i18n = inject(I18n);
  protected readonly preferences = inject(Preferences);
  private readonly api = inject(Api);
  private readonly snack = inject(MatSnackBar);
  protected readonly sizes = PAGE_SIZES;
  protected readonly themes = signal<ThemeChoice[]>([]);
  private readonly defaultTheme = signal<string>('violet');

  constructor() {
    void this.load();
  }

  private async load(): Promise<void> {
    const [themes, setting] = await Promise.all([
      this.api.get<{ records: ThemeChoice[] }>('/api/v1/sys/theme', {
        query: { limit: 100, count: false, fields: 'name,label,description' },
      }),
      this.api.get<{ records: { value: string }[] }>('/api/v1/sys/setting', {
        query: { 'filter[name]': 'sys.theme', fields: 'value', count: false },
      }),
    ]);
    this.themes.set(themes.data.records);
    this.defaultTheme.set(setting.data.records[0]?.value ?? 'violet');
  }

  protected text(value: unknown): string {
    return textIn(value, this.i18n.language());
  }

  protected defaultLabel(): string {
    return this.text(this.themes().find((t) => t.name === this.defaultTheme())?.label);
  }

  protected description(): string | null {
    const chosen = this.preferences.values().theme ?? this.defaultTheme();
    return this.text(this.themes().find((t) => t.name === chosen)?.description) || null;
  }

  protected async save(values: Partial<Omit<Preference, 'sys_id' | '_etag'>>): Promise<void> {
    try {
      await this.preferences.change(values);
      this.snack.open(this.i18n.t('common.saved'), undefined, { duration: 2500 });
    } catch (error) {
      this.snack.open(describe(error), this.i18n.t('common.close'));
    }
  }
}
