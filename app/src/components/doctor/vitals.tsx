import { StyleSheet, View } from 'react-native';

import { Spark } from '@/components/report/bento';
import { DOC } from '@/components/doctor/ui';
import { AppText } from '@/components/ui/app-text';
import { Colors } from '@/constants/theme';
import type { Vitals } from '@/data/store';

// The four vitals as a 2 x 2 of IDENTICAL tiles (same width, same height,
// same anatomy): name, latest value + unit, change since the last visit, and
// a trend line. Neutral by default; a value outside its safe range turns
// red with a HIGH / LOW tag, so the eye goes only where it should.

const last = (a: number[]) => a[a.length - 1];
const show = (a: number[], digits = 0) => (a.length ? last(a).toFixed(digits) : '—');

function change(a: number[], digits = 0) {
  if (a.length < 2) return a.length ? 'First reading' : 'No reading yet';
  const d = last(a) - a[a.length - 2];
  if (Math.abs(d) < 10 ** -digits / 2) return 'Same as last visit';
  return `${d > 0 ? '▲' : '▼'} ${Math.abs(d).toFixed(digits)} since last visit`;
}

type Tile = { name: string; value: string; unit: string; note: string; data: number[]; flag: 'HIGH' | 'LOW' | null; warnAbove?: number; warnBelow?: number };

function VitalTile({ v }: { v: Tile }) {
  const color = v.flag ? Colors.cherry : Colors.ink;
  return (
    <View style={[styles.tile, v.flag && styles.tileFlag]} accessible accessibilityLabel={`${v.name}: ${v.value} ${v.unit}${v.flag ? `, ${v.flag}` : ''}. ${v.note}`}>
      <View style={styles.top}>
        <AppText variant="caption" color={Colors.inkMuted} numberOfLines={1} style={styles.flex}>
          {v.name}
        </AppText>
        {v.flag && (
          <View style={styles.flag}>
            <AppText variant="overline" color={Colors.onCherry}>
              {v.flag}
            </AppText>
          </View>
        )}
      </View>
      <View style={styles.valueRow}>
        <AppText variant="metric" color={color} numberOfLines={1} adjustsFontSizeToFit minimumFontScale={0.7}>
          {v.value}
        </AppText>
        <AppText variant="caption" color={Colors.inkMuted}>
          {v.unit}
        </AppText>
      </View>
      <AppText variant="caption" color={Colors.inkMuted} numberOfLines={1}>
        {v.note}
      </AppText>
      <View style={styles.spark}>
        <Spark data={v.data} color={v.flag ? Colors.cherry : Colors.plum} height={44} warnAbove={v.warnAbove} warnBelow={v.warnBelow} />
      </View>
    </View>
  );
}

export function DoctorVitals({ vitals }: { vitals: Vitals }) {
  const bpHigh = !!vitals.sys.length && (last(vitals.sys) >= 140 || last(vitals.dia) >= 90);
  const tiles: Tile[] = [
    { name: 'Blood pressure', value: `${show(vitals.sys)}/${show(vitals.dia)}`, unit: 'mmHg', note: change(vitals.sys), data: vitals.sys, flag: bpHigh ? 'HIGH' : null, warnAbove: 140 },
    {
      name: 'Haemoglobin',
      value: show(vitals.hb, 1),
      unit: 'g/dL',
      note: change(vitals.hb, 1),
      data: vitals.hb,
      flag: vitals.hb.length && last(vitals.hb) < 10 ? 'LOW' : null,
      warnBelow: 10,
    },
    {
      name: 'Sugar (random)',
      value: show(vitals.sugar),
      unit: 'mg/dL',
      note: change(vitals.sugar),
      data: vitals.sugar,
      flag: vitals.sugar.length && last(vitals.sugar) >= 140 ? 'HIGH' : null,
      warnAbove: 140,
    },
    { name: 'Weight', value: show(vitals.weight, 1), unit: 'kg', note: change(vitals.weight, 1), data: vitals.weight, flag: null },
  ];
  return (
    <View style={styles.grid}>
      <View style={styles.row}>
        <VitalTile v={tiles[0]} />
        <VitalTile v={tiles[1]} />
      </View>
      <View style={styles.row}>
        <VitalTile v={tiles[2]} />
        <VitalTile v={tiles[3]} />
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  flex: { flex: 1, minWidth: 0 },
  grid: { gap: 10 },
  row: { flexDirection: 'row', gap: 10 },
  tile: {
    flex: 1,
    flexBasis: 0,
    minWidth: 0,
    gap: 2,
    padding: 12,
    borderRadius: DOC.radius,
    borderWidth: 1,
    borderColor: DOC.border,
    backgroundColor: Colors.surface,
  },
  tileFlag: { borderColor: Colors.cherry },
  top: { minHeight: 22, flexDirection: 'row', alignItems: 'center', gap: 6 },
  flag: { paddingHorizontal: 6, paddingVertical: 2, borderRadius: 4, backgroundColor: Colors.cherry },
  valueRow: { flexDirection: 'row', alignItems: 'baseline', gap: 4 },
  spark: { height: 44, marginTop: 4, alignSelf: 'stretch' },
});
