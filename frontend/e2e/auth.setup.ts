import { request, test as setup } from '@playwright/test';

import { code, person, state, storage } from './helpers';

// Signs in once by API and keeps the cookies, so the tests start signed in.
for (const name of ['admin', 'plain']) {
  setup(`sign in ${name}`, async () => {
    const who = person(name);
    const api = await request.newContext({
      baseURL: state().url,
      extraHTTPHeaders: { Origin: state().url },
    });
    const first = await api.post('/api/v1/sign-in', {
      data: { email: who.email, password: who.password },
    });
    if (!first.ok()) {
      throw new Error(`sign-in failed: ${await first.text()}`);
    }
    const second = await api.post('/api/v1/sign-in/code', { data: { code: code(who.key) } });
    if (!second.ok()) {
      throw new Error(`code refused: ${await second.text()}`);
    }
    await api.storageState({ path: storage(name) });
    await api.dispose();
  });
}
