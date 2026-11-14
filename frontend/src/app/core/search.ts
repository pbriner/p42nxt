import { Injectable, inject } from '@angular/core';

import { Api } from './api';

export interface Scope {
  list: string;
  label: string;
  module: string;
  module_label: string;
  table: string;
}

export interface Hit {
  sys_id: string;
  title: string;
  subtitle: string;
}

export interface Group {
  list: string;
  label: string;
  module: string;
  module_label: string;
  table: string;
  total: number | null;
  total_exact: boolean;
  records: Hit[];
}

export interface Results {
  query: string;
  groups: Group[];
}

export const MIN_SEARCH = 2;

@Injectable({ providedIn: 'root' })
export class Search {
  private readonly api = inject(Api);
  private scopes: Promise<Scope[]> | null = null;

  lists(): Promise<Scope[]> {
    this.scopes ??= this.api
      .get<{ scopes: Scope[] }>('/api/v1/search/scopes')
      .then((a) => a.data.scopes)
      .catch(() => {
        this.scopes = null;
        return [];
      });
    return this.scopes;
  }

  forget(): void {
    this.scopes = null;
  }

  async find(q: string, scope: string | null, limit = 5, signal?: AbortSignal): Promise<Results> {
    const { data } = await this.api.get<Results>('/api/v1/search', {
      query: { q, scope, limit },
      signal,
    });
    return data;
  }
}
