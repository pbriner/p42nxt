import { Component, inject, input, signal } from '@angular/core';
import { MatButtonModule } from '@angular/material/button';
import { MatCardModule } from '@angular/material/card';

import { Api, describe } from '../core/api';
import { I18n } from '../core/i18n';

interface Exported {
  texts: { state: string }[];
}

/** A language's translations of what modules define: download them to fill in, and load a
 * filled-in file back (all or nothing). */
@Component({
  selector: 'app-language-exchange',
  imports: [MatButtonModule, MatCardModule],
  template: `
    <mat-card appearance="outlined" class="section">
      <mat-card-header>
        <mat-card-title>{{ i18n.t('languages.title') }}</mat-card-title>
      </mat-card-header>
      <mat-card-content>
        @if (code() === 'en') {
          <p class="muted">{{ i18n.t('languages.english') }}</p>
        } @else {
          <p class="muted">{{ i18n.t('languages.explain') }}</p>
          @if (coverage(); as c) {
            <p>{{ i18n.t('languages.coverage', c) }}</p>
          }
          <div class="row">
            <button mat-stroked-button type="button" [disabled]="busy()" (click)="download()">
              {{ i18n.t('languages.export') }}
            </button>
            <button mat-stroked-button type="button" [disabled]="busy()" (click)="file.click()">
              {{ i18n.t('languages.import') }}
            </button>
            <input
              #file
              type="file"
              accept="application/json,.json"
              hidden
              (change)="upload($any($event.target))"
            />
          </div>
        }
        @if (said(); as text) {
          <p role="status">{{ text }}</p>
        }
      </mat-card-content>
    </mat-card>
  `,
  styles: `
    .row {
      display: flex;
      gap: 12px;
      flex-wrap: wrap;
    }
  `,
})
export class LanguageExchange {
  protected readonly i18n = inject(I18n);
  private readonly api = inject(Api);
  readonly code = input.required<string>();
  protected readonly busy = signal(false);
  protected readonly said = signal<string | null>(null);
  protected readonly coverage = signal<{ done: number; all: number } | null>(null);

  private path(what: 'export' | 'import'): string {
    return `/api/v1/languages/${encodeURIComponent(this.code())}/${what}`;
  }

  protected async download(): Promise<void> {
    await this.run(async () => {
      const { data } = await this.api.get<Exported>(this.path('export'));
      const done = data.texts.filter((t) => t.state === 'done').length;
      this.coverage.set({ done, all: data.texts.length });
      const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
      const link = document.createElement('a');
      link.href = URL.createObjectURL(blob);
      link.download = `translations-${this.code()}.json`;
      link.click();
      URL.revokeObjectURL(link.href);
    });
  }

  protected async upload(input: HTMLInputElement): Promise<void> {
    const file = input.files?.[0];
    input.value = '';
    if (!file) {
      return;
    }
    await this.run(async () => {
      let given: unknown;
      try {
        given = JSON.parse(await file.text());
      } catch {
        this.said.set(this.i18n.t('languages.not_json'));
        return;
      }
      const { data } = await this.api.post<Record<string, number>>(this.path('import'), given);
      this.said.set(this.i18n.t('languages.imported', data));
    });
  }

  private async run(work: () => Promise<void>): Promise<void> {
    this.busy.set(true);
    this.said.set(null);
    try {
      await work();
    } catch (error) {
      this.said.set(describe(error));
    } finally {
      this.busy.set(false);
    }
  }
}
