import { Component, ElementRef, inject, input, OnInit, signal, viewChild } from '@angular/core';
import { MatButtonModule } from '@angular/material/button';
import { MatCardModule } from '@angular/material/card';
import { MatIconModule } from '@angular/material/icon';
import { MatSnackBar } from '@angular/material/snack-bar';

import { Api, describe } from '../core/api';
import { I18n } from '../core/i18n';
import { path } from '../core/tables';

interface StoredFile {
  sys_id: string;
  name: string;
  type: string;
  size: number;
  created: string;
}

/** What browsers show themselves; the rest is downloaded. */
const SHOWN = new Set(['application/pdf', 'image/png', 'image/jpeg', 'image/gif', 'image/webp']);

/** The files attached to a record: open or download them; attach and remove them where the
 *  person may change the record. */
@Component({
  selector: 'app-record-files',
  imports: [MatButtonModule, MatCardModule, MatIconModule],
  template: `
    <mat-card appearance="outlined" class="section">
      <mat-card-header>
        <mat-card-title>{{ i18n.t('files.title') }}</mat-card-title>
      </mat-card-header>
      <mat-card-content>
        @for (f of files(); track f.sys_id) {
          <div class="file">
            <mat-icon aria-hidden="true">{{ icon(f.type) }}</mat-icon>
            <a [href]="link(f, true)" target="_blank" rel="noopener">{{ f.name }}</a>
            <span class="muted">{{ size(f.size) }} · {{ when(f.created) }}</span>
            <span class="spacer"></span>
            <a
              mat-icon-button
              [href]="link(f, false)"
              [attr.aria-label]="i18n.t('files.download', { name: f.name })"
            >
              <mat-icon>download</mat-icon>
            </a>
            @if (mayChange()) {
              <button
                mat-icon-button
                type="button"
                [disabled]="busy()"
                [attr.aria-label]="i18n.t('files.remove', { name: f.name })"
                (click)="remove(f)"
              >
                <mat-icon>delete</mat-icon>
              </button>
            }
          </div>
        } @empty {
          <p class="muted">{{ i18n.t('files.none') }}</p>
        }
        @if (mayChange()) {
          <input
            #picker
            id="record-files-picker"
            type="file"
            hidden
            multiple
            accept=".pdf,.png,.jpg,.jpeg,.gif,.webp,.heic,.xlsx,.docx,.pptx,.txt,.csv,.md"
            (change)="picked()"
          />
          <button mat-stroked-button type="button" [disabled]="busy()" (click)="choose()">
            <mat-icon>attach_file</mat-icon> {{ i18n.t('files.add') }}
          </button>
        }
        @if (problem(); as said) {
          <p role="alert">{{ said }}</p>
        }
      </mat-card-content>
    </mat-card>
  `,
  styles: `
    .file {
      display: flex;
      align-items: center;
      gap: 8px;
      flex-wrap: wrap;
      min-width: 0;
    }
    .file a:not([mat-icon-button]) {
      overflow-wrap: anywhere;
    }
    .spacer {
      flex: 1;
    }
  `,
})
export class RecordFiles implements OnInit {
  protected readonly i18n = inject(I18n);
  private readonly api = inject(Api);
  private readonly snack = inject(MatSnackBar);
  readonly table = input.required<string>();
  readonly id = input.required<string>();
  readonly mayChange = input(false);
  protected readonly files = signal<StoredFile[]>([]);
  protected readonly busy = signal(false);
  protected readonly problem = signal<string | null>(null);
  private readonly picker = viewChild<ElementRef<HTMLInputElement>>('picker');

  async ngOnInit(): Promise<void> {
    await this.load();
  }

  private async load(): Promise<void> {
    try {
      const { data } = await this.api.get<{ files: StoredFile[] }>(
        `${path(this.table(), this.id())}/files`,
      );
      this.files.set(data.files);
    } catch (error) {
      this.problem.set(describe(error));
    }
  }

  protected link(f: StoredFile, open: boolean): string {
    const inline = open && SHOWN.has(f.type) ? '?inline=true' : '';
    return `/api/v1/files/${f.sys_id}${inline}`;
  }

  protected icon(type: string): string {
    if (type.startsWith('image/')) return 'image';
    if (type === 'application/pdf') return 'picture_as_pdf';
    return 'description';
  }

  protected size(bytes: number): string {
    const language = this.i18n.language();
    if (bytes < 1024 * 1024) {
      return `${Math.max(1, Math.round(bytes / 1024)).toLocaleString(language)} KB`;
    }
    return `${(bytes / (1024 * 1024)).toLocaleString(language, { maximumFractionDigits: 1 })} MB`;
  }

  protected when(at: string): string {
    return new Date(at).toLocaleDateString(this.i18n.language(), { dateStyle: 'medium' });
  }

  protected choose(): void {
    this.picker()?.nativeElement.click();
  }

  protected async picked(): Promise<void> {
    const input = this.picker()?.nativeElement;
    const chosen = Array.from(input?.files ?? []);
    if (input) input.value = '';
    if (!chosen.length) return;
    this.busy.set(true);
    this.problem.set(null);
    try {
      for (const file of chosen) {
        await this.api.request('POST', `${path(this.table(), this.id())}/files`, {
          query: { name: file.name },
          raw: file,
        });
      }
      this.snack.open(this.i18n.t('files.added', { count: chosen.length }), undefined, {
        duration: 3000,
      });
    } catch (error) {
      this.problem.set(describe(error));
    } finally {
      this.busy.set(false);
      await this.load();
    }
  }

  protected async remove(f: StoredFile): Promise<void> {
    this.busy.set(true);
    this.problem.set(null);
    try {
      await this.api.delete(`/api/v1/files/${f.sys_id}`, '');
      this.snack.open(this.i18n.t('files.removed', { name: f.name }), undefined, {
        duration: 3000,
      });
    } catch (error) {
      this.problem.set(describe(error));
    } finally {
      this.busy.set(false);
      await this.load();
    }
  }
}
