import { Component, computed, inject } from '@angular/core';

import { I18n } from '../core/i18n';
import { Session } from '../core/session';
import { DashboardBoard } from '../widgets/dashboard-board';

/** Home: the person's own dashboard. */
@Component({
  selector: 'app-home',
  imports: [DashboardBoard],
  template: `<app-dashboard-board name="home" [title]="title()" />`,
})
export class Home {
  private readonly i18n = inject(I18n);
  private readonly session = inject(Session);
  protected readonly title = computed(() =>
    this.i18n.t('home.hello', { name: this.session.me()?.person.name ?? '' }),
  );
}
