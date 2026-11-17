import { Api } from './api';
import { TableInfo } from './format';

export interface FormSection {
  title: unknown;
  fields: string[];
  columns: number;
}

export interface FormView {
  sections: FormSection[];
  related: { table: string; field: string }[] | null;
  related_auto: boolean;
}

/** A table's form view, else one section with its fields. */
export async function formViewOf(api: Api, info: TableInfo): Promise<FormView> {
  try {
    const { data } = await api.get<{ records: FormView[] }>('/api/v1/sys/form_view', {
      query: { 'filter[name]': info.table, count: false },
    });
    if (data.records[0]) {
      return data.records[0];
    }
  } catch {
    // Made from the fields below.
  }
  return {
    sections: [
      {
        title: info.label,
        fields: info.fields.filter((f) => !f.system).map((f) => f.name),
        columns: 2,
      },
    ],
    related: null,
    related_auto: true,
  };
}
