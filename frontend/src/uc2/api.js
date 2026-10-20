import { selectSyntheticSnapshot } from "./syntheticData.js";

export async function loadSyntheticDashboard(filters, { signal } = {}) {
  signal?.throwIfAborted();
  const snapshot = selectSyntheticSnapshot(filters);
  signal?.throwIfAborted();
  return snapshot;
}