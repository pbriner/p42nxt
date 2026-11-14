import {
  ApplicationConfig,
  inject,
  provideAppInitializer,
  provideBrowserGlobalErrorListeners,
  provideZonelessChangeDetection,
} from '@angular/core';
import { MatIconRegistry } from '@angular/material/icon';
import { provideRouter, withComponentInputBinding } from '@angular/router';

import { routes } from './app.routes';
import { Preferences } from './core/preferences';
import { Session } from './core/session';
import { SignIn } from './core/sign-in';

/** At start: who is signed in, and their preferences, theme and navigator. */
async function start(): Promise<void> {
  inject(MatIconRegistry).setDefaultFontSetClass('material-symbols-outlined');
  const session = inject(Session);
  const signIn = inject(SignIn);
  const preferences = inject(Preferences);
  try {
    const me = await session.ensure();
    await (me ? signIn.signedIn() : preferences.loadSignedOut());
  } catch {
    await preferences.loadSignedOut();
  }
}

export const appConfig: ApplicationConfig = {
  providers: [
    provideBrowserGlobalErrorListeners(),
    provideZonelessChangeDetection(),
    provideRouter(routes, withComponentInputBinding()),
    provideAppInitializer(start),
  ],
};
