/**
 * /charts?view=…&symbols=…&cols=… — the Output Action menu's target.
 *
 * One route, several views, exactly as the Streamlit page worked; the URL
 * contract is unchanged so existing links keep working.
 *
 * Each view is lazily imported so a tab only downloads the charting library it
 * actually needs — the price view pulls Lightweight Charts, the rest pull
 * ECharts, and neither pays for the other.
 */
import { Suspense, lazy } from "react";
import { getRouteApi } from "@tanstack/react-router";
import { EmptyState } from "@/components/ui";

const route = getRouteApi("/charts");

const PriceView = lazy(() => import("@/pages/PriceView").then((m) => ({ default: m.PriceView })));
const FundamentalsBarView = lazy(() =>
  import("@/pages/FundamentalsBarView").then((m) => ({ default: m.FundamentalsBarView })),
);
const PeriodLineView = lazy(() =>
  import("@/pages/PeriodLineView").then((m) => ({ default: m.PeriodLineView })),
);
const RadarView = lazy(() => import("@/pages/RadarView").then((m) => ({ default: m.RadarView })));
const ValuationRangeView = lazy(() =>
  import("@/pages/ValuationRangeView").then((m) => ({ default: m.ValuationRangeView })),
);
const HeatmapView = lazy(() =>
  import("@/pages/HeatmapView").then((m) => ({ default: m.HeatmapView })),
);
const NewsView = lazy(() => import("@/pages/NewsView").then((m) => ({ default: m.NewsView })));
const FilterFailView = lazy(() =>
  import("@/pages/FilterFailView").then((m) => ({ default: m.FilterFailView })),
);

export function ChartsPage() {
  const { view, symbols, cols } = route.useSearch();

  if (symbols.length === 0) {
    return (
      <EmptyState
        title="No symbols in the link"
        detail="Chart views open from the Output page's Action menu with a selection of rows."
      />
    );
  }

  const body = () => {
    switch (view) {
      case "price":
        return <PriceView symbols={symbols} />;
      case "fundamentals_bar":
        return <FundamentalsBarView symbols={symbols} cols={cols} />;
      case "fundamentals_line":
        return <PeriodLineView symbols={symbols} cols={cols} kind="fundamentals" />;
      case "dividend_line":
        return <PeriodLineView symbols={symbols} cols={cols} kind="dividends" />;
      case "radar":
        return <RadarView symbols={symbols} />;
      case "valuation_range":
        return <ValuationRangeView symbols={symbols} />;
      case "heatmap":
        return <HeatmapView symbols={symbols} cols={cols} kind="metrics" />;
      case "scores_heatmap":
        return <HeatmapView symbols={symbols} cols={cols} kind="scores" />;
      case "news":
        return <NewsView symbols={symbols} />;
      case "filter_fail":
        return <FilterFailView symbols={symbols} />;
      default:
        return <EmptyState title={`Unknown chart view "${view}"`} />;
    }
  };

  return <Suspense fallback={<EmptyState title="Loading chart…" />}>{body()}</Suspense>;
}
