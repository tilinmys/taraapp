import { useLocalSearchParams } from 'expo-router';
import { useState } from 'react';
import { Pressable, StyleSheet, TextInput, View } from 'react-native';
import { useSharedValue } from 'react-native-reanimated';

import { goBack } from '@/lib/nav';
import { VisitThread } from '@/components/care/visit-thread';
import { ConfirmSheet } from '@/components/system/confirm-sheet';
import { NavCard } from '@/components/system/nav-card';
import { Screen } from '@/components/system/screen';
import { AppText } from '@/components/ui/app-text';
import { BottomSheet } from '@/components/ui/bottom-sheet';
import { Colors, Haptic } from '@/constants/theme';
import { fontFor } from '@/constants/typography';
import {
  BOOKING_CUTOFF_MS,
  ME_ASHA,
  dayStart,
  fmtTime,
  isActive,
  network,
  sameDay,
  staff,
  upcomingBooking,
  useNetwork,
  useNow,
  type Slot,
} from '@/data/care-network';
import { careStatus, sortByRisk, useAshaMothers } from '@/data/store';
import { useOnboarding, useT } from '@/features/onboarding/context';
import { dayLabel, whenLabel } from '@/lib/day-label';

// ASHA: book a doctor visit. Three numbered steps on ONE page, the same
// pattern as "add a woman":
//   1 Day     the next 7 days; each shows how many times are open
//   2 Time    the open times that day, grouped under each doctor
//   3 Woman   her women, needs-care first; anyone with a visit is greyed
//             (opened from a woman's page, she is already chosen)
//   4 Note    optional words for the doctor; starts the visit's thread
// One button: "Book this visit". Booked visits are listed below: tap one for
// its messages with the doctor; Cancel sits behind a confirm. The doctor and
// the mother are told automatically.

const DAYS = 7;

