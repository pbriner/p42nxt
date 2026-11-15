import { BreakpointObserver } from '@angular/cdk/layout';
import { Component, HostListener, computed, inject, signal, viewChild } from '@angular/core';
import { toSignal } from '@angular/core/rxjs-interop';
import { MatButtonModule } from '@angular/material/button';
import { MatDialog } from '@angular/material/dialog';
import { MatSidenavModule } from '@angular/material/sidenav';
import { MatTooltipModule } from '@angular/material/tooltip';
import { NavigationEnd, Router, RouterLink, RouterOutlet } from '@angular/router';
import { filter, firstValueFrom, map } from 'rxjs';

import { Api } from '../core/api';
import { I18n } from '../core/i18n';
import { Installation } from '../core/installation';
import { Navigation, target } from '../core/navigation';
import { Session, initials } from '../core/session';
import { Announcements } from './announcements';
import { Bell } from './bell';
import { GlobalSearch } from './global-search';
import { Navigator } from './navigator';
import { VerifyDialog } from './verify-dialog';

const WIDE = '(min-width: 960px)';
const COMPACT_NAME = '(max-width: 599px)';

/** The frame around every page: top bar, navigator, content. */
@Component({
  selector: 'app-shell',
  imports: [
    Announcements,
    Bell,
    GlobalSearch,
    MatButtonModule,
    MatSidenavModule,
    MatTooltipModule,
    Navigator,
    RouterLink,
    RouterOutlet,
  ],
  templateUrl: './shell.html',
  styleUrl: './shell.scss',
})
export class Shell {
  protected readonly i18n = inject(I18n);
  protected readonly session = inject(Session);
  protected readonly installation = inject(Installation);
  private readonly navigation = inject(Navigation);
  private readonly router = inject(Router);
  private readonly api = inject(Api);
  private readonly dialog = inject(MatDialog);
  private readonly search = viewChild(GlobalSearch);

  protected readonly wide = toSignal(
    inject(BreakpointObserver)
      .observe(WIDE)
      .pipe(map((s) => s.matches)),
    { initialValue: window.matchMedia(WIDE).matches },
  );
  protected readonly nameHidden = toSignal(
    inject(BreakpointObserver)
      .observe(COMPACT_NAME)
      .pipe(map((s) => s.matches)),
    { initialValue: window.matchMedia(COMPACT_NAME).matches },
  );
  protected readonly navOpen = signal(window.matchMedia(WIDE).matches);
  protected readonly stage = signal<string | null>(null);
  private readonly url = toSignal(
    this.router.events.pipe(
      filter((e) => e instanceof NavigationEnd),
      map(() => this.router.url),
    ),
    { initialValue: this.router.url },
  );

  protected readonly person = computed(() => this.session.me()?.person ?? null);
  protected readonly initials = computed(() => initials(this.person()?.name ?? ''));
  /** The list on the page, from /list/<view>. */
  protected readonly pageList = computed(() => {
    const found = /^\/list\/([^/?#]+)/.exec(this.url());
    return found ? decodeURIComponent(found[1]!) : null;
  });
  /** The module whose item leads to the page shown. */
  protected readonly currentModule = computed(() => {
    const path = this.url().split(/[?#]/)[0];
    const found = this.navigation
      .items()
      .find((f) => this.router.createUrlTree(target(f.item).commands).toString() === path);
    return found?.module.id ?? null;
  });

  constructor() {
    void this.loadStage();
    // One dialog for all the changes waiting for it at once.
    let asking: Promise<boolean> | null = null;
    this.api.verifyAgain = () =>
      (asking ??= firstValueFrom(
        this.dialog.open<VerifyDialog, void, boolean>(VerifyDialog).afterClosed(),
      ).then((given) => {
        asking = null;
        return !!given;
      }));
  }

  private async loadStage(): Promise<void> {
    try {
      const { data } = await this.api.get<{ records: { value: string }[] }>('/api/v1/sys/setting', {
        query: { 'filter[name]': 'sys.stage', fields: 'value', count: false },
      });
      const stage = data.records[0]?.value ?? 'production';
      this.stage.set(stage === 'production' ? null : stage);
    } catch {
      this.stage.set(null);
    }
  }

  protected stageLabel(): string {
    const stage = this.stage();
    return stage === 'qa' || stage === 'test' || stage === 'development'
      ? this.i18n.t(`stage.${stage}`)
      : '';
  }

  protected toggle(): void {
    this.navOpen.update((open) => !open);
  }

  protected navigated(): void {
    if (!this.wide()) {
      this.navOpen.set(false);
    }
  }

  protected async signOut(): Promise<void> {
    await this.session.signOut();
    await this.router.navigate(['/sign-in']);
  }

  @HostListener('document:keydown', ['$event'])
  protected shortcut(event: KeyboardEvent): void {
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k') {
      event.preventDefault();
      const search = this.search();
      if (this.wide() && search) {
        search.focus();
      } else {
        void this.router.navigate(['/search']);
      }
    }
  }

  protected skip(event: Event): void {
    event.preventDefault();
    document.getElementById('content')?.focus();
  }
}
