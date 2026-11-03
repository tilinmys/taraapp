import { Fragment, type ReactNode } from 'react';
import { View } from 'react-native';

import { BentoCell, BentoRow, Spark } from '@/components/report/bento';
import { TONES } from '@/components/system/emblem';
import { AppText } from '@/components/ui/app-text';
import { Brand } from '@/constants/brand';
import { useContentWidth } from '@/hooks/use-content-width';
import type { Vitals } from '@/data/store';

// The four vitals a PHC doctor scans first, as a 2 x 2 bento. Each cell:
// the latest value, the change since the last visit in words, and a
// sparkline whose last dot turns cherry when the value crosses its limit.
// Tablets show all four in one row.

const last = (a: number[]) => a[a.length - 1];
/** Latest value as text; a dash until the first reading exists. */
const show = (a: number[], digits = 0) => (a.length ? last(a).toFixed(digits) : '—');
const prev = (a: number[]) => (a.length > 1 ? a[a.length - 2] : undefined);

function delta(a: number[], digits = 0, unit = '') {
  const p = prev(a);
  if (p === undefined) return 'First reading';
  const d = last(a) - p;
  if (Math.abs(d) < 10 ** -digits / 2) return 'Same as last visit';
  return `${d > 0 ? 'Up' : 'Down'} ${Math.abs(d).toFixed(digits)}${unit} since last visit`;
}

const Column = ({ children }: { children: ReactNode }) => <View style={{ gap: 12 }}>{children}</View>;

export function VitalsGrid({ vitals }: { vitals: Vitals }) {
  const highBp = last(vitals.sys) >= 140 || last(vitals.dia) >= 90;
  const lowHb = last(vitals.hb) < 10;
  const highSugar = last(vitals.sugar) >= 140;
  const { isTablet } = useContentWidth();
  // Phones: two rows of two. Tablets: one row of four.
  const Outer = isTablet ? BentoRow : Column;
  const Split = isTablet ? Fragment : BentoRow;
  return (
    <View style={{ gap: 12 }}>
      <Outer>
        <Split>
          <BentoCell
            tone={highBp ? 'alert' : 'cherry'}
            icon="pulse"
            label="Blood pressure"
            value={`${show(vitals.sys)}/${show(vitals.dia)}`}
            unit="mmHg"
            note={highBp ? `High · ${delta(vitals.sys)}` : delta(vitals.sys)}
          >
            <Spark data={vitals.sys} color={TONES.cherry.fg} warnAbove={140} />
          </BentoCell>
          <BentoCell
            tone={lowHb ? 'alert' : 'plum'}
            icon="drop"
            label="Haemoglobin"
            value={show(vitals.hb, 1)}
            unit="g/dL"
            note={lowHb ? `Low · ${delta(vitals.hb, 1)}` : delta(vitals.hb, 1)}
          >
            <Spark data={vitals.hb} color={TONES.plum.fg} warnBelow={10} />
          </BentoCell>
        </Split>
        <Split>
          <BentoCell
            tone={highSugar ? 'alert' : 'pumpkin'}
            icon="thermometer"
            label="Sugar (random)"
            value={show(vitals.sugar)}
            unit="mg/dL"
            note={delta(vitals.sugar)}
          >
            <Spark data={vitals.sugar} color={TONES.pumpkin.fg} warnAbove={140} />
          </BentoCell>
          <BentoCell
            tone="sage"
            icon="scales"
            label="Weight"
            value={show(vitals.weight, 1)}
            unit="kg"
            note={delta(vitals.weight, 1, ' kg')}
          >
            <Spark data={vitals.weight} color={TONES.sage.fg} />
          </BentoCell>
        </Split>
      </Outer>
      <AppText variant="caption" color={Brand.inkMuted}>
        {`Trend over the last ${vitals.sys.length} visits. A cherry dot means outside the safe range.`}
      </AppText>
    </View>
  );
}
