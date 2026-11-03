import { StyleSheet, View } from 'react-native';

import { AppText } from '@/components/ui/app-text';
import { Colors } from '@/constants/theme';
import { useT } from '@/features/onboarding/context';

// The day at a glance: how many visits are scheduled, how many are done,
// how many are left, and one bar. No single woman is singled out here: the
// list below (or behind the tap) is where she picks who is next. Recomputed
// from the day plan on every change, so ticking a visit updates it at once.

export function VisitProgress({ total, done }: { total: number; done: number }) {
  const t = useT();
  const left = Math.max(0, total - done);
  const share = total ? done / total : 0;
  return (
    <View style={styles.wrap} accessible accessibilityRole="progressbar" accessibilityLabel={t('todayProgress', { done, total })} aria-valuenow={Math.round(share * 100)}>
      <View style={styles.stats}>
        <Stat label={t('todayScheduled')} value={total} />
        <Stat label={t('todayVisited')} value={done} tone={Colors.sage} />
        <Stat label={t('todayLeft')} value={left} tone={left ? Colors.cherry : Colors.sage} />
      </View>
      <View style={styles.track}>
        <View style={[styles.fill, { transform: [{ scaleX: share }] }]} />
      </View>
      <AppText variant="caption" color={total && !left ? Colors.sage : Colors.inkMuted}>
        {total && !left ? t('todayAllDone') : t('todayProgress', { done, total })}
      </AppText>
    </View>
  );
}

function Stat({ label, value, tone = Colors.ink }: { label: string; value: number; tone?: string }) {
  return (
    <View style={styles.stat}>
      <AppText variant="metric" color={tone}>
        {String(value)}
      </AppText>
      <AppText variant="caption" color={Colors.inkMuted} numberOfLines={1}>
        {label}
      </AppText>
    </View>
  );
}

const styles = StyleSheet.create({
  wrap: { gap: 8 },
  stats: { flexDirection: 'row', gap: 8 },
  stat: { flex: 1, minWidth: 0, alignItems: 'center', paddingVertical: 8, borderRadius: 14, backgroundColor: Colors.surface, borderWidth: 1, borderColor: Colors.bandLine },
  track: { height: 8, borderRadius: 4, backgroundColor: Colors.sageTint, overflow: 'hidden' },
  fill: { width: '100%', height: '100%', borderRadius: 4, backgroundColor: Colors.sage, transformOrigin: 'left' },
});
