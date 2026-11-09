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

// Doctor: visit times. One question at a time, nothing to decode:
//   1 Pick a day     7 dates; a dot means that day has times
//   2 That day       in plain words: "2 booked · 4 free", then who is coming
//                    (tap a name for messages). An empty day shows three big
//                    buttons: Morning / Afternoon / Full day. "Away this day"
//                    closes the free times; booked visits stay.
//   3 Every week     folded into one line ("Mon, Wed, Fri · Morning · 30 min");
//                    "Change" opens the editor. The sticky button opens the
//                    usual week in one tap.
// Every open goes straight to the ASHAs and mothers (one message per ASHA).

const SESSION_IDS: Session[] = ['morning', 'afternoon', 'both'];

export default function DoctorSlots() {
  const net = useNetwork();
  const now = useNow();
  const mothers = useMothers();
  const me = staff(ME_DOCTOR);
  const { day: dayParam } = useLocalSearchParams<{ day?: string }>();
  const days = Array.from({ length: PLAN_DAYS }, (_, i) => dayStart(i));
  const [day, setDay] = useState(dayParam ? Number(dayParam) : days[1]);
  const [note, setNote] = useState<string | null>(null);
  const [addMore, setAddMore] = useState(false);
  const [editWeek, setEditWeek] = useState(false);
  const usual = net.usual[ME_DOCTOR] ?? { weekdays: [1, 3, 5], session: 'morning' as Session, minutes: 30 };

  const mine = net.slots.filter((s) => s.doctorId === ME_DOCTOR && s.status !== 'closed' && s.start > now - 60 * 60_000);
  const ofDay = mine.filter((s) => sameDay(s.start, day)).sort((a, b) => a.start - b.start);
  const free = ofDay.filter((s) => s.status === 'open');
  const booked = ofDay.filter((s) => s.status === 'booked');
  const ashaNames = [...new Set(ZONES.filter((z) => z.phcId === me?.phcId).map((z) => staff(z.ashaId)?.name))].join(' and ');
  const weekDays = usualDays(usual, now);
  const preview = planTimes(ME_DOCTOR, weekDays, usual.session, usual.minutes, net);
  const bookingOf = (s: Slot) => net.bookings.find((x) => x.slotId === s.id && isActive(x));
  const motherOf = (s: Slot) => mothers.find((m) => m.id === bookingOf(s)?.motherId)?.name;
  const sheet = useSharedValue(0);
  const [threadId, setThreadId] = useState<string | null>(null);
  const threadMother = mothers.find((m) => m.id === net.bookings.find((b) => b.id === threadId)?.motherId)?.name ?? '';
  const weekLine = [1, 2, 3, 4, 5, 6, 0].filter((wd) => usual.weekdays.includes(wd)).map((wd) => WEEKDAYS[wd]).join(', ');

  const setUsual = (next: Partial<typeof usual>) => {
    Haptic.light();
    network.setUsual(ME_DOCTOR, { ...usual, ...next });
    setNote(null);
  };
  const openDay = (session: Session) => {
    const n = network.openTimes(ME_DOCTOR, [day], session, usual.minutes);
    Haptic[n ? 'success' : 'warning']();
    setAddMore(false);
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
        context: preview.length ? `${weekLine} · ${SESSIONS[usual.session].label}` : undefined,
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

      {/* 1 Pick a day */}
      <View style={styles.row} accessibilityRole="radiogroup">
        {days.map((d) => {
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
                setAddMore(false);
              }}
              style={({ pressed }) => [styles.date, on && styles.on, pressed && styles.pressed]}
            >
              <AppText variant="caption" color={on ? Colors.onCherry : Colors.inkMuted} numberOfLines={1}>
                {sameDay(d, now) ? 'Today' : WEEKDAYS[date.getDay()].slice(0, 3)}
              </AppText>
              <AppText variant="bodyStrong" color={on ? Colors.onCherry : Colors.ink}>
                {String(date.getDate())}
              </AppText>
              <View style={[styles.mark, has && { backgroundColor: on ? Colors.onCherry : Colors.sage }]} />
            </Pressable>
          );
        })}
      </View>

      {/* 2 That day, in plain words */}
      <View style={styles.card}>
        <View style={styles.cardHead}>
          <View style={styles.flex}>
            <AppText variant="heading">{fmtDay(day)}</AppText>
            <AppText variant="body" color={Colors.inkMuted}>
              {ofDay.length === 0 ? 'No visit times yet' : `${booked.length} booked · ${free.length} free`}
            </AppText>
          </View>
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

        {booked.map((s) => {
          const b = bookingOf(s);
          return (
            <Pressable
              key={s.id}
              disabled={!b}
              accessibilityRole="button"
              accessibilityLabel={`${fmtTime(s.start)}, ${motherOf(s) ?? 'booked'}. Opens messages with the ASHA.`}
              onPress={() => b && setThreadId(b.id)}
              style={({ pressed }) => [styles.visit, pressed && styles.pressed]}
            >
              <AppText variant="bodyStrong" style={styles.time}>
                {fmtTime(s.start)}
              </AppText>
              <AppText variant="bodyStrong" numberOfLines={1} style={styles.flex}>
                {motherOf(s) ?? 'Booked'}
              </AppText>
              {!!b?.comments.length && (
                <View style={styles.msg}>
                  <Icon name="chat" size={14} color={Colors.cherry} />
                  <AppText variant="label" color={Colors.cherry}>
                    {String(b.comments.length)}
                  </AppText>
                </View>
              )}
              <Icon name="forward" size={18} color={Colors.cherry} weight="bold" />
            </Pressable>
          );
        })}

        {free.length > 0 && (
          <AppText variant="caption" color={Colors.inkMuted}>
            {`Free: ${free.map((s) => fmtTime(s.start)).join(', ')}`}
          </AppText>
        )}

        {ofDay.length === 0 || addMore ? (
          <>
            <AppText variant="label">{ofDay.length === 0 ? 'Open this day for visits' : 'Add more times'}</AppText>
            <View style={styles.row}>
              {SESSION_IDS.map((s) => (
                <Pressable
                  key={s}
                  accessibilityRole="button"
                  accessibilityLabel={`Open ${fmtDay(day)} ${SESSIONS[s].label}, ${SESSIONS[s].hours}`}
                  onPress={() => openDay(s)}
                  style={({ pressed }) => [styles.session, pressed && styles.pressed]}
                >
                  <AppText variant="buttonSmall" color={Colors.cherry} numberOfLines={1}>
                    {SESSIONS[s].label}
                  </AppText>
                  <AppText variant="caption" color={Colors.inkMuted} numberOfLines={1}>
                    {SESSIONS[s].hours}
                  </AppText>
                </Pressable>
              ))}
            </View>
          </>
        ) : (
          <Pressable accessibilityRole="button" onPress={() => setAddMore(true)} style={({ pressed }) => [styles.link, pressed && styles.pressed]}>
            <Icon name="plus" size={16} color={Colors.cherry} weight="bold" />
            <AppText variant="buttonSmall" color={Colors.cherry}>
              Add more times
            </AppText>
          </Pressable>
        )}
      </View>

      {/* 3 Every week: one line until she taps Change */}
      <View style={styles.card}>
        <View style={styles.cardHead}>
          <View style={styles.flex}>
            <AppText variant="heading">Every week</AppText>
            <AppText variant="body" color={Colors.inkMuted}>
              {weekLine ? `${weekLine} · ${SESSIONS[usual.session].label} · ${usual.minutes} min` : 'No days chosen'}
            </AppText>
          </View>
          <Pressable
            accessibilityRole="button"
            accessibilityState={{ expanded: editWeek }}
            onPress={() => {
              Haptic.light();
              setEditWeek(!editWeek);
            }}
            style={({ pressed }) => [styles.away, pressed && styles.pressed]}
          >
            <AppText variant="buttonSmall" color={Colors.cherry}>
              {editWeek ? 'Done' : 'Change'}
            </AppText>
          </Pressable>
        </View>
        {editWeek && (
          <>
            <AppText variant="label">Days</AppText>
            <View style={styles.row}>
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
            <AppText variant="label">Time</AppText>
            <Segmented options={SESSION_IDS.map((s) => ({ id: s, label: SESSIONS[s].label }))} value={usual.session} onChange={(s) => setUsual({ session: s as Session })} />
            <AppText variant="label">Each visit</AppText>
            <Segmented options={SLOT_LENGTHS.map((m) => ({ id: String(m), label: `${m} min` }))} value={String(usual.minutes)} onChange={(m) => setUsual({ minutes: Number(m) })} />
          </>
        )}
      </View>
    </Screen>
  );
}

