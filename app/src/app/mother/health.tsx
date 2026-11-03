import { Linking, StyleSheet, View } from 'react-native';

import { goBack } from '@/lib/nav';
import { MissingMother } from '@/components/mother/missing-mother';
import { AppIcon, type AppIconName } from '@/components/system/app-icon';
import { Rise } from '@/components/system/rise';
import { Screen } from '@/components/system/screen';
import { AppText } from '@/components/ui/app-text';
import { Colors, Haptic } from '@/constants/theme';
import { ashaForZone } from '@/data/care-network';
import { useMother } from '@/data/store';
import { useOnboarding, useT } from '@/features/onboarding/context';
import { relLabel } from '@/lib/day-label';

// Mother: "My health". Her checkup readings, as plain numbers she can show
// anyone. Deliberately calm: every tile has the same soft colour, there are
// no high / low words, no red and no warning icons. Judging the numbers is
// the ASHA's and the doctor's job (their screens flag risk); here she only
// sees her readings, who took them, and one button to call her ASHA.

type Reading = { icon: AppIconName; label: string; value: string; unit: string; before?: string };

const last = (a: number[]) => a[a.length - 1];
const prev = (a: number[]) => a[a.length - 2];
const fmt = (n: number | undefined, d = 0) => (n === undefined || Number.isNaN(n) ? undefined : d ? n.toFixed(d) : String(Math.round(n)));

export default function MyHealth() {
  const { demoMother, language } = useOnboarding();
  const mother = useMother(demoMother);
  const t = useT();
  if (!mother) return <MissingMother />;
  const v = mother.vitals;
  const asha = ashaForZone(mother.zoneId);
  const bp = fmt(last(v.sys)) && fmt(last(v.dia)) ? `${fmt(last(v.sys))}/${fmt(last(v.dia))}` : undefined;
  const bpBefore = fmt(prev(v.sys)) && fmt(prev(v.dia)) ? `${fmt(prev(v.sys))}/${fmt(prev(v.dia))}` : undefined;
  const readings: Reading[] = [
    { icon: 'scan', label: t('vBp'), value: bp ?? '—', unit: 'mmHg', before: bpBefore },
    { icon: 'water', label: t('vHb'), value: fmt(last(v.hb), 1) ?? '—', unit: 'g/dL', before: fmt(prev(v.hb), 1) },
    { icon: 'food', label: t('vSugar'), value: fmt(last(v.sugar)) ?? '—', unit: 'mg/dL', before: fmt(prev(v.sugar)) },
    { icon: 'weight', label: t('vWeight'), value: fmt(last(v.weight), 1) ?? '—', unit: 'kg', before: fmt(prev(v.weight), 1) },
  ];

  return (
    <Screen
      title={t('myHealth')}
      titleVariant="greeting"
      compactTitle={t('myHealth')}
      back={{ label: t('back'), onPress: () => goBack('/mother') }}
      cta={
        asha
          ? {
              label: t('callHer', { name: asha.name }),
              enabled: true,
              icon: 'phone',
              haptic: 'medium',
              context: t('unwell'),
              onPress: () => {
                Haptic.medium();
                Linking.openURL(`tel:${asha.phone}`).catch(() => {});
              },
            }
          : null
      }
    >
      <Rise index={0}>
        <View style={styles.who}>
          <AppIcon name="ashaWorker" size={48} />
          <View style={styles.flex}>
            <AppText variant="bodyStrong">{t('lastCheckup', { when: relLabel(mother.lastCheck, language) })}</AppText>
            <AppText variant="caption" color={Colors.inkMuted}>
              {t('checkedBy', { asha: mother.asha })}
            </AppText>
          </View>
        </View>
      </Rise>

      <Rise index={1} style={styles.grid}>
        {readings.map((r) => (
          <View key={r.label} style={styles.cell} accessible accessibilityLabel={`${r.label}: ${r.value} ${r.unit}${r.before ? `. Last time ${r.before}` : ''}`}>
            <View style={styles.cellTop}>
              <View style={styles.well}>
                <AppIcon name={r.icon} size={30} />
              </View>
              <AppText variant="label" color={Colors.inkMuted} style={styles.flex} numberOfLines={2}>
                {r.label}
              </AppText>
            </View>
            <AppText variant="metric">{r.value}</AppText>
            <AppText variant="caption" color={Colors.inkMuted}>
              {r.before ? t('lastTime', { unit: r.unit, v: r.before }) : r.unit}
            </AppText>
          </View>
        ))}
      </Rise>

      <Rise index={2}>
        <View style={styles.note}>
          <AppIcon name="tip" size={36} />
          <AppText variant="body" color={Colors.ink} style={styles.flex}>
            {t('healthNote', { asha: mother.asha, when: relLabel(mother.nextVisit, language) })}
          </AppText>
        </View>
      </Rise>
    </Screen>
  );
}

const styles = StyleSheet.create({
  flex: { flex: 1, minWidth: 0 },
  who: { flexDirection: 'row', alignItems: 'center', gap: 12, padding: 14, borderRadius: 20, backgroundColor: Colors.shell },
  // Two equal columns; every tile the same calm colour.
  grid: { flexDirection: 'row', flexWrap: 'wrap', gap: 16 },
  cell: { width: '47%', flexGrow: 1, gap: 4, padding: 14, borderRadius: 20, borderWidth: 1, borderColor: Colors.bandLine, backgroundColor: Colors.surface },
  cellTop: { flexDirection: 'row', alignItems: 'center', gap: 8, marginBottom: 4 },
  well: { width: 44, height: 44, borderRadius: 14, alignItems: 'center', justifyContent: 'center', backgroundColor: Colors.sageTint },
  note: { flexDirection: 'row', alignItems: 'center', gap: 12, padding: 14, borderRadius: 20, backgroundColor: Colors.sageTint },
});
