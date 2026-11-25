import { Clipboard } from '@angular/cdk/clipboard';
import { Component, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { MatButtonModule } from '@angular/material/button';
import { MatCardModule } from '@angular/material/card';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatInputModule } from '@angular/material/input';
import { MatSelectModule } from '@angular/material/select';
import { MatSnackBar } from '@angular/material/snack-bar';

import { Api, describe } from '../core/api';
import { I18n } from '../core/i18n';

interface Token {
  sys_id: string;
  _etag: string;
  name: string;
  prefix: string;
  expires: string;
  last_used: string | null;
  addresses: string[] | null;
}

interface Session {
  sys_id: string;
  _etag: string;
  agent: string | null;
  address: string | null;
  last_seen: string;
}

const TOKENS = '/api/v1/sys/api_token';
const SESSIONS = '/api/v1/sys/session';
/** The core's administrators group: its ID never changes. */
const ADMINISTRATORS = '00000000-0000-7000-8000-000000000101';

/** The person's own API tokens (made here, shown once) and where they're signed in. */
@Component({
  selector: 'app-profile-access',
  imports: [
    FormsModule,
    MatButtonModule,
    MatCardModule,
    MatFormFieldModule,
    MatInputModule,
    MatSelectModule,
  ],
  template: `
    <mat-card appearance="outlined">
      <mat-card-header>
        <mat-card-title>{{ i18n.t('own.tokens') }}</mat-card-title>
      </mat-card-header>
      <mat-card-content>
        <p class="muted">{{ i18n.t('own.tokens_hint') }}</p>
        @if (made(); as token) {
          <div class="made" role="status">
            <p>{{ i18n.t('own.made') }}</p>
            <code class="token">{{ token }}</code>
            <div class="row">
              <button mat-flat-button type="button" (click)="copy(token)">
                {{ i18n.t('own.copy') }}
              </button>
              <button mat-button type="button" (click)="made.set(null)">
                {{ i18n.t('own.done') }}
              </button>
            </div>
          </div>
        }
        <ul class="items">
          @for (t of tokens(); track t.sys_id) {
            <li>
              <div class="what">
                <strong>{{ t.name }}</strong>
                <span class="muted"
                  >{{ t.prefix }}… · {{ i18n.t('own.expires', { date: day(t.expires) }) }} ·
                  {{
                    t.last_used
                      ? i18n.t('own.last_used', { date: moment(t.last_used) })
                      : i18n.t('own.never_used')
                  }}
                  @if (t.addresses?.length) {
                    · {{ t.addresses!.join(', ') }}
                  }
                </span>
              </div>
              <button mat-button type="button" class="danger" (click)="revoke(t)">
                {{ i18n.t('own.revoke') }}
              </button>
            </li>
          } @empty {
            <li class="muted">{{ i18n.t('own.no_tokens') }}</li>
          }
        </ul>
        <form class="new" (ngSubmit)="make()">
          <mat-form-field appearance="outline">
            <mat-label>{{ i18n.t('own.name') }}</mat-label>
            <input matInput name="tokenName" maxlength="80" [(ngModel)]="name" required />
          </mat-form-field>
          <mat-form-field appearance="outline">
            <mat-label>{{ i18n.t('own.lasts') }}</mat-label>
            <mat-select name="days" [(ngModel)]="days">
              @for (d of [7, 30, 90, 365]; track d) {
                <mat-option [value]="d">{{ i18n.t('own.days', { n: d }) }}</mat-option>
              }
            </mat-select>
          </mat-form-field>
          @if (robots().length) {
            <mat-form-field appearance="outline">
              <mat-label>{{ i18n.t('own.acts_as') }}</mat-label>
              <mat-select name="actsAs" [(ngModel)]="actsAs">
                <mat-option [value]="null">{{ i18n.t('own.me') }}</mat-option>
                @for (r of robots(); track r.sys_id) {
                  <mat-option [value]="r.sys_id">{{ r.name }}</mat-option>
                }
              </mat-select>
            </mat-form-field>
          }
          <mat-form-field appearance="outline" class="wide">
            <mat-label>{{ i18n.t('own.addresses') }}</mat-label>
            <input matInput name="addresses" [(ngModel)]="addresses" />
            <mat-hint>{{ i18n.t('own.addresses_hint') }}</mat-hint>
          </mat-form-field>
          <button mat-stroked-button type="submit" [disabled]="!name.trim() || busy()">
            {{ i18n.t('own.make') }}
          </button>
        </form>
      </mat-card-content>
    </mat-card>

    <mat-card appearance="outlined">
      <mat-card-header>
        <mat-card-title>{{ i18n.t('own.sessions') }}</mat-card-title>
      </mat-card-header>
      <mat-card-content>
        <ul class="items">
          @for (s of sessions(); track s.sys_id) {
            <li>
              <div class="what">
                <strong>{{ browser(s.agent) }}</strong>
                <span class="muted"
                  >{{ s.address ?? '' }} ·
                  {{ i18n.t('own.last_used', { date: moment(s.last_seen) }) }}</span
                >
              </div>
              @if (s.sys_id === mine()) {
                <span class="this">{{ i18n.t('own.this_one') }}</span>
              } @else {
                <button mat-button type="button" class="danger" (click)="end(s)">
                  {{ i18n.t('own.end') }}
                </button>
              }
            </li>
          }
        </ul>
        @if (sessions().length > 1) {
          <button mat-stroked-button type="button" (click)="endOthers()">
            {{ i18n.t('own.end_others') }}
          </button>
        }
      </mat-card-content>
    </mat-card>
  `,
  styles: `
    :host {
      display: contents;
    }
    .items {
      list-style: none;
      margin: 8px 0 16px;
      padding: 0;
    }
    .items li {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 8px;
      padding: 8px 0;
      border-bottom: 1px solid var(--mat-sys-outline-variant);
    }
    .what {
      display: flex;
      flex-direction: column;
      min-width: 0;
      overflow-wrap: anywhere;
    }
    .new {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      align-items: center;
    }
    .new mat-form-field {
      flex: 1 1 140px;
    }
    .new .wide {
      flex-basis: 100%;
    }
    .made {
      padding: 12px;
      margin-bottom: 12px;
      border-radius: var(--app-radius-small);
      background: var(--mat-sys-surface-container-high);
    }
    .token {
      display: block;
      overflow-wrap: anywhere;
      margin: 8px 0;
      font-size: 0.9em;
    }
    .row {
      display: flex;
      gap: 8px;
    }
    .danger {
      color: var(--mat-sys-error);
    }
    .this {
      color: var(--mat-sys-primary);
      font: var(--mat-sys-label-large);
    }
  `,
})
export class ProfileAccess {
  protected readonly i18n = inject(I18n);
  private readonly api = inject(Api);
  private readonly snack = inject(MatSnackBar);
  private readonly clipboard = inject(Clipboard);
  protected readonly tokens = signal<Token[]>([]);
  protected readonly sessions = signal<Session[]>([]);
  protected readonly mine = signal<string | null>(null);
  protected readonly made = signal<string | null>(null);
  protected readonly busy = signal(false);
  /** Service accounts an administrator may make tokens for; empty for everyone else. */
  protected readonly robots = signal<{ sys_id: string; name: string }[]>([]);
  protected actsAs: string | null = null;
  private person = '';
  private groups: string[] = [];
  protected name = '';
  protected days = 90;
  protected addresses = '';

