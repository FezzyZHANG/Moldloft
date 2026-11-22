// Theme registry and persistence; purely visual preference with no server state.
const STORAGE_KEY = 'moldloft.theme.v1';
export const THEMES = Object.freeze([
  {id: 'endfield', label: '终末地 · Endfield'},
  {id: 'monokai', label: 'Monokai'},
  {id: 'loft', label: '放样间 · Loft'},
]);
export const DEFAULT_THEME = 'endfield';
const VALID_IDS = new Set(THEMES.map(theme => theme.id));

export function loadTheme(storage = globalThis.localStorage) {
  try {
    const saved = storage?.getItem(STORAGE_KEY);
    return VALID_IDS.has(saved) ? saved : DEFAULT_THEME;
  } catch {
    return DEFAULT_THEME;
  }
}

export function saveTheme(id, storage = globalThis.localStorage) {
  const resolved = VALID_IDS.has(id) ? id : DEFAULT_THEME;
  try { storage?.setItem(STORAGE_KEY, resolved); }
  catch {}
  return resolved;
}

export function applyTheme(id, root = globalThis.document?.documentElement) {
  const resolved = VALID_IDS.has(id) ? id : DEFAULT_THEME;
  if (root?.dataset) root.dataset.theme = resolved;
  return resolved;
}

export class ThemeManager {
  constructor(select, {storage = globalThis.localStorage, root = globalThis.document?.documentElement, onChange} = {}) {
    this.select = select;
    this.storage = storage;
    this.root = root;
    this.onChange = onChange;
    if (this.select?.replaceChildren && globalThis.document?.createElement) {
      this.select.replaceChildren(...THEMES.map(({id, label}) => {
        const option = globalThis.document.createElement('option');
        option.value = id;
        option.textContent = label;
        return option;
      }));
    }
    this.current = applyTheme(loadTheme(this.storage), this.root);
    if (this.select) {
      this.select.value = this.current;
      this.select.addEventListener('change', () => this.set(this.select.value));
    }
  }

  set(id) {
    const resolved = applyTheme(id, this.root);
    this.current = saveTheme(resolved, this.storage);
    if (this.select) this.select.value = this.current;
    this.onChange?.(this.current);
    return this.current;
  }
}
