import { Injectable, signal } from '@angular/core';

/** The values a form shows now (as typed), for editors whose choices depend on other fields: a
 *  cost center picker narrowed to the chosen profit center, a widget's fields of its list. The
 *  form provides it; an editor outside a form finds none. */
@Injectable()
export class FormValues {
  readonly values = signal<Record<string, unknown>>({});
}
