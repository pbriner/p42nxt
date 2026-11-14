import { Component, computed, inject, input } from '@angular/core';
import { MatButtonModule } from '@angular/material/button';
import { RouterLink } from '@angular/router';

import { I18n } from '../core/i18n';

/** A whole page saying what's wrong: no access, nothing there, or an error. */
@Component({
  selector: 'app-problem-state',
  imports: [MatButtonModule, RouterLink],
  template: `
    <section class="problem">
      <span class="material-symbols-outlined icon" aria-hidden="true">{{ icon() }}</span>
      <h1>{{ title() }}</h1>
      <p class="muted">{{ text() }}</p>
      <a mat-flat-button routerLink="/">{{ i18n.t('problem.home') }}</a>
    </section>
  `,
  styles: `
    .problem {
      display: flex;
      flex-direction: column;
      align-items: center;
      text-align: center;
      padding: 64px 16px;
    }
    .icon {
      font-size: 48px;
      width: 48px;
      height: 48px;
      color: var(--mat-sys-on-surface-variant);
    }
    h1 {
      font: var(--mat-sys-title-large);
    }
    p {
      max-width: 480px;
    }
  `,
})
export class ProblemState {
  protected readonly i18n = inject(I18n);
  readonly status = input<number>(404);
  readonly detail = input<string | null>(null);
  protected readonly icon = computed(() =>
    this.status() === 403 ? 'lock' : this.status() === 404 ? 'search_off' : 'error',
  );
  protected readonly title = computed(() =>
    this.i18n.t(
      this.status() === 403
        ? 'problem.no_access'
        : this.status() === 404
          ? 'problem.not_found'
          : 'problem.error',
    ),
  );
  protected readonly text = computed(() =>
    this.status() === 403
      ? this.i18n.t('problem.no_access_text')
      : this.status() === 404
        ? this.i18n.t('problem.not_found_text')
        : (this.detail() ?? ''),
  );
}

/** The page for addresses that lead nowhere. */
@Component({
  selector: 'app-not-found',
  imports: [ProblemState],
  template: `<app-problem-state [status]="404" />`,
})
export class NotFound {}
