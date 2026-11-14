import { Routes } from '@angular/router';

import { signedIn } from './core/guards';
import { Shell } from './shell/shell';

const TITLE = 'P42cloud';

export const routes: Routes = [
  {
    path: 'sign-in/invitation',
    loadComponent: () => import('./pages/invitation-page').then((m) => m.InvitationPage),
    title: TITLE,
  },
  {
    path: 'setup',
    loadComponent: () => import('./pages/setup-page').then((m) => m.SetupPage),
    title: TITLE,
  },
  {
    path: 'sign-in',
    loadComponent: () => import('./pages/sign-in-page').then((m) => m.SignInPage),
    title: TITLE,
  },
  {
    path: '',
    component: Shell,
    canActivate: [signedIn],
    children: [
      { path: '', loadComponent: () => import('./pages/home').then((m) => m.Home), title: TITLE },
      {
        path: 'list/:view',
        loadComponent: () => import('./views/list-page').then((m) => m.ListPage),
        title: TITLE,
      },
      {
        path: 'dashboard/:module',
        loadComponent: () => import('./pages/module-dashboard').then((m) => m.ModuleDashboard),
        title: TITLE,
      },
      {
        path: 'form/:table/:id',
        loadComponent: () => import('./views/form-page').then((m) => m.FormPage),
        title: TITLE,
      },
      {
        path: 'search',
        loadComponent: () => import('./pages/search-page').then((m) => m.SearchPage),
        title: TITLE,
      },
      {
        path: 'profile',
        loadComponent: () => import('./pages/profile').then((m) => m.Profile),
        title: TITLE,
      },
      {
        path: '**',
        loadComponent: () => import('./pages/problem-state').then((m) => m.NotFound),
        title: TITLE,
      },
    ],
  },
];
