import React from 'react';
import { View, Text, StyleSheet, Dimensions } from 'react-native';
import Svg, { Rect, Defs, LinearGradient as SvgGradient, Stop, Text as SvgText, Line } from 'react-native-svg';

interface DataPoint {
  label: string;
  value: number;
  secondary?: number; // e.g., tips
}

interface EarningsBarChartProps {
  data: DataPoint[];
  height?: number;
  primaryColor?: string;
  secondaryColor?: string;
  formatValue?: (v: number) => string;
}

const { width: SCREEN_WIDTH } = Dimensions.get('window');

export default function EarningsBarChart({
  data,
  height = 180,
  primaryColor = '#EF4444',
  secondaryColor = '#FFD700',
  formatValue = (v) => `$${v.toFixed(0)}`,
}: EarningsBarChartProps) {
  if (!data || data.length === 0) {
    return (
      <View style={[styles.emptyContainer, { height }]}>
        <Text style={styles.emptyText}>No data available</Text>
      </View>
    );
  }

  const chartWidth = SCREEN_WIDTH - 80;
  const chartHeight = height - 10;
  const paddingLeft = 45;
  const paddingTop = 15;
  const paddingBottom = 25;
  const plotHeight = chartHeight - paddingTop - paddingBottom;
  const plotWidth = chartWidth - paddingLeft;

  const maxValue = Math.max(...data.map((d) => d.value + (d.secondary || 0)), 1);
  const barWidth = Math.min(Math.max(plotWidth / data.length - 8, 8), 24);
  const barSpacing = plotWidth / data.length;

  const getBarHeight = (value: number) => (value / maxValue) * plotHeight;
  const getX = (index: number) => paddingLeft + index * barSpacing + (barSpacing - barWidth) / 2;

  // Y-axis labels
  const yTicks = [0, Math.round(maxValue / 2), Math.round(maxValue)];
  const getY = (value: number) => paddingTop + plotHeight - (value / maxValue) * plotHeight;

  return (
    <View style={[styles.container, { height }]}>
      <Svg width={chartWidth} height={chartHeight}>
        <Defs>
          <SvgGradient id="barGrad" x1="0" y1="0" x2="0" y2="1">
            <Stop offset="0%" stopColor={primaryColor} stopOpacity="1" />
            <Stop offset="100%" stopColor={primaryColor} stopOpacity="0.7" />
          </SvgGradient>
          <SvgGradient id="tipGrad" x1="0" y1="0" x2="0" y2="1">
            <Stop offset="0%" stopColor={secondaryColor} stopOpacity="0.9" />
            <Stop offset="100%" stopColor={secondaryColor} stopOpacity="0.6" />
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

        {/* Bars */}
        {data.map((d, i) => {
          const x = getX(i);
          const mainH = getBarHeight(d.value);
          const tipH = d.secondary ? getBarHeight(d.secondary) : 0;
          const mainY = paddingTop + plotHeight - mainH;
          const tipY = mainY - tipH;
          const radius = Math.min(barWidth / 3, 4);

          return (
            <React.Fragment key={`bar-${i}`}>
              {/* Main earnings bar */}
              <Rect
                x={x}
                y={mainY}
                width={barWidth}
                height={mainH}
                rx={radius}
                ry={radius}
                fill="url(#barGrad)"
              />
              {/* Tips overlay on top */}
              {tipH > 0 && (
                <Rect
                  x={x}
                  y={tipY}
                  width={barWidth}
                  height={tipH}
                  rx={radius}
                  ry={radius}
                  fill="url(#tipGrad)"
                />
              )}
              {/* Value label above bar */}
              {d.value > 0 && (
                <SvgText
                  x={x + barWidth / 2}
                  y={(tipH > 0 ? tipY : mainY) - 4}
                  textAnchor="middle"
                  fontSize={9}
                  fontWeight="600"
                  fill="#6B7280"
                >
                  {formatValue(d.value + (d.secondary || 0))}
                </SvgText>
              )}
            </React.Fragment>
          );
        })}

        {/* X-axis labels */}
        {data.map((d, i) => (
          <SvgText
            key={`xlabel-${i}`}
            x={getX(i) + barWidth / 2}
            y={chartHeight - 2}
            textAnchor="middle"
            fontSize={10}
            fill="#9CA3AF"
          >
            {d.label}
          </SvgText>
        ))}
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
