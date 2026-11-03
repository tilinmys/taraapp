import { useLocalSearchParams } from 'expo-router';
import { useSharedValue } from 'react-native-reanimated';
import { useState } from 'react';
import { Pressable, StyleSheet, View } from 'react-native';

import { goBack } from '@/lib/nav';
import { VisitThread } from '@/components/care/visit-thread';
import { Icon } from '@/components/system/icon';
import { Screen } from '@/components/system/screen';
import { BottomSheet } from '@/components/ui/bottom-sheet';
import { AppText } from '@/components/ui/app-text';
import { Colors, Haptic } from '@/constants/theme';
import {
  ME_DOCTOR,
  PLAN_DAYS,
  SESSIONS,
  SLOT_LENGTHS,
  WEEKDAYS,
  ZONES,
  dayStart,
  fmtDay,
  fmtTime,
  isActive,
  network,
  planTimes,
  sameDay,
  staff,
  usualDays,
  useNetwork,
  useNow,
  type Session,
  type Slot,
} from '@/data/care-network';
import { useMothers } from '@/data/store';

// Doctor: visit times in one or two taps. Doctors are short of time, so
// nothing here needs typing and nothing needs a second "lock" step:
//   Usual week   his OPD days + session + visit length, remembered. The
//                sticky button opens all of them for the next 7 days: ONE tap
//                on a normal week. Changing a day or session is one tap more.
//   One day      tap a day, then Morning / Afternoon / Full day: opens it
//                (2 taps). "Away this day" closes its free times.
//   The day      every time as an equal tile: Free (tap x to close) or the
//                mother's name. Booked times cannot be closed here.
// Every open goes straight to the ASHAs and mothers (one message per ASHA).

const SESSION_IDS: Session[] = ['morning', 'afternoon', 'both'];

