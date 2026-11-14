import { Injectable, computed, inject, signal } from '@angular/core';

import { Api } from './api';

export interface Favorite {
  sys_id: string;
  _etag: string;
  kind: 'item' | 'folder';
  item: string | null;
  label: string | null;
  parent: string | null;
  position: number;
}

const PATH = '/api/v1/sys/favorite';

/** The person's favorites and folders (personal records), in their order. */
@Injectable({ providedIn: 'root' })
export class Favorites {
  private readonly api = inject(Api);
  readonly all = signal<Favorite[]>([]);
  readonly marked = computed(
    () =>
      new Set(
        this.all()
          .filter((f) => f.kind === 'item')
          .map((f) => f.item!),
      ),
  );

  async load(): Promise<void> {
    const { data } = await this.api.get<{ records: Favorite[] }>(PATH, {
      query: { limit: 500, count: false, sort: 'position,created' },
    });
    this.all.set(data.records);
  }

  /** One level, in order: the top level (null) or a folder's favorites. */
  level(parent: string | null): Favorite[] {
    return this.all()
      .filter((f) => (f.parent ?? null) === parent)
      .sort((a, b) => a.position - b.position);
  }

  private next(parent: string | null): number {
    return Math.max(-1, ...this.level(parent).map((f) => f.position)) + 1;
  }

  async toggle(item: string): Promise<void> {
    const found = this.all().find((f) => f.kind === 'item' && f.item === item);
    if (found) {
      await this.remove(found);
    } else {
      await this.api.post(PATH, { item, position: this.next(null) });
      await this.load();
    }
  }

  async addFolder(label: string): Promise<void> {
    await this.api.post(PATH, { kind: 'folder', label, position: this.next(null) });
    await this.load();
  }

  async rename(folder: Favorite, label: string): Promise<void> {
    await this.api.patch(`${PATH}/${folder.sys_id}`, { label }, folder._etag);
    await this.load();
  }

  async remove(favorite: Favorite): Promise<void> {
    await this.api.delete(`${PATH}/${favorite.sys_id}`, favorite._etag);
    await this.load();
  }

  async moveTo(favorite: Favorite, folder: string | null): Promise<void> {
    await this.api.patch(
      `${PATH}/${favorite.sys_id}`,
      { parent: folder, position: this.next(folder) },
      favorite._etag,
    );
    await this.load();
  }

  /** Swaps a favorite with its neighbour on the same level. */
  async move(favorite: Favorite, by: -1 | 1): Promise<void> {
    const level = this.level(favorite.parent ?? null);
    const at = level.findIndex((f) => f.sys_id === favorite.sys_id);
    const other = level[at + by];
    if (!other) {
      return;
    }
    const order = [...level];
    [order[at], order[at + by]] = [order[at + by]!, order[at]!];
    for (const [position, f] of order.entries()) {
      if (f.position !== position) {
        await this.api.patch(`${PATH}/${f.sys_id}`, { position }, f._etag);
      }
    }
    await this.load();
  }
}
