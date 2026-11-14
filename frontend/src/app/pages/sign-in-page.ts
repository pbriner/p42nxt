import { NgTemplateOutlet } from '@angular/common';
import { Component, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { MatButtonModule } from '@angular/material/button';
import { MatCardModule } from '@angular/material/card';
import { MatCheckboxModule } from '@angular/material/checkbox';
import { MatDividerModule } from '@angular/material/divider';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatInputModule } from '@angular/material/input';
import { MatProgressBarModule } from '@angular/material/progress-bar';
import { DomSanitizer, SafeUrl } from '@angular/platform-browser';
import { ActivatedRoute, Router } from '@angular/router';
import qrcode from 'qrcode-generator';

import { describe } from '../core/api';
import { I18n } from '../core/i18n';
import { NewApp, Provider, SignIn, Stage, Started } from '../core/sign-in';

type Step = 'password' | Exclude<Stage, 'active'> | 'app' | 'codes';

/** Where a person goes next: only a path inside the app. */
export function safeNext(next: string | null): string {
  // `//host` and `/\host` lead to another site in browsers.
  return next &&
    next.startsWith('/') &&
    !next.startsWith('//') &&
    !next.includes('\\') &&
    !next.startsWith('/sign-in')
    ? next
    : '/';
}

const AUTHENTICATOR_LINK = /^otpauth:\/\/totp\/[^\s<>"]+$/;

@Component({
  selector: 'app-sign-in-page',
  imports: [
    FormsModule,
    MatButtonModule,
    MatCardModule,
    MatCheckboxModule,
    MatDividerModule,
    MatFormFieldModule,
    MatInputModule,
    MatProgressBarModule,
    NgTemplateOutlet,
  ],
  templateUrl: './sign-in-page.html',
  styleUrl: './sign-in-page.scss',
})
export class SignInPage {
  protected readonly i18n = inject(I18n);
  private readonly signIn = inject(SignIn);
  private readonly router = inject(Router);
  private readonly route = inject(ActivatedRoute);

  protected readonly step = signal<Step>((history.state?.stage as Step) ?? 'password');
  protected readonly busy = signal(false);
  protected readonly error = signal<string | null>(null);
  protected readonly providers = signal<Provider[]>([]);
  protected readonly app = signal<NewApp | null>(null);
  protected readonly qr = signal<string | null>(null);
  protected readonly codes = signal<string[]>([]);
  protected readonly copied = signal(false);
  protected readonly passkeys = 'PublicKeyCredential' in window;
  private readonly sanitizer = inject(DomSanitizer);
  /** The authenticator link, trusted only if it is one (Angular refuses unknown schemes). */
  protected readonly appLink = signal<SafeUrl | null>(null);
  protected email = '';
  protected password = '';
  protected code = '';
  protected saved = false;

  constructor() {
    void this.signIn.providers().then((p) => this.providers.set(p));
  }

  private async run(work: () => Promise<Started | void>): Promise<void> {
    this.busy.set(true);
    this.error.set(null);
    try {
      const started = await work();
      if (started) {
        await this.after(started);
      }
    } catch (error) {
      this.error.set(describe(error));
    } finally {
      this.busy.set(false);
    }
  }

  private async after(started: Started): Promise<void> {
    this.code = '';
    if (started.recovery_codes?.length) {
      this.codes.set(started.recovery_codes);
      this.step.set('codes');
    } else if (started.stage === 'active') {
      await this.done();
    } else {
      this.step.set(started.stage);
    }
  }

  private async done(): Promise<void> {
    await this.signIn.signedIn();
    await this.router.navigateByUrl(safeNext(this.route.snapshot.queryParamMap.get('next')));
  }

  protected withPassword(): Promise<void> {
    return this.run(() => this.signIn.password(this.email.trim(), this.password));
  }

  protected withCode(): Promise<void> {
    return this.run(() => this.signIn.code(this.code.replace(/\s/g, '')));
  }

  protected withPasskey(): Promise<void> {
    return this.run(() => this.signIn.passkey());
  }

  protected setUpApp(): Promise<void> {
    return this.run(async () => {
      const made = await this.signIn.newApp();
      const code = qrcode(0, 'M');
      code.addData(made.link);
      code.make();
      this.qr.set(code.createDataURL(5, 0));
      this.app.set(made);
      this.appLink.set(
        AUTHENTICATOR_LINK.test(made.link)
          ? this.sanitizer.bypassSecurityTrustUrl(made.link)
          : null,
      );
      this.step.set('app');
    });
  }

  protected confirmApp(): Promise<void> {
    return this.run(() =>
      this.signIn.confirmApp(this.app()!.credential, this.code.replace(/\s/g, '')),
    );
  }

  protected setUpPasskey(): Promise<void> {
    return this.run(() => this.signIn.newPasskey());
  }

  protected startAgain(): void {
    this.step.set('password');
    this.password = '';
    this.code = '';
    this.error.set(null);
  }

  protected async copyCodes(): Promise<void> {
    await navigator.clipboard.writeText(this.codes().join('\n'));
    this.copied.set(true);
  }

  protected downloadCodes(): void {
    const link = document.createElement('a');
    link.href = URL.createObjectURL(
      new Blob([this.codes().join('\n') + '\n'], { type: 'text/plain' }),
    );
    link.download = 'recovery-codes.txt';
    link.click();
    URL.revokeObjectURL(link.href);
  }

  protected finish(): Promise<void> {
    return this.run(() => this.done());
  }
}