export default function Visits() {
  const t = useT();
  const { language } = useOnboarding();
  const net = useNetwork();
  const women = sortByRisk(useAshaMothers(ME_ASHA));
  const sheet = useSharedValue(0);
  const threadSheet = useSharedValue(0);
  const now = useNow();
  const { mother: motherParam } = useLocalSearchParams<{ mother?: string }>();

  const bookable = (s: Slot) => s.status === 'open' && s.start > now + BOOKING_CUTOFF_MS;
  const days = Array.from({ length: DAYS }, (_, i) => dayStart(i));
  const countOn = (d: number) => net.slots.filter((s) => bookable(s) && sameDay(s.start, d)).length;
  const firstDay = days.find((d) => countOn(d) > 0) ?? days[0];

  const [day, setDay] = useState(firstDay);
  const [slotId, setSlotId] = useState<string | null>(null);
  const [motherId, setMotherId] = useState<string | null>(motherParam ?? null);
  const [note, setNote] = useState('');
  const [threadId, setThreadId] = useState<string | null>(null);
  const [message, setMessage] = useState<{ text: string; ok: boolean } | null>(null);
  const [cancelId, setCancelId] = useState<string | null>(null);

  const daySlots = net.slots.filter((s) => bookable(s) && sameDay(s.start, day));
  const doctors = [...new Set(daySlots.map((s) => s.doctorId))];
  const slot = net.slots.find((s) => s.id === slotId && bookable(s));
  const mother = women.find((m) => m.id === motherId);
  const myBookings = net.bookings
    .filter((b) => b.ashaId === ME_ASHA && isActive(b))
    .map((b) => ({ b, s: net.slots.find((x) => x.id === b.slotId) }))
    .filter((x): x is { b: typeof x.b; s: Slot } => !!x.s && x.s.start > now)
    .sort((a, c) => a.s.start - c.s.start);

  const book = () => {
    if (!slot || !mother) return;
    const r = network.bookSlot(slot.id, mother, { kind: 'asha', id: ME_ASHA }, note);
    if (r.ok) {
      Haptic.success();
      setMessage({ text: t('bookedOk', { name: mother.name }), ok: true });
      setSlotId(null);
      setMotherId(null);
      setNote('');
    } else {
      Haptic.warning();
      setMessage({ text: r.reason === 'too_soon' ? t('errTooSoon') : r.reason === 'has_visit' ? t('errHasVisit') : t('errTaken'), ok: false });
      if (r.reason !== 'has_visit') setSlotId(null);
    }
  };

  const cancelling = myBookings.find((x) => x.b.id === cancelId);

  return (
    <Screen
      title={t('visitsTitle')}
      compactTitle={t('visitsTitle')}
      back={{ label: t('back'), onPress: () => goBack('/asha') }}
      sheetProgress={threadId ? threadSheet : sheet}
      cta={{
        label: t('bookVisit'),
        enabled: !!slot && !!mother,
        icon: 'calendar',
        haptic: 'success',
        context: slot && mother ? `${mother.name} · ${staff(slot.doctorId)?.name} · ${whenLabel(slot.start, language)}` : undefined,
        onPress: book,
      }}
      overlay={
        <>
          <BottomSheet open={!!threadId} onClose={() => setThreadId(null)} progress={threadSheet} title={t('visitMessages')} closeLabel={t('back')}>
            {threadId && (
              <VisitThread
                bookingId={threadId}
                me="asha"
                myId={ME_ASHA}
                motherName={women.find((m) => m.id === net.bookings.find((x) => x.id === threadId)?.motherId)?.name ?? ''}
              />
            )}
          </BottomSheet>
          <ConfirmSheet
          open={!!cancelling}
          progress={sheet}
          title={t('cancelVisit')}
          body={cancelling ? `${women.find((m) => m.id === cancelling.b.motherId)?.name ?? ''} · ${staff(cancelling.s.doctorId)?.name} · ${whenLabel(cancelling.s.start, language)}` : ''}
          confirmLabel={t('cancelVisit')}
          cancelLabel={t('back')}
          onConfirm={() => {
            if (cancelling) network.cancelBooking(cancelling.b.id);
            Haptic.medium();
            setCancelId(null);
          }}
          onCancel={() => setCancelId(null)}
          />
        </>
      }
    >
      {!!message && (
        <View style={[styles.message, message.ok ? styles.messageOk : styles.messageErr]} accessibilityLiveRegion="polite">
          <AppText variant="bodyStrong" color={message.ok ? Colors.sage : Colors.cherry}>
            {message.text}
          </AppText>
        </View>
      )}

      {/* 1 Day */}
      <AppText variant="label">{t('pickDay')}</AppText>
      <View style={styles.days} accessibilityRole="radiogroup">
        {days.map((d) => {
          const n = countOn(d);
          const on = sameDay(d, day);
          return (
            <Pressable
              key={d}
              accessibilityRole="radio"
              accessibilityState={{ checked: on, disabled: !n }}
              disabled={!n}
              onPress={() => {
                Haptic.light();
                setDay(d);
                setSlotId(null);
                setMessage(null);
              }}
              style={({ pressed }) => [styles.day, on && styles.dayOn, !n && styles.dayOff, pressed && styles.pressed]}
            >
              <AppText variant="label" color={on ? Colors.onCherry : Colors.ink} numberOfLines={1}>
                {dayLabel(d, language)}
              </AppText>
              <AppText variant="caption" color={on ? Colors.onCherry : Colors.inkMuted}>
                {n ? `${n}` : '—'}
              </AppText>
            </Pressable>
          );
        })}
      </View>

      {/* 2 Time, under each doctor */}
      <AppText variant="label">{t('pickTime')}</AppText>
      {daySlots.length === 0 ? (
        <AppText variant="body" color={Colors.inkMuted}>
          {t('noSlots')}
        </AppText>
      ) : (
        doctors.map((d) => (
          <View key={d} style={styles.doctor}>
            <AppText variant="bodyStrong">{`${staff(d)?.name ?? ''} · ${staff(d)?.title ?? ''}`}</AppText>
            <View style={styles.times}>
              {daySlots
                .filter((s) => s.doctorId === d)
                .map((s) => {
                  const on = s.id === slotId;
                  return (
                    <Pressable
                      key={s.id}
                      accessibilityRole="radio"
                      accessibilityState={{ checked: on }}
                      accessibilityLabel={`${staff(d)?.name}, ${fmtTime(s.start)}`}
                      onPress={() => {
                        Haptic.light();
                        setSlotId(s.id);
                        setMessage(null);
                      }}
                      style={({ pressed }) => [styles.time, on && styles.timeOn, pressed && styles.pressed]}
                    >
                      <AppText variant="button" color={on ? Colors.onCherry : Colors.ink}>
                        {fmtTime(s.start)}
                      </AppText>
                    </Pressable>
                  );
                })}
            </View>
          </View>
        ))
      )}

      {/* 3 Who it is for (only once a time is chosen) */}
      {slot && (
        <>
          <AppText variant="label">{t('pickMother')}</AppText>
          <View style={styles.list} accessibilityRole="radiogroup">
            {women.map((m) => {
              const has = !!upcomingBooking(m.id, net);
              const on = m.id === motherId;
              const c = careStatus(m);
              return (
                <Pressable
                  key={m.id}
                  accessibilityRole="radio"
                  accessibilityState={{ checked: on, disabled: has }}
                  disabled={has}
                  onPress={() => {
                    Haptic.light();
                    setMotherId(m.id);
                  }}
                  style={({ pressed }) => [styles.pick, on && styles.pickOn, has && styles.dayOff, pressed && styles.pressed]}
                >
                  <View style={[styles.radio, on && styles.radioOn]} />
                  <AppText variant="bodyStrong" style={styles.flex} numberOfLines={1}>
                    {m.name}
                  </AppText>
                  <AppText variant="label" color={has ? Colors.inkMuted : c === 'alert' ? Colors.cherry : c === 'new' ? Colors.plum : Colors.sage}>
                    {has ? t('hasVisit') : c === 'alert' ? t('statusNeedsCare') : c === 'new' ? t('newBadge') : t('statusWell')}
                  </AppText>
                </Pressable>
              );
            })}
          </View>
          {/* 4 Note for the doctor */}
          {mother && (
            <>
              <AppText variant="label">{t('noteForDoctor')}</AppText>
              <TextInput
                value={note}
                onChangeText={setNote}
                placeholder={t('notePlaceholder')}
                placeholderTextColor={Colors.inkSubtle}
                selectionColor={Colors.cherry}
                multiline
                maxLength={300}
                accessibilityLabel={t('noteForDoctor')}
                style={[styles.note, fontFor('input')]}
                maxFontSizeMultiplier={1.4}
              />
            </>
          )}
        </>
      )}

      {/* Booked visits */}
      <AppText variant="heading" accessibilityRole="header">
        {t('bookedVisits')}
      </AppText>
      {myBookings.length === 0 ? (
        <AppText variant="body" color={Colors.inkMuted}>
          {t('noVisits')}
        </AppText>
      ) : (
        <View style={styles.list}>
          {myBookings.map(({ b, s }) => (
            <NavCard
              key={b.id}
              icon="calendar"
              title={women.find((m) => m.id === b.motherId)?.name ?? ''}
              line={`${staff(s.doctorId)?.name} · ${whenLabel(s.start, language)}\n${b.bookedBy === 'mother' ? t('bookedByHer') : b.comments.length ? t('messagesCount', { n: b.comments.length }) : t('openMessages')}`}
              onPress={() => setThreadId(b.id)}
              action={
                <Pressable
                  accessibilityRole="button"
                  accessibilityLabel={t('cancelVisit')}
                  onPress={() => setCancelId(b.id)}
                  style={({ pressed }) => [styles.cancel, pressed && styles.pressed]}
                >
                  <AppText variant="buttonSmall" color={Colors.cherry}>
                    {t('cancelVisit')}
                  </AppText>
                </Pressable>
              }
            />
          ))}
        </View>
      )}
    </Screen>
  );
}

