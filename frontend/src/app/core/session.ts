import { Injectable, inject, signal } from '@angular/core';

import { Api, Problem } from './api';

export interface Person {
  sys_id: string;
  name: string;
  email?: string | null;
  kind?: string;
}

export interface Me {
  person: Person;
  groups: string[];
  channel: string;
  verified_at: string | null;
}

/** Who is signed in: unknown until asked, then the person or null. */
@Injectable({ providedIn: 'root' })
export class Session {
  private readonly api = inject(Api);
  readonly me = signal<Me | null | undefined>(undefined);
  private loading: Promise<Me | null> | null = null;

  /** The signed-in person, asking the API once. */
  ensure(): Promise<Me | null> {
    if (this.me() !== undefined) {
      return Promise.resolve(this.me() ?? null);
    }
    this.loading ??= this.load();
    return this.loading;
  }

  async load(): Promise<Me | null> {
    try {
      const { data } = await this.api.get<Me>('/api/v1/me', { quiet: true });
      this.me.set(data);
      return data;
    } catch (error) {
      if (error instanceof Problem && error.status === 401) {
        this.me.set(null);
        return null;
      }
      throw error;
    } finally {
      this.loading = null;
    }
  }

  async signOut(): Promise<void> {
    try {
      await this.api.post('/api/v1/sign-in/out', {}, { quiet: true });
    } finally {
      this.me.set(null);
    }
  }

  forget(): void {
    this.me.set(undefined);
  }
}

export function initials(name: string): string {
  return name
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((w) => w[0]!.toUpperCase())
    .join('');
}
