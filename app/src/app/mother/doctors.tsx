import { useLocalSearchParams } from 'expo-router';
import { useState } from 'react';
import { Linking, Pressable, StyleSheet, View } from 'react-native';
import { useSharedValue } from 'react-native-reanimated';

import { goBack } from '@/lib/nav';
import { MissingMother } from '@/components/mother/missing-mother';
import { AppIcon } from '@/components/system/app-icon';
import { ConfirmSheet } from '@/components/system/confirm-sheet';
import { Icon } from '@/components/system/icon';
import { Screen } from '@/components/system/screen';
import { AppText } from '@/components/ui/app-text';
import { Colors, Haptic } from '@/constants/theme';
import {
  BOOKING_CUTOFF_MS,
  ashaForZone,
  doctorWeek,
  doctorsOf,
  fmtTime,
  network,
  sameDay,
  staff,
  upcomingBooking,
  useNetwork,
  useNow,
  zone,
  type Slot,
} from '@/data/care-network';
import { useMother } from '@/data/store';
import { useOnboarding, useT } from '@/features/onboarding/context';
import { dayLabel, whenLabel } from '@/lib/day-label';

// Mother: book her own doctor visit. Three numbered steps on ONE page, each
// a row of big picture buttons, and only real choices are shown (days with
// free times, times still free):
//   1 Doctor   picture, name, "Next free: Tomorrow"
//   2 Day      days that still have free times
//   3 Time     Morning / Afternoon, three equal buttons a row
// One button at the bottom, "Book my visit", which repeats her choice in
// words. Once booked the page shows her visit, and the ASHA is told (she
// stays the mother's point of contact). Cancel sits behind a confirm.

