import {
  Component,
  ComponentRef,
  ViewContainerRef,
  effect,
  inject,
  input,
  output,
  untracked,
} from '@angular/core';

import { FieldInfo } from '../core/format';
import { unitOf } from './registry';
import { FieldEditor } from './unit';

/** Shows a field's editor, looked up by its type, and reports what the person enters. */
@Component({
  selector: 'app-field',
  template: '',
})
export class FieldHost {
  private readonly container = inject(ViewContainerRef);
  readonly field = input.required<FieldInfo>();
  readonly value = input<unknown>(null);
  readonly error = input<string | null>(null);
  readonly shown = input<string | null>(null);
  readonly changed = output<unknown>();
  private editor: ComponentRef<FieldEditor> | null = null;
  private type: string | null = null;

  constructor() {
    effect(() => {
      const field = this.field();
      untracked(() => this.make(field));
      this.editor?.setInput('field', field);
    });
    effect(() => this.editor?.setInput('value', this.value()));
    effect(() => this.editor?.setInput('error', this.error()));
    effect(() => this.editor?.setInput('shown', this.shown()));
  }

  private make(field: FieldInfo): void {
    if (this.type === field.type && this.editor) {
      return;
    }
    this.container.clear();
    this.type = field.type;
    this.editor = this.container.createComponent(unitOf(field.type).editor);
    this.editor.setInput('field', field);
    this.editor.setInput(
      'value',
      untracked(() => this.value()),
    );
    this.editor.setInput(
      'error',
      untracked(() => this.error()),
    );
    this.editor.setInput(
      'shown',
      untracked(() => this.shown()),
    );
    this.editor.instance.value.subscribe((v) => this.changed.emit(v));
  }
}
