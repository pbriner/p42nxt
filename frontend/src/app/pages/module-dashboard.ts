import { Component, computed, inject, input } from '@angular/core';

import { Navigation } from '../core/navigation';
import { DashboardBoard } from '../widgets/dashboard-board';

/** A module's dashboard, named after the module. */
@Component({
  selector: 'app-module-dashboard',
  imports: [DashboardBoard],
  template: `<app-dashboard-board [name]="module()" [title]="title()" />`,
})
export class ModuleDashboard {
  private readonly navigation = inject(Navigation);
  readonly module = input.required<string>();
  protected readonly title = computed(
    () =>
      this.navigation
        .categories()
        .flatMap((c) => c.modules)
        .find((m) => m.id === this.module())?.name ?? this.module(),
  );
}
