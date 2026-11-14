import { Component, DestroyRef, computed, inject, signal } from '@angular/core';
import { MatButtonModule } from '@angular/material/button';
import { MatCardModule } from '@angular/material/card';
import { MatSlideToggleModule } from '@angular/material/slide-toggle';
import { MatSnackBar } from '@angular/material/snack-bar';

import { Api, describe } from '../core/api';
import { textIn } from '../core/format';
import { I18n } from '../core/i18n';
import { path } from '../core/tables';

const LINKS = 'sys.person_channel';
const CHOICES = 'sys.notification_subscription';
const ASK_EVERY = 3000;

interface Link {
  sys_id: string;
  _etag: string;
  state: 'waiting' | 'linked';
  name: string | null;
  bot: string | null;
  expires: string | null;
  full_text: boolean;
}

interface Kind {
  sys_id: string;
  name: string;
  label: unknown;
  description: unknown;
  channels: string[] | null;
}

interface Choice {
  sys_id: string;
  _etag: string;
  kind: string;
  on: boolean;
}

/** A one-time code for the Telegram start link: random, URL-safe, shown only here. */
function newCode(): string {
  const bytes = crypto.getRandomValues(new Uint8Array(18));
  return btoa(String.fromCharCode(...bytes))
    .replace(/\+/g, '-')
    .replace(/\//g, '_')
    .replace(/=+$/, '');
}

/** Profile → Notifications: Telegram as a second channel, and what goes there. */
@Component({
  selector: 'app-profile-notifications',
  imports: [MatButtonModule, MatCardModule, MatSlideToggleModule],
  template: `
    <mat-card appearance="outlined">
      <mat-card-header>
        <mat-card-title>{{ i18n.t('notify.title') }}</mat-card-title>
      </mat-card-header>
      <mat-card-content>
        <p class="muted">{{ i18n.t('notify.intro') }}</p>
        <h3>Telegram</h3>
        @if (!bot()) {
          <p class="muted" role="status">{{ i18n.t('notify.no_bot') }}</p>
        } @else if (link()?.state === 'linked') {
          <p role="status">
            <span class="material-symbols-outlined ok" aria-hidden="true">check_circle</span>
            {{ i18n.t('notify.connected', { name: link()?.name ?? '' }) }}
          </p>
          <mat-slide-toggle
            [checked]="link()?.full_text ?? false"
            (change)="fullText($event.checked)"
          >
            {{ i18n.t('notify.full_text') }}
          </mat-slide-toggle>
          <p class="hint">{{ i18n.t('notify.full_text_hint') }}</p>
          <button mat-stroked-button type="button" (click)="disconnect()">
            {{ i18n.t('notify.disconnect') }}
          </button>
        } @else if (code(); as shown) {
          <p role="status">{{ i18n.t('notify.press_start') }}</p>
          <div class="row">
            <a
              mat-flat-button
              target="_blank"
              rel="noopener noreferrer"
              [href]="'https://t.me/' + bot() + '?start=' + shown"
            >
              <span class="material-symbols-outlined" aria-hidden="true">open_in_new</span>
              {{ i18n.t('notify.open_telegram') }}
            </a>
            <button mat-button type="button" (click)="disconnect()">
              {{ i18n.t('common.cancel') }}
            </button>
          </div>
        } @else {
          <button mat-flat-button type="button" (click)="connect()">
            {{ i18n.t('notify.connect') }}
          </button>
        }

        @if (kinds().length) {
          <h3>{{ i18n.t('notify.kinds') }}</h3>
          @if (!linked()) {
            <p class="hint">{{ i18n.t('notify.kinds_hint') }}</p>
          }
          <ul class="kinds">
            @for (kind of kinds(); track kind.sys_id) {
              <li>
                <span class="kind">
                  <span class="label">{{ text(kind.label) }}</span>
                  @if (text(kind.description); as about) {
                    <span class="hint">{{ about }}</span>
                  }
                </span>
                <mat-slide-toggle
                  [checked]="isOn(kind)"
                  [disabled]="!linked()"
                  [aria-label]="i18n.t('notify.on_telegram', { kind: text(kind.label) })"
                  (change)="choose(kind, $event.checked)"
                >
                  Telegram
                </mat-slide-toggle>
              </li>
            }
          </ul>
        }
      </mat-card-content>
    </mat-card>
  `,
  styles: `
    h3 {
      font: var(--mat-sys-title-small);
      margin: 20px 0 8px;
    }
    .muted,
    .hint {
      color: var(--mat-sys-on-surface-variant);
    }
    .hint {
      font: var(--mat-sys-body-small);
      margin: 4px 0 12px;
    }
    .ok {
      vertical-align: middle;
      color: var(--mat-sys-primary);
    }
    .row {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
    }
    .kinds {
      list-style: none;
      margin: 0;
      padding: 0;
    }
    .kinds li {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      min-height: 48px;
      padding: 4px 0;
      border-bottom: 1px solid var(--mat-sys-outline-variant);
    }
    .kind {
      display: flex;
      flex-direction: column;
      min-width: 0;
    }
    .kind .hint {
      margin: 0;
    }
  `,
})
export class ProfileNotifications {
  protected readonly i18n = inject(I18n);
  private readonly api = inject(Api);
  private readonly snack = inject(MatSnackBar);

  protected readonly bot = signal<string | null>(null);
  protected readonly link = signal<Link | null>(null);
  protected readonly code = signal<string | null>(null);
  protected readonly kinds = signal<Kind[]>([]);
  private readonly choices = signal<Choice[]>([]);
  protected readonly linked = computed(() => this.link()?.state === 'linked');
  private asking: ReturnType<typeof setInterval> | null = null;

  constructor() {
    void this.load();
    inject(DestroyRef).onDestroy(() => this.stopAsking());
  }

  private async load(): Promise<void> {
    const [state, kinds] = await Promise.all([
      this.api.get<{ records: { bot: string | null }[] }>(path('sys.channel_state'), {
        query: { 'filter[name]': 'telegram', fields: 'bot', count: false },
      }),
      this.api.get<{ records: Kind[] }>(path('sys.notification_kind'), {
        query: { sort: 'name', limit: 200, count: false },
      }),
      this.refresh(),
    ]);
    this.bot.set(state.data.records[0]?.bot ?? null);
    this.kinds.set(kinds.data.records);
  }

  private async refresh(): Promise<void> {
    const [links, choices] = await Promise.all([
      this.api.get<{ records: Link[] }>(path(LINKS), {
        query: { 'filter[channel]': 'telegram', count: false },
        background: true,
      }),
      this.api.get<{ records: Choice[] }>(path(CHOICES), {
        query: { 'filter[channel]': 'telegram', limit: 500, count: false },
        background: true,
      }),
    ]);
    const found = links.data.records[0] ?? null;
    this.link.set(found);
    this.choices.set(choices.data.records);
    if (found?.state === 'linked') {
      this.code.set(null);
      this.stopAsking();
    } else if (found === null) {
      this.code.set(null);
    }
  }

  protected text(value: unknown): string {
    return textIn(value, this.i18n.language());
  }

  protected isOn(kind: Kind): boolean {
    const chosen = this.choices().find((c) => c.kind === kind.name);
    return chosen ? chosen.on : (kind.channels ?? []).includes('telegram');
  }

  protected async connect(): Promise<void> {
    const code = newCode();
    try {
      await this.api.post(path(LINKS), { channel: 'telegram', code });
      this.code.set(code);
      await this.refresh();
      this.stopAsking();
      // Until they press Start in Telegram (the worker reads the bot every few seconds).
      this.asking = setInterval(() => void this.refresh(), ASK_EVERY);
    } catch (error) {
      this.snack.open(describe(error), this.i18n.t('common.close'));
    }
  }

  protected async disconnect(): Promise<void> {
    const current = this.link();
    this.stopAsking();
    this.code.set(null);
    if (!current) {
      return;
    }
    try {
      await this.api.delete(path(LINKS, current.sys_id), current._etag);
      this.link.set(null);
      if (current.state === 'linked') {
        this.snack.open(this.i18n.t('notify.disconnected'), undefined, { duration: 3000 });
      }
    } catch (error) {
      this.snack.open(describe(error), this.i18n.t('common.close'));
    }
  }

  protected async fullText(on: boolean): Promise<void> {
    const current = this.link();
    if (!current) {
      return;
    }
    try {
      const { data } = await this.api.patch<Link>(
        path(LINKS, current.sys_id),
        { full_text: on },
        current._etag,
      );
      this.link.set(data);
      this.snack.open(this.i18n.t('common.saved'), undefined, { duration: 2500 });
    } catch (error) {
      this.snack.open(describe(error), this.i18n.t('common.close'));
    }
  }

  protected async choose(kind: Kind, on: boolean): Promise<void> {
    const chosen = this.choices().find((c) => c.kind === kind.name);
    try {
      if (chosen) {
        await this.api.patch(path(CHOICES, chosen.sys_id), { on }, chosen._etag);
      } else {
        await this.api.post(path(CHOICES), { kind: kind.name, channel: 'telegram', on });
      }
      await this.refresh();
      this.snack.open(this.i18n.t('common.saved'), undefined, { duration: 2500 });
    } catch (error) {
      this.snack.open(describe(error), this.i18n.t('common.close'));
    }
  }

  private stopAsking(): void {
    if (this.asking !== null) {
      clearInterval(this.asking);
      this.asking = null;
    }
  }
}