export default function MotherDoctors() {
  const { demoMother, language } = useOnboarding();
  const t = useT();
  const mother = useMother(demoMother);
  const net = useNetwork();
  const now = useNow();
  const { doctor: doctorParam } = useLocalSearchParams<{ doctor?: string }>();
  const sheet = useSharedValue(0);
  const [doctorId, setDoctorId] = useState<string | null>(doctorParam ?? null);
  const [day, setDay] = useState<number | null>(null);
  const [slotId, setSlotId] = useState<string | null>(null);
  const [message, setMessage] = useState<{ text: string; ok: boolean } | null>(null);
  const [cancelling, setCancelling] = useState(false);
  if (!mother) return <MissingMother />;

  const asha = ashaForZone(mother.zoneId);
  const doctors = doctorsOf(zone(mother.zoneId)?.phcId ?? 'hosahalli');
  const b = upcomingBooking(mother.id, net);
  const mySlot = b && net.slots.find((x) => x.id === b.slotId);

  const weeks = Object.fromEntries(doctors.map((d) => [d.id, doctorWeek(d.id, net, now).filter((x) => x.free > 0)]));
  // Smart defaults: the chosen (or first available) doctor, then his first free day.
  const doc = doctors.find((d) => d.id === doctorId && weeks[d.id].length) ?? doctors.find((d) => weeks[d.id].length);
  const days = doc ? weeks[doc.id] : [];
  const theDay = days.find((x) => day !== null && sameDay(x.day, day))?.day ?? days[0]?.day;
  const times = doc && theDay !== undefined ? net.slots.filter((s) => s.doctorId === doc.id && s.status === 'open' && sameDay(s.start, theDay) && s.start > now + BOOKING_CUTOFF_MS) : [];
  const slot = times.find((s) => s.id === slotId);
  const morning = times.filter((s) => new Date(s.start).getHours() < 13);
  const afternoon = times.filter((s) => new Date(s.start).getHours() >= 13);

  const book = () => {
    if (!slot) return;
    const r = network.bookSlot(slot.id, mother, { kind: 'mother' });
    if (r.ok) {
      Haptic.success();
      setMessage({ text: t('bookedBoth', { asha: asha?.name ?? '', doctor: staff(slot.doctorId)?.name ?? '' }), ok: true });
      setSlotId(null);
    } else {
      Haptic.warning();
      setMessage({ text: r.reason === 'has_visit' ? t('alreadyBooked') : t('justTaken'), ok: false });
      setSlotId(null);
    }
  };

  const callAsha = () => {
    Haptic.medium();
    if (asha) Linking.openURL(`tel:${asha.phone}`).catch(() => {});
  };

  return (
    <Screen
      title={mySlot ? t('yourVisit') : t('bookADoctor')}
      titleVariant="greeting"
      compactTitle={mySlot ? t('yourVisit') : t('bookADoctor')}
      eyebrow={`${zone(mother.zoneId)?.name ?? ''} · Hosahalli PHC`}
      back={{ label: t('back'), onPress: () => goBack('/mother') }}
      sheetProgress={sheet}
      cta={
        mySlot
          ? asha
            ? { label: t('callHer', { name: asha.name }), enabled: true, icon: 'phone', haptic: 'medium', context: t('askAsha'), onPress: callAsha }
            : null
          : {
              label: t('bookMyVisit'),
              enabled: !!slot,
              icon: 'calendar',
              haptic: 'success',
              context: slot ? `${staff(slot.doctorId)?.name} · ${whenLabel(slot.start, language)}` : t('chooseAll'),
              onPress: book,
            }
      }
      overlay={
        <ConfirmSheet
          open={cancelling}
          progress={sheet}
          title={t('cancelQ')}
          body={mySlot ? t('cancelBody', { doctor: staff(mySlot.doctorId)?.name ?? '', when: whenLabel(mySlot.start, language) }) : ''}
          confirmLabel={t('yesCancel')}
          cancelLabel={t('keepVisit')}
          onConfirm={() => {
            if (b) network.cancelBooking(b.id, 'mother', mother.name);
            Haptic.medium();
            setCancelling(false);
            setMessage({ text: t('cancelledMsg', { asha: asha?.name ?? '' }), ok: true });
          }}
          onCancel={() => setCancelling(false)}
        />
      }
    >
      {!!message && (
        <View style={[styles.message, message.ok ? styles.messageOk : styles.messageErr]} accessibilityLiveRegion="polite">
          <Icon name={message.ok ? 'checkCircle' : 'info'} size={20} color={message.ok ? Colors.sage : Colors.cherry} weight="fill" />
          <AppText variant="bodyStrong" color={message.ok ? Colors.sage : Colors.cherry} style={styles.flex}>
            {message.text}
          </AppText>
        </View>
      )}

      {mySlot ? (
        <>
          {/* Her visit: the facts, big and plain. */}
          <View style={styles.visit}>
            <View style={styles.visitWell}>
              <AppIcon name="calendar" size={48} />
            </View>
            <View style={styles.flex}>
              <AppText variant="heading">{whenLabel(mySlot.start, language)}</AppText>
              <AppText variant="bodyStrong">{staff(mySlot.doctorId)?.name}</AppText>
              <AppText variant="caption" color={Colors.inkMuted}>
                {`Hosahalli PHC · ${b?.bookedBy === 'mother' ? t('youBooked') : t('bookedByName', { asha: asha?.name ?? '' })}`}
              </AppText>
            </View>
          </View>
          <View style={styles.tip}>
            <AppIcon name="bag" size={36} />
            <AppText variant="body" style={styles.flex}>
              {t('bringCard')}
            </AppText>
          </View>
          <Pressable
            accessibilityRole="button"
            accessibilityLabel={t('cancelThis')}
            onPress={() => setCancelling(true)}
            style={({ pressed }) => [styles.cancel, pressed && styles.pressed]}
          >
            <AppText variant="buttonSmall" color={Colors.cherry}>
              {t('cancelThis')}
            </AppText>
          </Pressable>
        </>
      ) : (
        <>
          {/* 1 Doctor */}
          <Step n={1} title={t('chooseDoctor')} />
          <View style={styles.list} accessibilityRole="radiogroup">
            {doctors.map((d) => {
              const free = weeks[d.id];
              const on = doc?.id === d.id;
              return (
                <Pressable
                  key={d.id}
                  accessibilityRole="radio"
                  accessibilityState={{ checked: on, disabled: !free.length }}
                  disabled={!free.length}
                  onPress={() => {
                    Haptic.light();
                    setDoctorId(d.id);
                    setDay(null);
                    setSlotId(null);
                    setMessage(null);
                  }}
                  style={({ pressed }) => [styles.doctor, on && styles.choiceOn, !free.length && styles.off, pressed && styles.pressed]}
                >
                  <View style={styles.doctorWell}>
                    <AppIcon name="doctor" size={44} />
                  </View>
                  <View style={styles.flex}>
                    <AppText variant="heading" numberOfLines={1}>
                      {d.name}
                    </AppText>
                    <AppText variant="caption" color={Colors.inkMuted} numberOfLines={1}>
                      {d.title ?? 'Doctor'}
                    </AppText>
                    <AppText variant="label" color={free.length ? Colors.sage : Colors.inkMuted} numberOfLines={1}>
                      {free.length ? t('nextFree', { day: dayLabel(free[0].day, language) }) : t('noFreeWeek')}
                    </AppText>
                  </View>
                  <View style={[styles.radio, on && styles.radioOn]}>{on && <Icon name="check" size={14} color={Colors.onCherry} weight="bold" />}</View>
                </Pressable>
              );
            })}
          </View>

          {doc && (
            <>
              {/* 2 Day */}
              <Step n={2} title={t('chooseDay')} />
              <View style={styles.grid} accessibilityRole="radiogroup">
                {days.map((x) => {
                  const on = theDay !== undefined && sameDay(x.day, theDay);
                  return (
                    <Pressable
                      key={x.day}
                      accessibilityRole="radio"
                      accessibilityState={{ checked: on }}
                      accessibilityLabel={`${dayLabel(x.day, language)}, ${t('freeTimes', { n: x.free })}`}
                      onPress={() => {
                        Haptic.light();
                        setDay(x.day);
                        setSlotId(null);
                        setMessage(null);
                      }}
                      style={({ pressed }) => [styles.cell, on && styles.cellOn, pressed && styles.pressed]}
                    >
                      <AppText variant="bodyStrong" color={on ? Colors.onCherry : Colors.ink} numberOfLines={1}>
                        {dayLabel(x.day, language)}
                      </AppText>
                      <AppText variant="caption" color={on ? Colors.onCherry : Colors.inkMuted}>
                        {t('freeTimes', { n: x.free })}
                      </AppText>
                    </Pressable>
                  );
                })}
              </View>

              {/* 3 Time */}
              <Step n={3} title={t('chooseTime')} />
              {[
                { label: t('morning'), icon: 'tip' as const, list: morning },
                { label: t('afternoon'), icon: 'rest' as const, list: afternoon },
              ]
                .filter((g) => g.list.length)
                .map((g) => (
                  <View key={g.label} style={styles.group}>
                    <AppText variant="label" color={Colors.inkMuted}>
                      {g.label}
                    </AppText>
                    <View style={styles.grid} accessibilityRole="radiogroup">
                      {g.list.map((s: Slot) => {
                        const on = s.id === slotId;
                        return (
                          <Pressable
                            key={s.id}
                            accessibilityRole="radio"
                            accessibilityState={{ checked: on }}
                            accessibilityLabel={fmtTime(s.start)}
                            onPress={() => {
                              Haptic.light();
                              setSlotId(s.id);
                              setMessage(null);
                            }}
                            style={({ pressed }) => [styles.cell, styles.time, on && styles.cellOn, pressed && styles.pressed]}
                          >
                            <AppText variant="button" color={on ? Colors.onCherry : Colors.ink}>
                              {fmtTime(s.start)}
                            </AppText>
                          </Pressable>
                        );
                      })}
                    </View>
                  </View>
                ))}
            </>
          )}

          {!doc && (
            <View style={styles.tip}>
              <AppIcon name="ashaWorker" size={40} />
              <AppText variant="body" style={styles.flex}>
                {t('noFreeHelp', { asha: asha?.name ?? '' })}
              </AppText>
            </View>
          )}
          {asha && (
            <Pressable accessibilityRole="button" accessibilityLabel={t('needHelp', { asha: asha.name })} onPress={callAsha} style={({ pressed }) => [styles.help, pressed && styles.pressed]}>
              <Icon name="phone" size={18} color={Colors.cherry} weight="fill" />
              <AppText variant="buttonSmall" color={Colors.cherry}>
                {t('needHelp', { asha: asha.name })}
              </AppText>
            </Pressable>
          )}
        </>
      )}
    </Screen>
  );
}

