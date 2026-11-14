import { inject } from '@angular/core';
import { CanActivateFn, Router } from '@angular/router';

import { Session } from './session';

/** Pages for signed-in people; others go to sign in, and come back afterwards. */
export const signedIn: CanActivateFn = async (_route, state) => {
  // Both before waiting: inject works only while the guard starts.
  const session = inject(Session);
  const router = inject(Router);
  if (await session.ensure()) {
    return true;
  }
  const next = state.url && state.url !== '/' ? { next: state.url } : {};
  return router.createUrlTree(['/sign-in'], { queryParams: next });
};
