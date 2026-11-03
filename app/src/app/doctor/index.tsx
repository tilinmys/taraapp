import { router } from 'expo-router';
import { Pressable, StyleSheet, View } from 'react-native';

import { CaseRow, Empty, LinkRow, SectionHead, StatTile, TRIAGE, Tag } from '@/components/doctor/ui';
import { Icon } from '@/components/system/icon';
import { Screen } from '@/components/system/screen';
import { AppText } from '@/components/ui/app-text';
import { Colors, Haptic } from '@/constants/theme';
import { ME_DOCTOR, dayStart, fmtTime, inbox, sameDay, slotEnd, useNetwork, useNow } from '@/data/care-network';
import { byTriage, reviewOf, triageOf, useDoctorReview } from '@/data/doctor-review';
import { riskOf, useMothers } from '@/data/store';

// Doctor home: ONLY what needs her, so she can act in seconds.
//   Tiles      three rectangles that open their list: Urgent, New reports,
//              Visits today (a number is never a dead end)
//   Needs you  urgent cases, then unread reports that found something.
//              Nothing else: all-normal reports wait on Reports; referred
//              and reviewed cases live in All cases
//   Recent     the last cases she opened, with where each one stands: her
//              memory, so she never has to remember what she already did
//   More       All cases · Reports from ASHAs · My visit times
// No filters and no "Review next case" button: the list says what is
// waiting, and which one she opens next is her call.

const MAX_HOME = 5;

export default function DoctorHome() {
  const mothers = useMothers();
  const review = useDoctorReview();
  const net = useNetwork();
  const now = useNow();
  const unread = inbox(net, { kind: 'staff', id: ME_DOCTOR }).filter((n) => !n.read).length;

  const sorted = byTriage(mothers, review);
  const urgent = sorted.filter((m) => triageOf(m, review) === 'urgent');
  // Every report she has not read yet (the tile and the Reports page agree).
  const unreadReports = mothers.filter((m) => m.reports.length && !reviewOf(m, review));
  // Home shows only what needs her: urgent cases, and new reports that FOUND
  // something. An all-normal report waits on the Reports page.
  const fresh = sorted.filter((m) => triageOf(m, review) === 'new' && riskOf(m).hits.length > 0);
  const needs = [...urgent, ...fresh];
  const recent = review.recent.map((id) => mothers.find((m) => m.id === id)).filter((m) => !!m);

  const todays = net.slots
    .filter((x) => x.doctorId === ME_DOCTOR && x.status === 'booked' && sameDay(x.start, now) && slotEnd(x) > now)
    .sort((a, b) => a.start - b.start);
  const week = net.slots.filter((x) => x.doctorId === ME_DOCTOR && x.start > now && x.start < now + 7 * 86_400_000);
  const openSlots = week.filter((x) => x.status === 'open').length;
  const reports = mothers.reduce((n, m) => n + m.reports.length, 0);

  const open = (id: string) => router.push({ pathname: '/doctor/case/[id]', params: { id } });

  return (
    <Screen
      band
      eyebrow="Dr. Anand · Hosahalli PHC"
      title="Today"
      compactTitle="Today"
      right={{ label: 'Profile', icon: 'profile', onPress: () => router.push('/profile') }}
      rightExtra={{ label: 'Notifications', icon: 'bell', badge: unread, onPress: () => router.push('/doctor/inbox') }}
      accessory={
        <View style={styles.stats}>
          <StatTile label="Urgent" value={urgent.length} tone="urgent" onPress={() => router.push({ pathname: '/doctor/cases', params: { show: 'urgent' } })} />
          <StatTile label="New reports" value={unreadReports.length} tone="new" onPress={() => router.push('/doctor/reports')} />
          <StatTile
            label="Visits today"
            value={todays.length}
            onPress={() => router.push({ pathname: '/doctor/slots', params: { day: String(dayStart(0, now)) } })}
          />
        </View>
      }
    >
      <SectionHead
        title="Needs you"
        count={needs.length}
        link={needs.length > MAX_HOME ? { label: 'See all', onPress: () => router.push('/doctor/cases') } : undefined}
      />
      {needs.length === 0 ? (
        <Empty text="Nothing is waiting. Every urgent case is referred and every report reviewed." />
      ) : (
        <View style={styles.list}>
          {needs.slice(0, MAX_HOME).map((m) => (
            <CaseRow key={m.id} m={m} onPress={open} />
          ))}
        </View>
      )}

      {recent.length > 0 && (
        <>
          <SectionHead title="Recently opened" />
          <View style={styles.recent}>
            {recent.map((m, i) => (
              <Pressable
                key={m.id}
                accessibilityRole="button"
                accessibilityLabel={`${m.name}: ${TRIAGE[triageOf(m, review)].label}. Opens the case.`}
                onPress={() => {
                  Haptic.light();
                  open(m.id);
                }}
                style={({ pressed }) => [styles.recentRow, i > 0 && styles.recentLine, pressed && styles.pressed]}
              >
                <AppText variant="bodyStrong" numberOfLines={1} style={styles.flex}>
                  {m.name}
                </AppText>
                <Tag triage={triageOf(m, review)} />
                <Icon name="forward" size={16} color={Colors.inkMuted} weight="bold" />
              </Pressable>
            ))}
          </View>
        </>
      )}

      <SectionHead title="More" />
      <View style={styles.list}>
        <LinkRow icon="team" title="All cases" line={`${mothers.length} mothers, grouped by status`} onPress={() => router.push('/doctor/cases')} />
        <LinkRow
          icon="clipboard"
          title="Reports from ASHAs"
          line={unreadReports.length ? `${unreadReports.length} not reviewed · ${reports} on file` : `${reports} on file, all reviewed`}
          onPress={() => router.push('/doctor/reports')}
        />
        <LinkRow
          icon="calendar"
          title="My visit times"
          line={
            todays[0]
              ? `First visit today ${fmtTime(todays[0].start)} · ${openSlots} free this week`
              : openSlots
                ? `${openSlots} free this week`
                : 'Nothing open: one tap opens your usual week'
          }
          onPress={() => router.push('/doctor/slots')}
        />
      </View>
    </Screen>
  );
}

const styles = StyleSheet.create({
  flex: { flex: 1, minWidth: 0 },
  pressed: { opacity: 0.6 },
  stats: { flexDirection: 'row', gap: 10 },
  list: { gap: 10 },
  recent: { borderRadius: 10, borderWidth: 1, borderColor: Colors.bandLine, backgroundColor: Colors.surface, paddingHorizontal: 14 },
  recentRow: { minHeight: 52, flexDirection: 'row', alignItems: 'center', gap: 10 },
  recentLine: { borderTopWidth: 1, borderTopColor: Colors.hairline },
});
