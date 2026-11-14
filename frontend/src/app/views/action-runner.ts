import { Injectable, inject } from '@angular/core';
import { MatDialog } from '@angular/material/dialog';
import { MatSnackBar } from '@angular/material/snack-bar';
import { firstValueFrom } from 'rxjs';

import { ActionAnswer, ActionDef, Actions } from '../core/actions';
import { describe } from '../core/api';
import { textIn } from '../core/format';
import { I18n } from '../core/i18n';
import { ActionDialog } from './action-dialog';
import { ConfirmDialog } from './confirm-dialog';

/** Running an action from a screen: asking first or asking for inputs, then saying what
 * happened. The server checks it all again. */
@Injectable({ providedIn: 'root' })
export class ActionRunner {
  private readonly actions = inject(Actions);
  private readonly dialog = inject(MatDialog);
  private readonly snack = inject(MatSnackBar);
  private readonly i18n = inject(I18n);

  label(action: ActionDef): string {
    return textIn(action.label, this.i18n.language());
  }

  /** The inputs to send, or null if the person changed their mind. */
  private async ask(action: ActionDef): Promise<Record<string, unknown> | null> {
    if (action.inputs?.length) {
      const ref = this.dialog.open(ActionDialog, { data: action });
      return (await firstValueFrom(ref.afterClosed())) ?? null;
    }
    const question = textIn(action.confirm, this.i18n.language());
    if (question) {
      const ref = this.dialog.open(ConfirmDialog, {
        data: { title: `${this.label(action)}?`, text: question, action: this.label(action) },
      });
      return (await firstValueFrom(ref.afterClosed())) ? {} : null;
    }
    return {};
  }

  async one(action: ActionDef, id: string, etag?: string): Promise<ActionAnswer | null> {
    const inputs = await this.ask(action);
    if (inputs === null) {
      return null;
    }
    try {
      const answer = await this.actions.run(action.table, id, action.name, inputs, etag);
      this.snack.open(
        answer.message ?? this.i18n.t('action.done', { label: this.label(action) }),
        undefined,
        {
          duration: 3000,
        },
      );
      return answer;
    } catch (error) {
      this.snack.open(describe(error), this.i18n.t('common.close'));
      return null;
    }
  }

  async many(action: ActionDef, ids: string[]): Promise<boolean> {
    const inputs = await this.ask(action);
    if (inputs === null) {
      return false;
    }
    try {
      const { results } = await this.actions.runMany(action.table, ids, action.name, inputs);
      const refused = results.filter((r) => !r.ok);
      const done = results.length - refused.length;
      if (refused.length) {
        this.snack.open(
          `${this.i18n.t('action.partly', { n: done, m: refused.length })} · ${refused[0]!.title}`,
          this.i18n.t('common.close'),
        );
      } else {
        this.snack.open(this.i18n.t('action.done', { label: this.label(action) }), undefined, {
          duration: 3000,
        });
      }
    } catch (error) {
      this.snack.open(describe(error), this.i18n.t('common.close'));
    }
    return true;
  }
}