/** A numbered step heading: a round number badge and the instruction. */
function Step({ n, title }: { n: number; title: string }) {
  return (
    <View style={styles.step} accessibilityRole="header">
      <View style={styles.stepBadge}>
        <AppText variant="label" color={Colors.onCherry}>
          {String(n)}
        </AppText>
      </View>
      <AppText variant="heading">{title}</AppText>
    </View>
  );
}

const styles = StyleSheet.create({
  flex: { flex: 1, minWidth: 0 },
  pressed: { transform: [{ scale: 0.97 }] },
  off: { opacity: 0.45 },
  message: { flexDirection: 'row', alignItems: 'center', gap: 8, padding: 14, borderRadius: 16 },
  messageOk: { backgroundColor: Colors.sageTint },
  messageErr: { backgroundColor: Colors.cherryMist },
  step: { flexDirection: 'row', alignItems: 'center', gap: 10, marginTop: 4 },
  stepBadge: { width: 28, height: 28, borderRadius: 14, alignItems: 'center', justifyContent: 'center', backgroundColor: Colors.cherry },
  list: { gap: 10 },
  doctor: { minHeight: 88, flexDirection: 'row', alignItems: 'center', gap: 12, padding: 14, borderRadius: 20, borderWidth: 2, borderColor: Colors.bandLine, backgroundColor: Colors.surface },
  choiceOn: { borderColor: Colors.cherry, backgroundColor: Colors.cherryMist },
  doctorWell: { width: 60, height: 60, borderRadius: 18, alignItems: 'center', justifyContent: 'center', backgroundColor: Colors.shell },
  radio: { width: 26, height: 26, borderRadius: 13, borderWidth: 2, borderColor: Colors.outline, alignItems: 'center', justifyContent: 'center', backgroundColor: Colors.surface },
  radioOn: { borderColor: Colors.cherry, backgroundColor: Colors.cherry },
  // Three equal columns for days and times.
  grid: { flexDirection: 'row', flexWrap: 'wrap', gap: 8 },
  cell: { width: '31%', maxWidth: '32%', flexGrow: 1, minHeight: 64, alignItems: 'center', justifyContent: 'center', paddingHorizontal: 6, borderRadius: 16, borderWidth: 2, borderColor: Colors.bandLine, backgroundColor: Colors.surface },
  cellOn: { backgroundColor: Colors.cherry, borderColor: Colors.cherry },
  time: { minHeight: 56 },
  group: { gap: 8 },
  visit: { flexDirection: 'row', alignItems: 'center', gap: 14, padding: 16, borderRadius: 22, borderWidth: 1, borderColor: `${Colors.sage}33`, backgroundColor: Colors.sageTint },
  visitWell: { width: 72, height: 72, borderRadius: 22, alignItems: 'center', justifyContent: 'center', backgroundColor: Colors.surface },
  tip: { flexDirection: 'row', alignItems: 'center', gap: 12, padding: 14, borderRadius: 18, backgroundColor: Colors.shell },
  cancel: { alignSelf: 'center', minHeight: 44, justifyContent: 'center', paddingHorizontal: 18, borderRadius: 22, borderWidth: 1.5, borderColor: Colors.cherry },
  help: { alignSelf: 'center', minHeight: 44, flexDirection: 'row', alignItems: 'center', gap: 6, paddingHorizontal: 16 },
});