  constructor() {
    void this.load();
  }

  private async load(): Promise<void> {
    const { data: me } = await this.api.get<{
      person: { sys_id: string };
      session: string | null;
      groups: string[];
    }>('/api/v1/me');
    this.person = me.person.sys_id;
    this.groups = me.groups;
    this.mine.set(me.session);
    const [tokens, sessions] = await Promise.all([
      this.api.get<{ records: Token[] }>(TOKENS, {
        query: { 'filter[person]': this.person, sort: '-expires', limit: 100, count: false },
      }),
      this.api.get<{ records: Session[] }>(SESSIONS, {
        query: {
          'filter[person]': this.person,
          'filter[stage]': 'active',
          sort: '-last_seen',
          limit: 100,
          count: false,
        },
      }),
    ]);
    this.tokens.set(tokens.data.records);
    this.sessions.set(sessions.data.records);
    if (this.groups.includes(ADMINISTRATORS)) {
      const { data } = await this.api.get<{ records: { sys_id: string; name: string }[] }>(
        '/api/v1/sys/person',
        { query: { 'filter[kind]': 'service', fields: 'name', sort: 'name', limit: 100 } },
      );
      this.robots.set(data.records);
    }
  }

  protected day(value: string): string {
    return new Date(value).toLocaleDateString(this.i18n.language(), { dateStyle: 'medium' });
  }

  protected moment(value: string): string {
    return new Date(value).toLocaleString(this.i18n.language(), {
      dateStyle: 'medium',
      timeStyle: 'short',
    });
  }

  /** A browser's name in a few words, from what it says about itself. */
  protected browser(agent: string | null): string {
    if (!agent) {
      return this.i18n.t('own.a_browser');
    }
    const name =
      ['Edg', 'Firefox', 'Chrome', 'Safari'].find((b) => agent.includes(b)) ??
      this.i18n.t('own.a_browser');
    const system = ['Windows', 'Mac OS', 'Android', 'iPhone', 'iPad', 'Linux'].find((s) =>
      agent.includes(s),
    );
    return [name === 'Edg' ? 'Edge' : name, system?.replace('Mac OS', 'macOS')]
      .filter(Boolean)
      .join(', ');
  }

  protected async make(): Promise<void> {
    this.busy.set(true);
    try {
      const expires = new Date(Date.now() + this.days * 86_400_000).toISOString();
      const addresses = this.addresses
        .split(/[\s,]+/)
        .map((a) => a.trim())
        .filter(Boolean);
      const { data } = await this.api.post<{ token: string }>('/api/v1/tokens', {
        name: this.name.trim(),
        person: this.actsAs,
        expires,
        addresses: addresses.length ? addresses : null,
      });
      this.made.set(data.token);
      this.name = '';
      this.addresses = '';
      await this.load();
    } catch (error) {
      this.snack.open(describe(error), this.i18n.t('common.close'));
    } finally {
      this.busy.set(false);
    }
  }

  protected copy(token: string): void {
    this.clipboard.copy(token);
    this.snack.open(this.i18n.t('own.copied'), undefined, { duration: 2500 });
  }

  protected async revoke(token: Token): Promise<void> {
    await this.remove(`${TOKENS}/${token.sys_id}`, token._etag);
  }

  protected async end(session: Session): Promise<void> {
    await this.remove(`${SESSIONS}/${session.sys_id}`, session._etag);
  }

  protected async endOthers(): Promise<void> {
    for (const s of this.sessions().filter((s) => s.sys_id !== this.mine())) {
      await this.remove(`${SESSIONS}/${s.sys_id}`, s._etag, false);
    }
    await this.load();
  }

  private async remove(path: string, etag: string, reload = true): Promise<void> {
    try {
      await this.api.delete(path, etag);
      if (reload) {
        await this.load();
      }
    } catch (error) {
      this.snack.open(describe(error), this.i18n.t('common.close'));
    }
  }
}
