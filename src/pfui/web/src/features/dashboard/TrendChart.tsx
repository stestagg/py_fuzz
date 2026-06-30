import { Colors, Tab, Tabs } from "@blueprintjs/core";
import { CartesianGrid, Line, LineChart, Tooltip, XAxis, YAxis } from "recharts";
import type { TrendMetric, TrendPoint } from "../../protocol/types";
import { MacWindow } from "../../shared/MacWindow";

const METRICS: { key: TrendMetric; label: string }[] = [
  { key: "execs_done", label: "Execs Done" },
  { key: "execs_per_sec", label: "Execs/sec" },
  { key: "corpus_count", label: "Corpus" },
  { key: "edges_found", label: "Edges" },
];

function shortValue(value: number): string {
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}M`;
  if (value >= 1_000) return `${(value / 1_000).toFixed(value >= 10_000 ? 0 : 1)}K`;
  return value < 100 ? value.toFixed(1) : String(Math.round(value));
}

function shortTime(seconds: number): string {
  if (seconds < 60) return `${seconds}s`;
  if (seconds < 3600) return `${Math.round(seconds / 60)}m`;
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.round((seconds % 3600) / 60);
  return minutes ? `${hours}h${minutes}m` : `${hours}h`;
}

export function TrendChart({ points, metric, onMetricChange }: {
  points: TrendPoint[];
  metric: TrendMetric;
  onMetricChange: (metric: TrendMetric) => void;
}) {
  const hasData = points.length > 1;

  return (
    <MacWindow title="Fuzzing trend" decorativeLights>
      <Tabs className="safari-tabs" animate={false} selectedTabId={metric} onChange={(id) => onMetricChange(id as TrendMetric)}>
        {METRICS.map(({ key, label }) => (
          <Tab key={key} id={key} title={`${label}${points.length ? ` · ${shortValue(points[points.length - 1][key])}` : ""}`} />
        ))}
      </Tabs>
      <div className="trend-body">
        {!hasData ? <div className="empty-state">No trend data</div> : (
          <div className="trend-chart" role="img" aria-label={`${metric} over time`}>
              <LineChart
                data={points}
                responsive
                accessibilityLayer
                style={{ width: "100%", height: "100%", minWidth: 0, minHeight: 300 }}
                margin={{ top: 16, right: 24, bottom: 8, left: 12 }}
              >
                <CartesianGrid stroke="currentColor" opacity={0.15} vertical={false} />
                <XAxis dataKey="time" tickFormatter={shortTime} minTickGap={36} tick={{ fill: "currentColor" }} />
                <YAxis tickFormatter={shortValue} width={64} tick={{ fill: "currentColor" }} />
                <Tooltip
                  contentStyle={{ backgroundColor: "Canvas", color: "CanvasText", borderColor: "ButtonBorder" }}
                  labelFormatter={(value) => `Time: ${shortTime(Number(value))}`}
                  formatter={(value) => [shortValue(Number(value)), METRICS.find((item) => item.key === metric)?.label]}
                />
                <Line dataKey={metric} type="monotone" stroke={Colors.BLUE3} strokeWidth={2} dot={false} isAnimationActive={false} />
              </LineChart>
          </div>
        )}
      </div>
    </MacWindow>
  );
}
