import { Component, computed, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { MatButtonModule } from '@angular/material/button';
import { MatCardModule } from '@angular/material/card';
import { MatCheckboxModule } from '@angular/material/checkbox';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatInputModule } from '@angular/material/input';
import { MatProgressBarModule } from '@angular/material/progress-bar';
import { MatRadioModule } from '@angular/material/radio';
import { MatSelectModule } from '@angular/material/select';
import { MatStepperModule } from '@angular/material/stepper';
import { Router, RouterLink } from '@angular/router';

import { Api, Problem, describe } from '../core/api';
import { I18n } from '../core/i18n';
import { Installation } from '../core/installation';

interface Module {
  id: string;
  name: string;
  description: string | null;
  depends_on: string[];
  required: boolean;
  enabled: boolean;
}

const MIN_PASSWORD = 12;

/** The web installer: a new installation's name, first administrator, modules and safety. */
@Component({
  selector: 'app-setup-page',
  imports: [
    FormsModule,
    MatButtonModule,
    MatCardModule,
    MatCheckboxModule,
    MatFormFieldModule,
    MatInputModule,
    MatProgressBarModule,
    MatRadioModule,
    MatSelectModule,
    MatStepperModule,
    RouterLink,
  ],
  templateUrl: './setup-page.html',
  styleUrl: './setup-page.scss',
})
export class SetupPage {
  protected readonly i18n = inject(I18n);
  protected readonly installation = inject(Installation);
  private readonly api = inject(Api);
  private readonly router = inject(Router);

  protected readonly modules = signal<Module[]>([]);
  protected readonly chosen = signal(new Set<string>());
  protected readonly busy = signal(false);
  protected readonly error = signal<string | null>(null);
  protected readonly errors = signal<Record<string, string>>({});
  protected readonly min = MIN_PASSWORD;
  protected code = '';
  protected language = this.i18n.language() as string;
  protected name = '';
  protected adminName = '';
  protected email = '';
  protected password = '';
  protected repeat = '';
  protected backups: 'done' | 'later' | null = null;
  protected masterKey = false;
  protected backupFolder = '';
  protected releaseAddress = '';
  /** The folder install.sh made: Docker gives the worker this one until the next restart. */
  protected madeFolder = '';

  /** The modules kept: those chosen, what they need, and the core's. */
  protected readonly kept = computed(() => {
    const all = this.modules();
    const kept = new Set([...all.filter((m) => m.required).map((m) => m.id), ...this.chosen()]);
    let grew = true;
    while (grew) {
      grew = false;
      for (const m of all.filter((x) => kept.has(x.id))) {
        for (const other of m.depends_on) {
          if (!kept.has(other)) {
            kept.add(other);
            grew = true;
          }
        }
      }
    }
    return kept;
  });

  constructor() {
    void this.load();
  }

  private async load(): Promise<void> {
    try {
      const [{ data }, machine] = await Promise.all([
        this.api.get<{ modules: Module[] }>('/api/v1/setup/modules', { quiet: true }),
        this.api.get<{ backup_folder: string; release_address: string }>('/api/v1/setup/settings', {
          quiet: true,
        }),
      ]);
      this.modules.set(data.modules);
      this.chosen.set(new Set(data.modules.filter((m) => !m.required).map((m) => m.id)));
      this.madeFolder = machine.data.backup_folder;
      this.backupFolder = machine.data.backup_folder;
      this.releaseAddress = machine.data.release_address;
    } catch {
      this.installation.setupOpen.set(false);
    }
  }

  protected chooseLanguage(language: string): void {
    this.language = language;
    this.i18n.choose(language);
  }

  /** Why a module can't be switched off here, or null. */
  protected locked(m: Module): string | null {
    if (m.required) {
      return this.i18n.t('setup.always');
    }
    const needing = this.modules().filter(
      (o) => this.kept().has(o.id) && o.depends_on.includes(m.id),
    );
    return needing.length
      ? this.i18n.t('setup.needed_by', { names: needing.map((o) => o.name).join(', ') })
      : null;
  }

  protected toggle(m: Module, on: boolean): void {
    this.chosen.update((all) => {
      const copy = new Set(all);
      if (on) {
        copy.add(m.id);
      } else {
        copy.delete(m.id);
      }
      return copy;
    });
  }

  protected adminReady(): boolean {
    return (
      !!this.adminName.trim() &&
      /^[^@\s]+@[^@\s]+$/.test(this.email.trim()) &&
      this.password.length >= MIN_PASSWORD &&
      this.password === this.repeat
    );
  }

  protected async finish(): Promise<void> {
    this.busy.set(true);
    this.error.set(null);
    this.errors.set({});
    try {
      await this.api.post(
        '/api/v1/setup',
        {
          code: this.code.trim(),
          language: this.language,
          name: this.name.trim(),
          admin_name: this.adminName.trim(),
          email: this.email.trim(),
          password: this.password,
          modules: [...this.kept()],
          backups: this.backups === 'done',
          master_key: this.masterKey,
          backup_folder: this.backupFolder.trim(),
          release_address: this.releaseAddress.trim(),
        },
        { quiet: true },
      );
      this.installation.setupOpen.set(false);
      await this.installation.load();
      // Signed in up to the second factor: set it up next.
      await this.router.navigate(['/sign-in'], { state: { stage: 'enroll' } });
    } catch (error) {
      if (error instanceof Problem && error.errors.length) {
        this.errors.set(Object.fromEntries(error.errors.map((e) => [e.field, e.message])));
      }
      this.error.set(describe(error));
    } finally {
      this.busy.set(false);
    }
  }
}
