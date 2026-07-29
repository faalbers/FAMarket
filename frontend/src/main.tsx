import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  RouterProvider,
  createRootRoute,
  createRoute,
  createRouter,
  lazyRouteComponent,
  redirect,
} from "@tanstack/react-router";
import { Shell } from "@/components/Shell";
import { validateChartSearch, validateOutputSearch } from "@/lib/search";
import "./index.css";

const rootRoute = createRootRoute({ component: Shell });

const routes = [
  createRoute({
    getParentRoute: () => rootRoute,
    path: "/",
    beforeLoad: () => {
      throw redirect({ to: "/fetch" });
    },
  }),
  createRoute({
    getParentRoute: () => rootRoute,
    path: "/parameters",
    component: lazyRouteComponent(() => import("@/pages/ParametersPage"), "ParametersPage"),
  }),
  createRoute({
    getParentRoute: () => rootRoute,
    path: "/fetch",
    component: lazyRouteComponent(() => import("@/pages/FetchPage"), "FetchPage"),
  }),
  createRoute({
    getParentRoute: () => rootRoute,
    path: "/filter",
    component: lazyRouteComponent(() => import("@/pages/FilterPage"), "FilterPage"),
  }),
  createRoute({
    getParentRoute: () => rootRoute,
    path: "/output",
    validateSearch: validateOutputSearch,
    component: lazyRouteComponent(() => import("@/pages/OutputPage"), "OutputPage"),
  }),
  createRoute({
    getParentRoute: () => rootRoute,
    path: "/charts",
    validateSearch: validateChartSearch,
    component: lazyRouteComponent(() => import("@/pages/ChartsPage"), "ChartsPage"),
  }),
  createRoute({
    getParentRoute: () => rootRoute,
    path: "/sector-indices",
    component: lazyRouteComponent(
      () => import("@/pages/SectorIndicesPage"),
      "SectorIndicesPage",
    ),
  }),
  createRoute({
    getParentRoute: () => rootRoute,
    path: "/scoring-rules",
    component: lazyRouteComponent(() => import("@/pages/ScoringRulesPage"), "ScoringRulesPage"),
  }),
  createRoute({
    getParentRoute: () => rootRoute,
    path: "/utilities",
    component: lazyRouteComponent(() => import("@/pages/UtilitiesPage"), "UtilitiesPage"),
  }),
  createRoute({
    getParentRoute: () => rootRoute,
    path: "/settings",
    component: lazyRouteComponent(() => import("@/pages/SettingsPage"), "SettingsPage"),
  }),
];

const router = createRouter({ routeTree: rootRoute.addChildren(routes) });

declare module "@tanstack/react-router" {
  interface Register {
    router: typeof router;
  }
}

const queryClient = new QueryClient({
  defaultOptions: { queries: { staleTime: 60_000, refetchOnWindowFocus: false } },
});

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  </StrictMode>,
);
