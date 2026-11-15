import { NgTemplateOutlet } from '@angular/common';
import {
  Component,
  computed,
  effect,
  inject,
  input,
  output,
  signal,
  untracked,
} from '@angular/core';
import { MatButtonModule } from '@angular/material/button';
import { MatButtonToggleModule } from '@angular/material/button-toggle';
import { MatDialog } from '@angular/material/dialog';
import { MatIconModule } from '@angular/material/icon';
import { MatMenuModule } from '@angular/material/menu';
import { MatSnackBar } from '@angular/material/snack-bar';
import { RouterLink, RouterLinkActive } from '@angular/router';

import { describe } from '../core/api';
import { Favorite, Favorites } from '../core/favorites';
import { I18n } from '../core/i18n';
import { Installation } from '../core/installation';
import { Found, NavItem, NavModule, Navigation, fold, target } from '../core/navigation';
import { remember, remembered } from '../core/storage';
import { FolderDialog } from './folder-dialog';

const OPEN_MODULES = 'p42.nav.expanded';
const OPEN_FOLDERS = 'p42.nav.folders';

interface FavoriteRow {
  favorite: Favorite;
  found: Found;
}

/** The navigator: every module with its sections and items, or the person's favorites. */
@Component({
  selector: 'app-navigator',
  imports: [
    MatButtonModule,
    MatButtonToggleModule,
    MatIconModule,
    MatMenuModule,
    NgTemplateOutlet,
    RouterLink,
    RouterLinkActive,
  ],
  templateUrl: './navigator.html',
  styleUrl: './navigator.scss',
})
export class Navigator {
  protected readonly i18n = inject(I18n);
  protected readonly navigation = inject(Navigation);
  protected readonly installation = inject(Installation);
  protected readonly favorites = inject(Favorites);
  private readonly dialog = inject(MatDialog);
  private readonly snack = inject(MatSnackBar);
  /** The module of the page shown: it opens by itself. */
  readonly currentModule = input<string | null>(null);
  readonly navigated = output<void>();

  protected readonly tab = signal<'all' | 'favorites'>('all');
  protected readonly query = signal('');
  protected readonly openModules = signal(new Set(remembered(OPEN_MODULES)));
  protected readonly openFolders = signal(new Set(remembered(OPEN_FOLDERS)));
  protected readonly target = target;

  protected readonly modules = computed(() => {
    const q = fold(this.query());
    const all = this.navigation
      .categories()
      .flatMap((c) => c.modules.map((module) => ({ category: c.name, module })));
    if (!q) {
      return all;
    }
    return all
      .map(({ category, module }) => ({
        category,
        module: {
          ...module,
          sections: module.sections
            .map((s) => ({ ...s, items: s.items.filter((i) => this.matches(i, module, q)) }))
            .filter((s) => s.items.length),
        },
      }))
      .filter(({ module }) => module.sections.length);
  });

  protected readonly topLevel = computed(() => this.rows(null));

  constructor() {
    // Opens the module once on arriving at its page; closing it there afterwards must stay
    // closed, so what's open isn't a reason to look again.
    effect(() => {
      const current = this.currentModule();
      untracked(() => {
        if (current && !this.openModules().has(current)) {
          this.toggleModule(current);
        }
      });
    });
  }

  private matches(item: NavItem, module: NavModule, q: string): boolean {
    return (
      fold(item.label).includes(q) ||
      fold(module.name).includes(q) ||
      item.keywords.some((k) => fold(k).includes(q))
    );
  }

  /** A level of favorites, those the person can't open any more left out. */
  protected rows(parent: string | null): { folders: Favorite[]; items: FavoriteRow[] } {
    const q = fold(this.query());
    const level = this.favorites.level(parent);
    const items: FavoriteRow[] = [];
    for (const favorite of level.filter((f) => f.kind === 'item')) {
      const found = this.navigation.find(favorite.item!);
      if (found && (!q || this.matches(found.item, found.module, q))) {
        items.push({ favorite, found });
      }
    }
    const folders = level.filter(
      (f) => f.kind === 'folder' && (!q || this.rows(f.sys_id).items.length),
    );
    return { folders, items };
  }

  protected isOpen(module: string): boolean {
    return !!this.query() || this.openModules().has(module);
  }

  protected toggleModule(module: string): void {
    const open = new Set(this.openModules());
    if (!open.delete(module)) {
      open.add(module);
    }
    this.openModules.set(open);
    remember(OPEN_MODULES, open);
  }

  protected folderOpen(folder: string): boolean {
    return !!this.query() || this.openFolders().has(folder);
  }

  protected toggleFolder(folder: string, open?: boolean): void {
    const folders = new Set(this.openFolders());
    if (open ?? !folders.has(folder)) {
      folders.add(folder);
    } else {
      folders.delete(folder);
    }
    this.openFolders.set(folders);
    remember(OPEN_FOLDERS, folders);
  }

  protected folders(): Favorite[] {
    return this.favorites.level(null).filter((f) => f.kind === 'folder');
  }

  protected async run(work: () => Promise<void>): Promise<void> {
    try {
      await work();
    } catch (error) {
      this.snack.open(describe(error), this.i18n.t('common.close'));
    }
  }

  protected star(item: NavItem): Promise<void> {
    return this.run(() => this.favorites.toggle(item.name));
  }

  protected moveTo(favorite: Favorite, folder: string | null): Promise<void> {
    return this.run(async () => {
      await this.favorites.moveTo(favorite, folder);
      if (folder) {
        this.toggleFolder(folder, true);
      }
    });
  }

  protected newFolder(): void {
    this.dialog
      .open(FolderDialog, { data: { label: null } })
      .afterClosed()
      .subscribe((label?: string) => {
        if (label) {
          void this.run(() => this.favorites.addFolder(label));
        }
      });
  }

  protected rename(folder: Favorite): void {
    this.dialog
      .open(FolderDialog, { data: { label: folder.label } })
      .afterClosed()
      .subscribe((label?: string) => {
        if (label) {
          void this.run(() => this.favorites.rename(folder, label));
        }
      });
  }
}