/** Equal-width choices in one row. */
function Segmented({ options, value, onChange }: { options: { id: string; label: string }[]; value: string; onChange: (id: string) => void }) {
  return (
    <View style={styles.row} accessibilityRole="radiogroup">
      {options.map((o) => {
        const on = o.id === value;
        return (
          <Pressable
            key={o.id}
            accessibilityRole="radio"
            accessibilityState={{ checked: on }}
            accessibilityLabel={o.label}
            onPress={() => onChange(o.id)}
            style={({ pressed }) => [styles.segment, on && styles.segmentOn, pressed && styles.pressed]}
          >
            <AppText variant="buttonSmall" color={on ? Colors.cherryPressed : Colors.ink} numberOfLines={1}>
              {o.label}
            </AppText>
          </Pressable>
        );
      })}
    </View>
  );
}

const styles = StyleSheet.create({
  flex: { flex: 1, minWidth: 0 },
  pressed: { transform: [{ scale: 0.97 }] },
  note: { flexDirection: 'row', alignItems: 'center', gap: 8, padding: 12, borderRadius: 14, backgroundColor: Colors.sageTint },
  card: { gap: 10, padding: 14, borderRadius: 20, borderWidth: 1, borderColor: Colors.bandLine, backgroundColor: Colors.surface },
  cardHead: { flexDirection: 'row', alignItems: 'center', gap: 8 },
  // Every row of choices is equal columns, so edges line up down the page.
  row: { flexDirection: 'row', gap: 6 },
  date: { flex: 1, minHeight: 60, alignItems: 'center', justifyContent: 'center', gap: 1, borderRadius: 14, borderWidth: 1.5, borderColor: Colors.bandLine, backgroundColor: Colors.surface },
  on: { backgroundColor: Colors.cherry, borderColor: Colors.cherry },
  mark: { width: 6, height: 6, borderRadius: 3, marginTop: 2 },
  away: { minHeight: 40, justifyContent: 'center', paddingHorizontal: 14, borderRadius: 20, borderWidth: 1.5, borderColor: Colors.cherry },
  visit: { minHeight: 52, flexDirection: 'row', alignItems: 'center', gap: 12, paddingHorizontal: 12, borderRadius: 14, backgroundColor: Colors.cherryMist },
  time: { minWidth: 52 },
  msg: { flexDirection: 'row', alignItems: 'center', gap: 3 },
  session: { flex: 1, minHeight: 64, alignItems: 'center', justifyContent: 'center', gap: 2, paddingHorizontal: 4, borderRadius: 16, borderWidth: 1.5, borderStyle: 'dashed', borderColor: Colors.cherry, backgroundColor: Colors.cherryMist },
  link: { minHeight: 40, flexDirection: 'row', alignItems: 'center', gap: 6, alignSelf: 'flex-start' },
  weekday: { flex: 1, height: 44, alignItems: 'center', justifyContent: 'center', borderRadius: 22, borderWidth: 1.5, borderColor: Colors.bandLine, backgroundColor: Colors.surface },
  segment: { flex: 1, minHeight: 48, alignItems: 'center', justifyContent: 'center', paddingHorizontal: 4, borderRadius: 14, borderWidth: 1.5, borderColor: Colors.bandLine, backgroundColor: Colors.surface },
  segmentOn: { borderColor: Colors.cherry, backgroundColor: Colors.cherryMist },
});
