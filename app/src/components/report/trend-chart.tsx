import { useState } from 'react';
import { View, type LayoutChangeEvent } from 'react-native';
import { LineChart } from 'react-native-gifted-charts';

import { Brand } from '@/constants/brand';

// A vitals trend (react-native-gifted-charts): one smooth line with a soft
// gradient under it, a small dot per visit and a larger one for the latest,
// cherry when the latest value is past its limit. The limit itself (e.g. BP
// 140, Hb 10) is a faint dashed line when it falls inside the range shown.
// No axes or labels: the value and the change are written next to it. It
// fills the width it is given (or measures its parent).

type Props = { data: number[]; color: string; height: number; width?: number; warnAbove?: number; warnBelow?: number };

export function TrendChart({ data, color, height, width: fixed, warnAbove, warnBelow }: Props) {
  const [measured, setMeasured] = useState(0);
  const width = fixed ?? measured;
  const onLayout = (e: LayoutChangeEvent) => {
    const w = Math.floor(e.nativeEvent.layout.width);
    if (w !== measured) setMeasured(w);
  };
  if (data.length < 2) return null;

  const now = data[data.length - 1];
  const hot = (warnAbove !== undefined && now >= warnAbove) || (warnBelow !== undefined && now < warnBelow);
  const limit = warnAbove ?? warnBelow;
  // The range shown: the readings (and the limit when it is close), with room above and below.
  const lo = Math.min(...data, ...(limit !== undefined && near(limit, data) ? [limit] : []));
  const hi = Math.max(...data, ...(limit !== undefined && near(limit, data) ? [limit] : []));
  const pad = Math.max((hi - lo) * 0.18, Math.abs(hi) * 0.01, 0.5);
  const base = lo - pad;
  const top = hi + pad - base;
  const points = data.map((v, i) => {
    const last = i === data.length - 1;
    return {
      value: v - base,
      dataPointColor: last && hot ? Brand.cherry : color,
      dataPointRadius: last ? 4.5 : 2.5,
    };
  });
  const inner = Math.max(0, width - 12);
  const showLimit = limit !== undefined && limit - base > 0 && limit - base < top;

  return (
    <View style={{ height, alignSelf: fixed ? undefined : 'stretch' }} onLayout={fixed ? undefined : onLayout} aria-hidden>
      {width > 0 && (
        <LineChart
          data={points}
          width={inner}
          height={height - 6}
          maxValue={top}
          noOfSections={1}
          spacing={inner / (data.length - 1)}
          initialSpacing={6}
          endSpacing={0}
          curved
          curvature={0.2}
          areaChart
          color={color}
          thickness={2.5}
          startFillColor={color}
          endFillColor={color}
          startOpacity={0.22}
          endOpacity={0.02}
          dataPointsColor={color}
          hideRules
          hideYAxisText
          yAxisLabelWidth={0}
          yAxisThickness={0}
          xAxisThickness={0}
          disableScroll
          adjustToWidth={false}
          overflowTop={6}
          showReferenceLine1={showLimit}
          referenceLine1Position={showLimit ? (limit as number) - base : 0}
          referenceLine1Config={{ color: Brand.cherry, thickness: 1, dashWidth: 4, dashGap: 4, type: 'dashed' }}
        />
      )}
    </View>
  );
}

/** A limit worth showing: within a third of the readings' own spread of them. */
function near(limit: number, data: number[]) {
  const lo = Math.min(...data);
  const hi = Math.max(...data);
  const spread = Math.max(hi - lo, Math.abs(hi) * 0.05);
  return limit >= lo - spread && limit <= hi + spread;
}
