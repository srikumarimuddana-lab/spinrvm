import React from 'react';
import { View, Text, StyleSheet, Dimensions } from 'react-native';
import Svg, { Path, Defs, LinearGradient as SvgGradient, Stop, Circle, Line, Text as SvgText } from 'react-native-svg';

interface DataPoint {
  label: string;
  value: number;
}

interface EarningsLineChartProps {
  data: DataPoint[];
  height?: number;
  color?: string;
  showArea?: boolean;
  formatValue?: (v: number) => string;
}

const { width: SCREEN_WIDTH } = Dimensions.get('window');

export default function EarningsLineChart({
  data,
  height = 180,
  color = '#EF4444',
  showArea = true,
  formatValue = (v) => `$${v.toFixed(0)}`,
}: EarningsLineChartProps) {
  if (!data || data.length === 0) {
    return (
      <View style={[styles.emptyContainer, { height }]}>
        <Text style={styles.emptyText}>No data available</Text>
      </View>
    );
  }

  const chartWidth = SCREEN_WIDTH - 80;
  const chartHeight = height - 40; // Leave room for labels
  const paddingLeft = 45;
  const paddingTop = 10;
  const paddingBottom = 25;
  const plotHeight = chartHeight - paddingTop - paddingBottom;
  const plotWidth = chartWidth - paddingLeft;

  const maxValue = Math.max(...data.map((d) => d.value), 1);
  const minValue = 0;
  const valueRange = maxValue - minValue || 1;

  const getX = (index: number) => paddingLeft + (index / Math.max(data.length - 1, 1)) * plotWidth;
  const getY = (value: number) => paddingTop + plotHeight - ((value - minValue) / valueRange) * plotHeight;

  // Build SVG path
  const points = data.map((d, i) => ({ x: getX(i), y: getY(d.value) }));

  let linePath = `M ${points[0].x} ${points[0].y}`;
  for (let i = 1; i < points.length; i++) {
    // Smooth curve using cubic bezier
    const prev = points[i - 1];
    const curr = points[i];
    const cpx = (prev.x + curr.x) / 2;
    linePath += ` C ${cpx} ${prev.y}, ${cpx} ${curr.y}, ${curr.x} ${curr.y}`;
  }

  // Area path (closes the line path to the bottom)
  const areaPath = `${linePath} L ${points[points.length - 1].x} ${paddingTop + plotHeight} L ${points[0].x} ${paddingTop + plotHeight} Z`;

  // Y-axis labels (3 ticks)
  const yTicks = [0, Math.round(maxValue / 2), Math.round(maxValue)];

  return (
    <View style={[styles.container, { height }]}>
      <Svg width={chartWidth} height={chartHeight}>
        <Defs>
          <SvgGradient id="areaGrad" x1="0" y1="0" x2="0" y2="1">
            <Stop offset="0%" stopColor={color} stopOpacity="0.3" />
            <Stop offset="100%" stopColor={color} stopOpacity="0.02" />
          </SvgGradient>
        </Defs>

        {/* Grid lines */}
        {yTicks.map((tick, i) => (
          <Line
            key={`grid-${i}`}
            x1={paddingLeft}
            y1={getY(tick)}
            x2={paddingLeft + plotWidth}
            y2={getY(tick)}
            stroke="#E5E7EB"
            strokeWidth={1}
            strokeDasharray="4,4"
          />
        ))}

        {/* Y-axis labels */}
        {yTicks.map((tick, i) => (
          <SvgText
            key={`ylabel-${i}`}
            x={paddingLeft - 8}
            y={getY(tick) + 4}
            textAnchor="end"
            fontSize={10}
            fill="#9CA3AF"
          >
            {formatValue(tick)}
          </SvgText>
        ))}

        {/* Area fill */}
        {showArea && <Path d={areaPath} fill="url(#areaGrad)" />}

        {/* Line */}
        <Path d={linePath} stroke={color} strokeWidth={2.5} fill="none" strokeLinecap="round" />

        {/* Data points */}
        {points.map((p, i) => (
          <Circle key={`dot-${i}`} cx={p.x} cy={p.y} r={3.5} fill="#fff" stroke={color} strokeWidth={2} />
        ))}

        {/* X-axis labels */}
        {data.map((d, i) => {
          // Show every label if ≤ 7 items, else every other
          if (data.length > 7 && i % 2 !== 0 && i !== data.length - 1) return null;
          return (
            <SvgText
              key={`xlabel-${i}`}
              x={getX(i)}
              y={chartHeight - 2}
              textAnchor="middle"
              fontSize={10}
              fill="#9CA3AF"
            >
              {d.label}
            </SvgText>
          );
        })}
      </Svg>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    alignItems: 'center',
  },
  emptyContainer: {
    justifyContent: 'center',
    alignItems: 'center',
  },
  emptyText: {
    color: '#9CA3AF',
    fontSize: 14,
  },
});
