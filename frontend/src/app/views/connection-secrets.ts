import { Component, effect, inject, input, signal, untracked } from '@angular/core';
import { MatButtonModule } from '@angular/material/button';
import { MatCardModule } from '@angular/material/card';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatInputModule } from '@angular/material/input';

import { Api, describe, humanize } from '../core/api';
import { I18n } from '../core/i18n';

interface Secret {
  name: string;
  set: boolean;
  sys_id: string | null;
  etag: string | null;
  expires: string | null;
}

const SECRETS = '/api/v1/sys/connection_secret';
// Words people know, for the secrets connection types usually name.
const WORDS: Record<string, string> = { api: 'API', url: 'URL', id: 'ID' };

/** A connection's secrets, as its type names them: set or not, and each one written (sealed at
 * once, never read back) through the normal record routes. */
@Component({
  selector: 'app-connection-secrets',
  imports: [MatButtonModule, MatCardModule, MatFormFieldModule, MatInputModule],
  template: `
    <mat-card appearance="outlined" class="section">
      <mat-card-header>
        <mat-card-title>{{ i18n.t('secrets.title') }}</mat-card-title>
      </mat-card-header>
      <mat-card-content>
        <p class="muted">{{ i18n.t('secrets.explain') }}</p>
        @for (s of secrets(); track s.name) {
          <div class="row">
            <mat-form-field appearance="outline" class="input">
              <mat-label>{{ label(s.name) }}</mat-label>
              <input
                matInput
                type="password"
                autocomplete="off"
                [value]="typed()[s.name] ?? ''"
                [placeholder]="s.set ? i18n.t('secrets.set') : i18n.t('secrets.not_set')"
                (input)="typeIn(s.name, $any($event.target).value)"
                (keydown.enter)="$event.preventDefault(); save(s)"
              />
              <mat-hint>{{ s.set ? i18n.t('secrets.set') : i18n.t('secrets.not_set') }}</mat-hint>
            </mat-form-field>
            <mat-form-field appearance="outline" class="date">
              <mat-label>{{ i18n.t('secrets.expires') }}</mat-label>
              <input
                matInput
                type="date"
                [value]="dates()[s.name] ?? s.expires ?? ''"
                (input)="dateIn(s.name, $any($event.target).value)"
              />
              <mat-hint>{{ i18n.t('secrets.expires_hint') }}</mat-hint>
            </mat-form-field>
            <button
              mat-stroked-button
              type="button"
              [disabled]="(!typed()[s.name] && !(s.set && s.name in dates())) || busy()"
              (click)="save(s)"
            >
              {{ i18n.t('secrets.save') }}
            </button>
          </div>
        } @empty {
          <p class="muted">{{ i18n.t('secrets.none') }}</p>
        }
        @if (said(); as text) {
          <p role="status">{{ text }}</p>
        }
      </mat-card-content>
    </mat-card>
  `,
  styles: `
    .row {
      display: flex;
      gap: 12px;
      align-items: baseline;
      flex-wrap: wrap;
    }
    .input {
      flex: 1;
      min-width: min(320px, 100%);
    }
    .date {
      width: min(200px, 100%);
    }
  `,
})
export class ConnectionSecrets {
  protected readonly i18n = inject(I18n);
  private readonly api = inject(Api);
  /** The connection's ID, and its type's name. */
  readonly connection = input.required<string>();
  readonly type = input.required<string>();
  protected readonly secrets = signal<Secret[]>([]);
  protected readonly typed = signal<Record<string, string>>({});
  /** Expiry dates changed here, by secret; '' clears one. */
  protected readonly dates = signal<Record<string, string>>({});
  protected readonly busy = signal(false);
  protected readonly said = signal<string | null>(null);

  constructor() {
    effect(() => {
      const connection = this.connection();
      const type = this.type();
      untracked(() => void this.load(connection, type));
    });
  }

  private async load(connection: string, type: string): Promise<void> {
    const [types, kept] = await Promise.all([
      this.api.get<{ records: { secrets: string[] | null }[] }>('/api/v1/sys/connection_type', {
        query: { 'filter[name]': type, fields: 'secrets', count: false },
      }),
      this.api.get<{
        records: { sys_id: string; name: string; _etag: string; expires: string | null }[];
      }>(SECRETS, {
        query: {
          'filter[connection]': connection,
          fields: 'name,expires',
          count: false,
          limit: 50,
        },
      }),
    ]);
    const have = new Map(kept.data.records.map((r) => [r.name, r]));
    this.secrets.set(
      (types.data.records[0]?.secrets ?? []).map((name) => ({
        name,
        set: have.has(name),
        sys_id: have.get(name)?.sys_id ?? null,
        etag: have.get(name)?._etag ?? null,
        expires: have.get(name)?.expires ?? null,
      })),
    );
  }

  protected label(name: string): string {
    return humanize(name)
      .split(' ')
      .map((w) => WORDS[w.toLowerCase()] ?? w)
      .join(' ');
  }

  protected typeIn(name: string, value: string): void {
    this.typed.update((all) => ({ ...all, [name]: value }));
  }

  protected dateIn(name: string, value: string): void {
    this.dates.update((all) => ({ ...all, [name]: value }));
  }

  protected async save(secret: Secret): Promise<void> {
    const value = this.typed()[secret.name];
    const dated = secret.name in this.dates();
    if ((!value && !(secret.set && dated)) || this.busy()) {
      return;
    }
    const changes: Record<string, unknown> = value ? { value } : {};
    if (dated) {
      changes['expires'] = this.dates()[secret.name] || null;
    }
    this.busy.set(true);
    this.said.set(null);
    try {
      if (secret.sys_id && secret.etag) {
        await this.api.patch(`${SECRETS}/${secret.sys_id}`, changes, secret.etag);
      } else {
        await this.api.post(SECRETS, {
          connection: this.connection(),
          name: secret.name,
          ...changes,
        });
      }
      this.typed.update((all) => ({ ...all, [secret.name]: '' }));
      this.dates.update(({ [secret.name]: _, ...rest }) => rest);
      this.said.set(this.i18n.t('secrets.saved', { name: this.label(secret.name) }));
      await this.load(this.connection(), this.type());
    } catch (error) {
      this.said.set(describe(error));
    } finally {
      this.busy.set(false);
    }
  }
}