const styles = StyleSheet.create({
  flex: { flex: 1, minWidth: 0 },
  pressed: { transform: [{ scale: 0.97 }] },
  message: { padding: 14, borderRadius: 16 },
  messageOk: { backgroundColor: Colors.sageTint },
  messageErr: { backgroundColor: Colors.cherryMist },
  days: { flexDirection: 'row', flexWrap: 'wrap', gap: 8 },
  day: { minWidth: 76, flexGrow: 1, minHeight: 60, alignItems: 'center', justifyContent: 'center', paddingHorizontal: 8, borderRadius: 16, borderWidth: 2, borderColor: Colors.bandLine, backgroundColor: Colors.surface },
  dayOn: { backgroundColor: Colors.cherry, borderColor: Colors.cherry },
  dayOff: { opacity: 0.4 },
  doctor: { gap: 8 },
  times: { flexDirection: 'row', flexWrap: 'wrap', gap: 8 },
  time: { width: '30%', flexGrow: 1, minHeight: 56, alignItems: 'center', justifyContent: 'center', borderRadius: 16, borderWidth: 2, borderColor: Colors.bandLine, backgroundColor: Colors.surface },
  timeOn: { backgroundColor: Colors.cherry, borderColor: Colors.cherry },
  list: { gap: 8 },
  pick: { minHeight: 60, flexDirection: 'row', alignItems: 'center', gap: 12, paddingHorizontal: 14, borderRadius: 16, borderWidth: 2, borderColor: Colors.bandLine, backgroundColor: Colors.surface },
  pickOn: { borderColor: Colors.cherry, backgroundColor: Colors.cherryMist },
  radio: { width: 22, height: 22, borderRadius: 11, borderWidth: 2, borderColor: Colors.outline },
  radioOn: { borderColor: Colors.cherry, backgroundColor: Colors.cherry },
  note: { minHeight: 80, paddingHorizontal: 16, paddingVertical: 12, borderRadius: 16, borderWidth: 1.5, borderColor: Colors.outline, color: Colors.ink, backgroundColor: Colors.surface, textAlignVertical: 'top' },
  cancel: { minHeight: 40, justifyContent: 'center', paddingHorizontal: 12, borderRadius: 20, borderWidth: 1.5, borderColor: Colors.cherry },
});
