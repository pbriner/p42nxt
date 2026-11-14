/** Per-browser conveniences (open sections): never needed, so failures are ignored. */
export function remembered(key: string): string[] {
  try {
    const found = JSON.parse(localStorage.getItem(key) ?? '[]');
    return Array.isArray(found) ? found.filter((v) => typeof v === 'string') : [];
  } catch {
    return [];
  }
}

export function remember(key: string, values: Iterable<string>): void {
  try {
    localStorage.setItem(key, JSON.stringify([...values]));
  } catch {
    // Private windows may refuse; it only costs the open sections.
  }
}