export default function DoctorSlots() {
  const net = useNetwork();
  const now = useNow();
  const mothers = useMothers();
  const me = staff(ME_DOCTOR);
  const { day: dayParam } = useLocalSearchParams<{ day?: string }>();
  const days = Array.from({ length: PLAN_DAYS + 1 }, (_, i) => dayStart(i));
  const [day, setDay] = useState(dayParam ? Number(dayParam) : days[1]);
  const [note, setNote] = useState<string | null>(null);
  const usual = net.usual[ME_DOCTOR] ?? { weekdays: [1, 3, 5], session: 'morning' as Session, minutes: 30 };

  const mine = net.slots.filter((s) => s.doctorId === ME_DOCTOR && s.status !== 'closed' && s.start > now - 60 * 60_000);
  const ofDay = mine.filter((s) => sameDay(s.start, day)).sort((a, b) => a.start - b.start);
  const free = ofDay.filter((s) => s.status === 'open');
  const ashaNames = [...new Set(ZONES.filter((z) => z.phcId === me?.phcId).map((z) => staff(z.ashaId)?.name))].join(' and ');
  const weekDays = usualDays(usual, now);
  const preview = planTimes(ME_DOCTOR, weekDays, usual.session, usual.minutes, net);
  const bookingOf = (s: Slot) => net.bookings.find((x) => x.slotId === s.id && isActive(x));
  const motherOf = (s: Slot) => mothers.find((m) => m.id === bookingOf(s)?.motherId)?.name;
  const sheet = useSharedValue(0);
  const [threadId, setThreadId] = useState<string | null>(null);
  const threadMother = mothers.find((m) => m.id === net.bookings.find((b) => b.id === threadId)?.motherId)?.name ?? '';

  const setUsual = (next: Partial<typeof usual>) => {
    Haptic.light();
    network.setUsual(ME_DOCTOR, { ...usual, ...next });
    setNote(null);
  };
  const openDay = (session: Session) => {
    const n = network.openTimes(ME_DOCTOR, [day], session, usual.minutes);
    Haptic[n ? 'success' : 'warning']();
    setNote(n ? `${fmtDay(day)}: ${n} times open. ${ashaNames} told.` : `${fmtDay(day)} ${SESSIONS[session].label.toLowerCase()} is already open.`);
  };

  return (
    <Screen
      band
      eyebrow={`${me?.name ?? ''} · Hosahalli PHC`}
      title="My visit times"
      compactTitle="My visit times"
      back={{ label: 'Home', onPress: () => goBack('/doctor') }}
      sheetProgress={sheet}
      overlay={
        <BottomSheet open={!!threadId} onClose={() => setThreadId(null)} progress={sheet} title="Messages about this visit" closeLabel="Close">
          {threadId && <VisitThread bookingId={threadId} me="doctor" myId={ME_DOCTOR} motherName={threadMother} />}
        </BottomSheet>
      }
      cta={{
        label: preview.length ? 'Open my usual week' : 'Usual week is open',
        enabled: preview.length > 0,
        icon: 'calendar',
        haptic: 'success',
        context: preview.length ? `${preview.length} times · ${ashaNames} and mothers are told` : 'Change a day above to open more',
        onPress: () => {
          const n = network.openTimes(ME_DOCTOR, weekDays, usual.session, usual.minutes);
          setNote(n ? `${n} times open for the week. ${ashaNames} told.` : null);
        },
      }}
    >
      {!!note && (
        <View style={styles.note} accessibilityLiveRegion="polite">
          <Icon name="checkCircle" size={20} color={Colors.sage} weight="fill" />
          <AppText variant="bodyStrong" color={Colors.sage} style={styles.flex}>
            {note}
          </AppText>
        </View>
      )}

      {/* Usual week: set once, then one tap a week. */}
      <View style={styles.card}>
        <View style={styles.cardHead}>
          <AppText variant="heading" style={styles.flex}>
            Your usual week
          </AppText>
          <AppText variant="caption" color={Colors.inkMuted}>
            Saved for next time
          </AppText>
        </View>
        <View style={styles.row} accessibilityRole="none">
          {[1, 2, 3, 4, 5, 6, 0].map((wd) => {
            const on = usual.weekdays.includes(wd);
            return (
              <Pressable
                key={wd}
                accessibilityRole="checkbox"
                accessibilityState={{ checked: on }}
                accessibilityLabel={WEEKDAYS[wd]}
                onPress={() => setUsual({ weekdays: on ? usual.weekdays.filter((x) => x !== wd) : [...usual.weekdays, wd] })}
                style={({ pressed }) => [styles.weekday, on && styles.on, pressed && styles.pressed]}
              >
                <AppText variant="label" color={on ? Colors.onCherry : Colors.ink}>
                  {WEEKDAYS[wd].slice(0, 2)}
                </AppText>
              </Pressable>
            );
          })}
        </View>
        <Segmented
          options={SESSION_IDS.map((s) => ({ id: s, label: SESSIONS[s].label, sub: SESSIONS[s].hours }))}
          value={usual.session}
          onChange={(s) => setUsual({ session: s as Session })}
        />
        <Segmented
          options={SLOT_LENGTHS.map((m) => ({ id: String(m), label: `${m} min`, sub: 'each visit' }))}
          value={String(usual.minutes)}
          onChange={(m) => setUsual({ minutes: Number(m) })}
        />
        <AppText variant="caption" color={Colors.inkMuted}>
          {weekDays.length ? `Next 7 days: ${weekDays.map((d) => fmtDay(d)).join(', ')}` : 'Pick at least one day.'}
        </AppText>
      </View>

      {/* One day: pick it, then tap a session. */}
      <AppText variant="heading" accessibilityRole="header">
        Or change one day
      </AppText>
      <View style={styles.row} accessibilityRole="radiogroup">
        {days.slice(1).map((d) => {
          const on = sameDay(d, day);
          const has = mine.some((s) => sameDay(s.start, d));
          const date = new Date(d);
          return (
            <Pressable
              key={d}
              accessibilityRole="radio"
              accessibilityState={{ checked: on }}
              accessibilityLabel={`${fmtDay(d)}${has ? ', has times' : ''}`}
              onPress={() => {
                Haptic.light();
                setDay(d);
                setNote(null);
              }}
              style={({ pressed }) => [styles.date, on && styles.on, pressed && styles.pressed]}
            >
              <AppText variant="caption" color={on ? Colors.onCherry : Colors.inkMuted}>
                {WEEKDAYS[date.getDay()]}
              </AppText>
              <AppText variant="bodyStrong" color={on ? Colors.onCherry : Colors.ink}>
                {String(date.getDate())}
              </AppText>
              <View style={[styles.mark, has && { backgroundColor: on ? Colors.onCherry : Colors.sage }]} />
            </Pressable>
          );
        })}
      </View>
      <View style={styles.row}>
        {SESSION_IDS.map((s) => (
          <Pressable
            key={s}
            accessibilityRole="button"
            accessibilityLabel={`Open ${fmtDay(day)} ${SESSIONS[s].label}, ${SESSIONS[s].hours}`}
            onPress={() => openDay(s)}
            style={({ pressed }) => [styles.session, pressed && styles.pressed]}
          >
            <Icon name="plus" size={18} color={Colors.cherry} weight="bold" />
            <AppText variant="buttonSmall" color={Colors.cherry} numberOfLines={1}>
              {SESSIONS[s].label}
            </AppText>
            <AppText variant="caption" color={Colors.inkMuted} numberOfLines={1}>
              {SESSIONS[s].hours}
            </AppText>
          </Pressable>
        ))}
      </View>

      {/* The chosen day */}
      <View style={styles.cardHead}>
        <AppText variant="heading" style={styles.flex}>
          {fmtDay(day)}
        </AppText>
        {free.length > 0 && (
          <Pressable
            accessibilityRole="button"
            accessibilityLabel={`Away on ${fmtDay(day)}: close ${free.length} free times`}
            onPress={() => {
              const n = network.closeDay(ME_DOCTOR, day);
              Haptic.medium();
              setNote(`${fmtDay(day)}: ${n} free times closed. Booked visits stay.`);
            }}
            style={({ pressed }) => [styles.away, pressed && styles.pressed]}
          >
            <AppText variant="buttonSmall" color={Colors.cherry}>
              Away this day
            </AppText>
          </Pressable>
        )}
      </View>
      {ofDay.length === 0 ? (
        <AppText variant="body" color={Colors.inkMuted}>
          No times on this day. Tap Morning, Afternoon or Full day above.
        </AppText>
      ) : (
        <View style={styles.tiles}>
          {ofDay.map((s) => {
            const booked = s.status === 'booked';
            const b = booked ? bookingOf(s) : undefined;
            return (
              <Pressable
                key={s.id}
                disabled={!b}
                accessibilityRole={b ? 'button' : undefined}
                accessibilityLabel={b ? `${fmtTime(s.start)}, ${motherOf(s) ?? 'booked'}. Opens messages with the ASHA.` : undefined}
                onPress={() => b && setThreadId(b.id)}
                style={({ pressed }) => [styles.tile, booked ? styles.tileBooked : styles.tileOpen, pressed && styles.pressed]}
              >
                <View style={styles.tileTop}>
                  <AppText variant="bodyStrong">{fmtTime(s.start)}</AppText>
                  {!booked && (
                    <Pressable
                      accessibilityRole="button"
                      accessibilityLabel={`Close ${fmtTime(s.start)}`}
                      hitSlop={10}
                      onPress={() => {
                        Haptic.light();
                        network.removeSlot(s.id);
                      }}
                    >
                      <Icon name="close" size={16} color={Colors.inkMuted} weight="bold" />
                    </Pressable>
                  )}
                </View>
                <AppText variant="caption" color={booked ? Colors.cherry : Colors.sage} numberOfLines={1}>
                  {booked ? (motherOf(s) ?? 'Booked') : 'Free'}
                </AppText>
                {!!b?.comments.length && (
                  <View style={styles.msg}>
                    <Icon name="chat" size={12} color={Colors.cherry} />
                    <AppText variant="overline" color={Colors.cherry}>
                      {String(b.comments.length)}
                    </AppText>
                  </View>
                )}
              </Pressable>
            );
          })}
        </View>
      )}
    </Screen>
  );
}

