import { router } from 'expo-router';
import { Pressable, StyleSheet, View } from 'react-native';

import { DOC, Empty, SectionHead, Tag } from '@/components/doctor/ui';
import { Icon } from '@/components/system/icon';
import { Screen } from '@/components/system/screen';
import { AppText } from '@/components/ui/app-text';
import { Colors, Haptic } from '@/constants/theme';
import { fmtWhen } from '@/data/care-network';
import { reviewOf, useDoctorReview } from '@/data/doctor-review';
import { RULES } from '@/data/mockScreening';
import { useMothers, type CheckInReport, type MotherRecord } from '@/data/store';
import { goBack } from '@/lib/nav';

// Reports from ASHAs: every Sakhi check-in an ASHA has filed, the way a
// doctor works through results:
//   Not reviewed   the latest report of each mother she has not read yet
//   Reviewed       latest reports she has read (when, so she remembers)
//   Earlier        older reports, for the history
// A row says who, which ASHA, when, and the finding in one line (risk and
// rules fired). Tap: the case opens on its Report section.

type Item = { m: MotherRecord; r: CheckInReport; latest: boolean };

function finding(r: CheckInReport) {
  if (!r.rules.length) return { text: 'All answers normal', color: Colors.sage };
  const top = RULES[r.rules[0] as keyof typeof RULES];
  const n = `${r.rules.length} rule${r.rules.length === 1 ? '' : 's'}`;
  return r.high ? { text: `High risk · ${top?.title ?? n} · ${n}`, color: Colors.cherry } : { text: `Watch · ${n}`, color: Colors.pumpkinInk };
}

function ReportRow({ item, reviewedAt, onOpen }: { item: Item; reviewedAt?: number; onOpen: () => void }) {
  const { m, r, latest } = item;
  const f = finding(r);
  const isNew = latest && !reviewedAt;
  return (
    <Pressable
      accessibilityRole="button"
      accessibilityLabel={`${m.name}. Check-in by ASHA ${r.asha}, ${r.when}. ${f.text}. ${isNew ? 'Not reviewed.' : ''} Opens the report.`}
      onPress={() => {
        Haptic.light();
        onOpen();
      }}
      style={({ pressed }) => [styles.row, pressed && styles.pressed]}
    >
      <View style={[styles.bar, { backgroundColor: isNew ? '#1D4E89' : Colors.bandLine }]} />
      <View style={styles.body}>
        <View style={styles.top}>
          <AppText variant="bodyStrong" numberOfLines={1} style={styles.flex}>
            {m.name}
          </AppText>
          {latest && <Tag triage={isNew ? 'new' : 'done'} label={isNew ? 'New' : 'Reviewed'} />}
        </View>
        <AppText variant="caption" color={Colors.inkMuted} numberOfLines={1}>
          {`Sakhi check-in · ASHA ${r.asha} · ${r.when}`}
        </AppText>
        <AppText variant="caption" color={f.color} numberOfLines={2}>
          {reviewedAt ? `${f.text} · read ${fmtWhen(reviewedAt)}` : f.text}
        </AppText>
      </View>
      <Icon name="forward" size={18} color={Colors.inkMuted} weight="bold" />
    </Pressable>
  );
}

export default function Reports() {
  const mothers = useMothers();
  const review = useDoctorReview();
  const items: Item[] = mothers.flatMap((m) => m.reports.map((r, i) => ({ m, r, latest: i === 0 })));
  const latest = items.filter((x) => x.latest);
  // High risk first inside each group: those are the ones to read first.
  const rank = (x: Item) => (x.r.high ? 0 : x.r.rules.length ? 1 : 2);
  const fresh = latest.filter((x) => !reviewOf(x.m, review)).sort((a, b) => rank(a) - rank(b) || a.m.name.localeCompare(b.m.name));
  const read = latest.filter((x) => reviewOf(x.m, review)).sort((a, b) => (reviewOf(b.m, review)?.at ?? 0) - (reviewOf(a.m, review)?.at ?? 0));
  const earlier = items.filter((x) => !x.latest);
  const open = (id: string) => router.push({ pathname: '/doctor/case/[id]', params: { id, section: 'report' } });

  return (
    <Screen title="Reports from ASHAs" compactTitle="Reports" back={{ label: 'Home', onPress: () => goBack('/doctor') }}>
      <AppText variant="caption" color={Colors.inkMuted}>
        Every check-in your ASHAs file with Sakhi. Mark a report reviewed on its case page; a newer report shows here as new again.
      </AppText>

      <SectionHead title="Not reviewed" count={fresh.length} />
      {fresh.length === 0 ? (
        <Empty text="You have read every report." />
      ) : (
        <View style={styles.list}>
          {fresh.map((x) => (
            <ReportRow key={x.r.id} item={x} onOpen={() => open(x.m.id)} />
          ))}
        </View>
      )}

      {read.length > 0 && (
        <>
          <SectionHead title="Reviewed" count={read.length} />
          <View style={styles.list}>
            {read.map((x) => (
              <ReportRow key={x.r.id} item={x} reviewedAt={reviewOf(x.m, review)?.at} onOpen={() => open(x.m.id)} />
            ))}
          </View>
        </>
      )}

      {earlier.length > 0 && (
        <>
          <SectionHead title="Earlier reports" count={earlier.length} />
          <View style={styles.list}>
            {earlier.map((x) => (
              <ReportRow key={x.r.id} item={x} onOpen={() => open(x.m.id)} />
            ))}
          </View>
        </>
      )}
    </Screen>
  );
}

const styles = StyleSheet.create({
  flex: { flex: 1, minWidth: 0 },
  pressed: { opacity: 0.6 },
  list: { gap: 10 },
  row: {
    minHeight: 84,
    flexDirection: 'row',
    alignItems: 'center',
    gap: 12,
    paddingRight: 14,
    borderRadius: DOC.radius,
    borderWidth: 1,
    borderColor: DOC.border,
    backgroundColor: Colors.surface,
    overflow: 'hidden',
  },
  bar: { alignSelf: 'stretch', width: 4 },
  body: { flex: 1, minWidth: 0, gap: 2, paddingVertical: 12 },
  top: { flexDirection: 'row', alignItems: 'center', gap: 8 },
});
