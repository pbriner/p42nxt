import { Injectable, inject } from '@angular/core';

import { Api } from './api';
import { Favorites } from './favorites';
import { Navigation } from './navigation';
import { Preferences } from './preferences';
import { Search } from './search';
import { Session } from './session';

export type Stage = 'second_factor' | 'enroll' | 'active';

export interface Started {
  stage: Stage;
  recovery_codes?: string[];
}

export interface NewApp {
  credential: string;
  key: string;
  link: string;
}

export interface Provider {
  id: string;
  name: string;
}

/** Signing in step by step, and loading what a signed-in person needs. */
@Injectable({ providedIn: 'root' })
export class SignIn {
  private readonly api = inject(Api);
  private readonly session = inject(Session);
  private readonly preferences = inject(Preferences);
  private readonly navigation = inject(Navigation);
  private readonly favorites = inject(Favorites);
  private readonly search = inject(Search);

  private async step(path: string, body: unknown): Promise<Started> {
    const { data } = await this.api.post<Started>(`/api/v1/sign-in${path}`, body, { quiet: true });
    return data;
  }

  providers(): Promise<Provider[]> {
    return this.api
      .get<Provider[]>('/api/v1/sign-in/providers', { quiet: true })
      .then((a) => a.data)
      .catch(() => []);
  }

  password(email: string, password: string): Promise<Started> {
    return this.step('', { email, password });
  }

  code(code: string): Promise<Started> {
    return this.step('/code', { code });
  }

  invitation(token: string, password: string): Promise<Started> {
    return this.step('/invitation', { token, password });
  }

  async newApp(): Promise<NewApp> {
    const { data } = await this.api.post<NewApp>('/api/v1/sign-in/setup/app', {}, { quiet: true });
    return data;
  }

  confirmApp(credential: string, code: string): Promise<Started> {
    return this.step('/setup/app/confirm', { credential, code });
  }

  /** Signing in with a passkey as the second factor. */
  async passkey(): Promise<Started> {
    const { data } = await this.api.post<PublicKeyCredentialRequestOptionsJSON>(
      '/api/v1/sign-in/passkey/challenge',
      {},
      { quiet: true },
    );
    const options = PublicKeyCredential.parseRequestOptionsFromJSON(data);
    const answer = (await navigator.credentials.get({ publicKey: options })) as PublicKeyCredential;
    return this.step('/passkey', { answer: answer.toJSON() });
  }

  /** Making a first passkey while setting up the second factor. */
  async newPasskey(): Promise<Started> {
    const { data } = await this.api.post<PublicKeyCredentialCreationOptionsJSON>(
      '/api/v1/sign-in/setup/passkey/options',
      {},
      { quiet: true },
    );
    const options = PublicKeyCredential.parseCreationOptionsFromJSON(data);
    const made = (await navigator.credentials.create({
      publicKey: options,
    })) as PublicKeyCredential;
    return this.step('/setup/passkey', { answer: made.toJSON(), label: 'Passkey' });
  }

  /** Once signed in: who they are, their preferences, navigator and favorites. */
  async signedIn(): Promise<void> {
    this.session.forget();
    this.search.forget();
    await this.session.ensure();
    await Promise.all([this.preferences.load(), this.navigation.load(), this.favorites.load()]);
  }
}
