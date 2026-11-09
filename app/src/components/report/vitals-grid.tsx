import { Fragment, type ReactNode } from 'react';
import { View } from 'react-native';

import { BentoCell, BentoRow, Spark } from '@/components/report/bento';
import { TONES } from '@/components/system/emblem';
import { AppText } from '@/components/ui/app-text';
import { Brand } from '@/constants/brand';
import { useContentWidth } from '@/hooks/use-content-width';
import type { Vitals } from '@/data/store';
import { useT } from '@/features/onboarding/context';

// The four vitals a PHC doctor scans first, as a 2 x 2 bento. Each cell:
// the latest value, the change since the last visit in words, and a
// sparkline whose last dot turns cherry when the value crosses its limit.
// Tablets show all four in one row.

const last = (a: number[]) => a[a.length - 1];
/** Latest value as text; a dash until the first reading exists. */
const show = (a: number[], digits = 0) => (a.length ? last(a).toFixed(digits) : '—');
const prev = (a: number[]) => (a.length > 1 ? a[a.length - 2] : undefined);

type T = ReturnType<typeof useT>;
function delta(t: T, a: number[], digits = 0, unit = '') {
  const p = prev(a);
  if (p === undefined) return t('vgFirst');
  const d = last(a) - p;
  if (Math.abs(d) < 10 ** -digits / 2) return t('vgSame');
  return t(d > 0 ? 'vgUp' : 'vgDown', { d: `${Math.abs(d).toFixed(digits)}${unit}` });
}

const Column = ({ children }: { children: ReactNode }) => <View style={{ gap: 8 }}>{children}</View>;
const Row = ({ children }: { children: ReactNode }) => <BentoRow style={{ gap: 8 }}>{children}</BentoRow>;

/** `compact` (default: on phones) keeps the 2 x 2 grid short enough to see with the report above it. */
export function VitalsGrid({ vitals, compact: compactProp }: { vitals: Vitals; compact?: boolean }) {
  const t = useT();
  const highBp = last(vitals.sys) >= 140 || last(vitals.dia) >= 90;
  const lowHb = last(vitals.hb) < 10;
  const highSugar = last(vitals.sugar) >= 140;
  const { isTablet } = useContentWidth();
  const compact = compactProp ?? !isTablet;
  // Phones: two rows of two. Tablets: one row of four.
  const Outer = isTablet ? Row : Column;
  const Split = isTablet ? Fragment : Row;
  return (
    <View style={{ gap: 8 }}>
      <Outer>
        <Split>
          <BentoCell
            compact={compact}
            tone={highBp ? 'alert' : 'cherry'}
            icon="pulse"
            label={t('vgBp')}
            value={`${show(vitals.sys)}/${show(vitals.dia)}`}
            unit="mmHg"
            note={highBp ? `${t('vgHigh')} · ${delta(t, vitals.sys)}` : delta(t, vitals.sys)}
          >
            <Spark compact={compact} data={vitals.sys} color={TONES.cherry.fg} warnAbove={140} />
          </BentoCell>
          <BentoCell
            compact={compact}
            tone={lowHb ? 'alert' : 'plum'}
            icon="drop"
            label={t('vgHb')}
            value={show(vitals.hb, 1)}
            unit="g/dL"
            note={lowHb ? `${t('vgLow')} · ${delta(t, vitals.hb, 1)}` : delta(t, vitals.hb, 1)}
          >
            <Spark compact={compact} data={vitals.hb} color={TONES.plum.fg} warnBelow={10} />
          </BentoCell>
        </Split>
        <Split>
          <BentoCell
            compact={compact}
            tone={highSugar ? 'alert' : 'pumpkin'}
            icon="thermometer"
            label={t('vgSugar')}
            value={show(vitals.sugar)}
            unit="mg/dL"
            note={delta(t, vitals.sugar)}
          >
            <Spark compact={compact} data={vitals.sugar} color={TONES.pumpkin.fg} warnAbove={140} />
          </BentoCell>
          <BentoCell
            compact={compact}
            tone="sage"
            icon="scales"
            label={t('vgWeight')}
            value={show(vitals.weight, 1)}
            unit="kg"
            note={delta(t, vitals.weight, 1, ' kg')}
          >
            <Spark compact={compact} data={vitals.weight} color={TONES.sage.fg} />
          </BentoCell>
        </Split>
      </Outer>
      <AppText variant="caption" color={Brand.inkMuted}>
        {t('vgTrend', { n: vitals.sys.length })}
      </AppText>
    </View>
  );
}