/** Equal-width segments in one row: label + small sub-line. */
function Segmented({ options, value, onChange }: { options: { id: string; label: string; sub: string }[]; value: string; onChange: (id: string) => void }) {
  return (
    <View style={styles.row} accessibilityRole="radiogroup">
      {options.map((o) => {
        const on = o.id === value;
        return (
          <Pressable
            key={o.id}
            accessibilityRole="radio"
            accessibilityState={{ checked: on }}
            accessibilityLabel={`${o.label}, ${o.sub}`}
            onPress={() => onChange(o.id)}
            style={({ pressed }) => [styles.segment, on && styles.segmentOn, pressed && styles.pressed]}
          >
            <AppText variant="buttonSmall" color={on ? Colors.cherryPressed : Colors.ink} numberOfLines={1}>
              {o.label}
            </AppText>
            <AppText variant="caption" color={Colors.inkMuted} numberOfLines={1}>
              {o.sub}
            </AppText>
          </Pressable>
        );
      })}
    </View>
  );
}

const styles = StyleSheet.create({
  flex: { flex: 1, minWidth: 0 },
  pressed: { transform: [{ scale: 0.96 }] },
  note: { flexDirection: 'row', alignItems: 'center', gap: 8, padding: 12, borderRadius: 14, backgroundColor: Colors.sageTint },
  card: { gap: 12, padding: 16, borderRadius: 20, borderWidth: 1, borderColor: Colors.bandLine, backgroundColor: Colors.surface },
  cardHead: { flexDirection: 'row', alignItems: 'center', gap: 8 },
  // Every row of choices is equal columns, so edges line up down the page.
  row: { flexDirection: 'row', gap: 6 },
  weekday: { flex: 1, height: 44, alignItems: 'center', justifyContent: 'center', borderRadius: 22, borderWidth: 1.5, borderColor: Colors.bandLine, backgroundColor: Colors.surface },
  on: { backgroundColor: Colors.cherry, borderColor: Colors.cherry },
  segment: { flex: 1, minHeight: 56, alignItems: 'center', justifyContent: 'center', paddingHorizontal: 4, borderRadius: 14, borderWidth: 1.5, borderColor: Colors.bandLine, backgroundColor: Colors.surface },
  segmentOn: { borderColor: Colors.cherry, backgroundColor: Colors.cherryMist },
  date: { flex: 1, minHeight: 64, alignItems: 'center', justifyContent: 'center', gap: 1, borderRadius: 14, borderWidth: 1.5, borderColor: Colors.bandLine, backgroundColor: Colors.surface },
  mark: { width: 6, height: 6, borderRadius: 3, marginTop: 2 },
  session: { flex: 1, minHeight: 72, alignItems: 'center', justifyContent: 'center', gap: 2, paddingHorizontal: 4, borderRadius: 16, borderWidth: 1.5, borderStyle: 'dashed', borderColor: Colors.cherry, backgroundColor: Colors.cherryMist },
  away: { minHeight: 40, justifyContent: 'center', paddingHorizontal: 14, borderRadius: 20, borderWidth: 1.5, borderColor: Colors.cherry },
  tiles: { flexDirection: 'row', flexWrap: 'wrap', gap: 8 },
  tile: { width: '31.5%', minHeight: 64, gap: 2, padding: 10, borderRadius: 14, borderWidth: 1.5 },
  msg: { flexDirection: 'row', alignItems: 'center', gap: 3 },
  tileTop: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' },
  tileOpen: { borderColor: `${Colors.sage}55`, backgroundColor: Colors.sageTint },
  tileBooked: { borderColor: Colors.cherry, backgroundColor: Colors.cherryMist },
});
