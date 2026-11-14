import { Component, computed, inject } from '@angular/core';
import { MatCardModule } from '@angular/material/card';
import { RouterLink } from '@angular/router';

import { I18n } from '../core/i18n';
import { Navigation, target } from '../core/navigation';
import { Session } from '../core/session';

/** Home: a greeting and the modules the person may open. */
@Component({
  selector: 'app-home',
  imports: [MatCardModule, RouterLink],
  template: `
    <div class="page">
      <header class="page-header">
        <h1>{{ i18n.t('home.hello', { name: name() }) }}</h1>
      </header>
      @if (navigation.loaded() && !modules().length) {
        <p class="muted">{{ i18n.t('home.nothing') }}</p>
      }
      @if (modules().length) {
        <h2 class="heading">{{ i18n.t('home.modules') }}</h2>
        <div class="grid">
          @for (module of modules(); track module.id) {
            <mat-card appearance="outlined">
              <mat-card-header>
                <span mat-card-avatar class="material-symbols-outlined icon" aria-hidden="true">{{
                  module.icon ?? 'extension'
                }}</span>
                <mat-card-title>{{ module.name }}</mat-card-title>
              </mat-card-header>
              <mat-card-content>
                <ul>
                  @for (item of module.items; track item.name) {
                    <li>
                      <a [routerLink]="target(item).commands" [queryParams]="target(item).query">{{
                        item.label
                      }}</a>
                    </li>
                  }
                </ul>
              </mat-card-content>
            </mat-card>
          }
        </div>
      }
    </div>
  `,
  styles: `
    .heading {
      font: var(--mat-sys-title-medium);
      margin: 8px 0 12px;
    }
    .grid {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(min(100%, 260px), 1fr));
      gap: 16px;
    }
    .icon {
      color: var(--mat-sys-primary);
      font-size: 32px;
    }
    ul {
      margin: 8px 0 0;
      padding-left: 20px;
    }
    li {
      margin: 4px 0;
    }
  `,
})
export class Home {
  protected readonly i18n = inject(I18n);
  protected readonly navigation = inject(Navigation);
  private readonly session = inject(Session);
  protected readonly target = target;
  protected readonly name = computed(() => this.session.me()?.person.name ?? '');
  protected readonly modules = computed(() =>
    this.navigation
      .categories()
      .flatMap((c) => c.modules.map((m) => ({ ...m, items: m.sections.flatMap((s) => s.items) }))),
  );
}
