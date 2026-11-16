import { Injectable, inject, signal } from '@angular/core';
import { Router } from '@angular/router';

/** What the API says went wrong (RFC 9457 problem details). */
export interface FieldProblem {
  field: string;
  code: string;
  message: string;
}

export class Problem extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    readonly title: string,
    readonly detail: string | null = null,
    readonly errors: FieldProblem[] = [],
    readonly body: Record<string, unknown> = {},
  ) {
    super(detail ?? title);
  }
}

export interface Answer<T> {
  data: T;
  etag: string | null;
  status: number;
}

type Query = Record<string, string | number | boolean | null | undefined>;

interface Options {
  query?: Query;
  body?: unknown;
  ifMatch?: string;
  /** A 401 means "not signed in" and is the caller's to handle: no redirect. */
  quiet?: boolean;
  signal?: AbortSignal;
  /** Made by the app itself (polling): doesn't count as the person being there. */
  background?: boolean;
  /** A file sent as it is (the body), not as JSON. */
  raw?: Blob;
}

const CSRF_COOKIE = 'p42_csrf';
const CHANGES = new Set(['POST', 'PATCH', 'PUT', 'DELETE']);

function cookie(name: string): string | null {
  const found = document.cookie.split('; ').find((c) => c.startsWith(`${name}=`));
  return found ? decodeURIComponent(found.slice(name.length + 1)) : null;
}

/** The public HTTP API, the only thing the app talks to. */
@Injectable({ providedIn: 'root' })
export class Api {
  private readonly router = inject(Router);
  /** The language the API answers in (labels, messages). */
  readonly language = signal('en');
  /** Asks the person for their second factor again (the shell's dialog); true once given. */
  verifyAgain: (() => Promise<boolean>) | null = null;

  get<T>(path: string, options: Options = {}): Promise<Answer<T>> {
    return this.request<T>('GET', path, options);
  }

  post<T>(path: string, body: unknown = {}, options: Options = {}): Promise<Answer<T>> {
    return this.request<T>('POST', path, { ...options, body });
  }

  patch<T>(
    path: string,
    body: unknown,
    ifMatch: string,
    options: Options = {},
  ): Promise<Answer<T>> {
    return this.request<T>('PATCH', path, { ...options, body, ifMatch });
  }

  delete<T>(path: string, ifMatch: string, options: Options = {}): Promise<Answer<T>> {
    return this.request<T>('DELETE', path, { ...options, ifMatch });
  }

  async request<T>(method: string, path: string, options: Options = {}): Promise<Answer<T>> {
    try {
      return await this.send<T>(method, path, options);
    } catch (error) {
      // A change that needs a recent second factor: asked for, then tried once more.
      if (
        error instanceof Problem &&
        error.code === 'verify_again' &&
        !options.quiet &&
        this.verifyAgain &&
        (await this.verifyAgain())
      ) {
        return this.send<T>(method, path, options);
      }
      throw error;
    }
  }

  private async send<T>(method: string, path: string, options: Options): Promise<Answer<T>> {
    const url = new URL(path, window.location.origin);
    for (const [key, value] of Object.entries(options.query ?? {})) {
      if (value !== null && value !== undefined && value !== '') {
        url.searchParams.set(key, String(value));
      }
    }
    const headers: Record<string, string> = {
      Accept: 'application/json',
      'Accept-Language': this.language(),
    };
    if (options.raw !== undefined) {
      headers['Content-Type'] = options.raw.type || 'application/octet-stream';
    } else if (options.body !== undefined) {
      headers['Content-Type'] = 'application/json';
    }
    if (options.ifMatch) {
      headers['If-Match'] = options.ifMatch;
    }
    if (options.background) {
      headers['X-P42-Background'] = '1';
    }
    const csrf = cookie(CSRF_COOKIE);
    if (CHANGES.has(method) && csrf) {
      headers['X-CSRF-Token'] = csrf;
    }
    const response = await fetch(url, {
      method,
      headers,
      body: options.raw ?? (options.body === undefined ? undefined : JSON.stringify(options.body)),
      credentials: 'same-origin',
      signal: options.signal,
    });
    const text = await response.text();
    const data = text ? JSON.parse(text) : null;
    if (!response.ok) {
      const problem = new Problem(
        response.status,
        data?.code ?? 'error',
        data?.title ?? response.statusText,
        data?.detail ?? null,
        data?.errors ?? [],
        data ?? {},
      );
      if (response.status === 401 && !options.quiet) {
        const next = this.router.url;
        void this.router.navigate(['/sign-in'], {
          queryParams: next && next !== '/' && !next.startsWith('/sign-in') ? { next } : {},
        });
      }
      throw problem;
    }
    return { data: data as T, etag: response.headers.get('ETag'), status: response.status };
  }
}

/** A problem as one sentence for people: per field "Label: message", else its detail or title. */
export function describe(error: unknown, label: (field: string) => string = humanize): string {
  if (error instanceof Problem) {
    if (error.errors.length) {
      return error.errors.map((e) => `${label(e.field)}: ${e.message}`).join(', ');
    }
    return error.detail ?? error.title;
  }
  return error instanceof Error ? error.message : String(error);
}

export function humanize(name: string): string {
  const words = name.split('.').pop()!.replace(/_/g, ' ').trim();
  return words.charAt(0).toUpperCase() + words.slice(1);
}
