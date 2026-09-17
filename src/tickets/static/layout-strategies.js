import {compactLayered} from './layouts/compact-layered.js';

const strategies = new Map();
export const DEFAULT_LAYOUT = 'compact-layered';

// Every strategy consumes an owned projection and returns the same positioned graph contract.
// Engines are injected: the renderer knows neither ELK options nor packing policy.
export function registerLayoutStrategy(id, strategy) {
  if (!id || strategies.has(id) || typeof strategy?.compute !== 'function') throw new Error(`Invalid or duplicate layout strategy: ${id}`);
  strategies.set(id, Object.freeze({...strategy}));
}
export const layoutStrategies = () => [...strategies].map(([id, strategy]) => ({id, label: strategy.label, defaults: {...strategy.defaults}}));
export async function computeLayout(projection, {strategy = DEFAULT_LAYOUT, engine, options = {}} = {}) {
  const implementation = strategies.get(strategy);
  if (!implementation) throw new Error(`Unknown layout strategy: ${strategy}`);
  const result = await implementation.compute(structuredClone(projection), {engine, options: {...implementation.defaults, ...options}});
  if (!Number.isFinite(result?.width) || !Number.isFinite(result?.height)) throw new Error('Layout returned invalid bounds');
  return result;
}
registerLayoutStrategy(DEFAULT_LAYOUT, compactLayered);
