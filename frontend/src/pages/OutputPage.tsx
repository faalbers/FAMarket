/**
 * /output — the launcher without `?run=`, one saved output's results with it.
 * The page rebuilds entirely from the URL, so a deep link opened in a fresh tab
 * works cold.
 */
import { getRouteApi } from "@tanstack/react-router";
import { OutputLauncher } from "@/pages/OutputLauncher";
import { OutputRun } from "@/pages/OutputRun";

// getRouteApi gives typed search params without importing the route object
// (which would be a circular import with main.tsx).
const route = getRouteApi("/output");

export function OutputPage() {
  const { run } = route.useSearch();
  return run ? <OutputRun key={run} runId={run} /> : <OutputLauncher />;
}
