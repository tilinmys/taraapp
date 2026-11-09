import { router } from 'expo-router';
import { useEffect, useState } from 'react';
import { Linking, Pressable, StyleSheet, View } from 'react-native';
import Animated, { cancelAnimation, useAnimatedStyle, useSharedValue, withRepeat, withSpring, withTiming } from 'react-native-reanimated';

import { MissingMother } from '@/components/mother/missing-mother';
import { ChecklistCard, MedicinesCard, MotherChecklistSheet } from '@/components/mother/today-cards';
import { AppIcon, type AppIconName } from '@/components/system/app-icon';
import { Icon } from '@/components/system/icon';
import { Rise } from '@/components/system/rise';
import { Screen } from '@/components/system/screen';
import { AppText } from '@/components/ui/app-text';
import { BottomSheet } from '@/components/ui/bottom-sheet';
import { Colors, Haptic, Physics } from '@/constants/theme';
import { useCarePlan } from '@/data/care-plan';
import { weekOf } from '@/data/pregnancy';
import { ashaForZone, doctorsOf, fmtTime, inbox, staff, upcomingBooking, useNetwork, zone } from '@/data/care-network';
import { useMother, type MotherRecord } from '@/data/store';
import { useOnboarding, useT } from '@/features/onboarding/context';
import { STATUS_PILL } from '@/lib/appt-status';
import { whenLabel } from '@/lib/day-label';
import { useSpeech } from '@/hooks/use-speech';
import { tx } from '@/i18n/content';
import type { Lang } from '@/i18n/strings';

// Mother home, "Care begins with you." Built for a first-time, rural user:
// big pictures, one idea per card, the same card shape everywhere.
//   (The weekly story card is removed: her week is in the header.)
//   2 Talk to Tara   THE main action: one big card, the talk icon
//   3 Quick tiles    three equal picture tiles: My health · Book doctor ·
//                    Kicks, so her readings are never more than one tap away
//   4 Today          her doctor visit (or "Book a visit"), today's checklist
//                    and today's medicines
//   5 Care Circle    her ASHA: Call · each doctor: Book · 108: Call
// No risk labels or alerts anywhere on her side: clinical status belongs to
// the ASHA and the doctor.

function PressCard({ onPress, label, style, children }: { onPress: () => void; label: string; style: object; children: React.ReactNode }) {
  const press = useSharedValue(1);
  const a = useAnimatedStyle(() => ({ transform: [{ scale: press.get() }] }));
  return (
    <Animated.View style={a}>
      <Pressable
        accessibilityRole="button"
        accessibilityLabel={label}
        onPressIn={() => press.set(withSpring(Physics.pressScale, Physics.press))}
        onPressOut={() => press.set(withSpring(1, Physics.press))}
        onPress={() => {
          Haptic.light();
          onPress();
        }}
        style={style}
      >
        {children}
      </Pressable>
    </Animated.View>
  );
}

/** One of the three equal picture tiles under Talk. */
function QuickTile({ icon, label, hint, onPress }: { icon: AppIconName; label: string; hint: string; onPress: () => void }) {
  return (
    <View style={styles.tileCell}>
      <PressCard label={`${label}. ${hint}`} onPress={onPress} style={styles.tile}>
        <View style={styles.tileWell}>
          <AppIcon name={icon} size={44} />
        </View>
        <AppText variant="label" align="center" numberOfLines={2}>
          {label}
        </AppText>
      </PressCard>
    </View>
  );
}

/**
 * Her doctor visit. Booked: the day and time, the doctor, who booked it.
 * Not booked: a clear "Book a visit" that opens the booking page.
 */
function NextVisit({ mother }: { mother: MotherRecord }) {
  const t = useT();
  const { language } = useOnboarding();
  const net = useNetwork();
  const asha = ashaForZone(mother.zoneId);
  const b = upcomingBooking(mother.id, net);
  const slot = b && net.slots.find((x) => x.id === b.slotId);
  const doctor = slot && staff(slot.doctorId);
  const reminder = inbox(net, { kind: 'mother', id: mother.id }).find((n) => n.kind === 'visit_reminder');
  const title = slot ? whenLabel(slot.start, language) : t('bookAVisit');
  const line = slot
    ? b?.bookedBy === 'mother'
      ? t('visitYou', { doctor: doctor?.name ?? '' })
      : b?.bookedBy === 'doctor'
        ? doctor?.name ?? ''
        : t('visitByAsha', { doctor: doctor?.name ?? '', asha: asha?.name ?? '' })
    : t('pickSuits');
  // Its status in one word: Requested (her ASHA confirms it with her),
  // Confirmed, Rescheduled.
  const pill = b ? STATUS_PILL[b.status] : null;
  const pillColor = pill?.tone === 'ok' ? Colors.sage : pill?.tone === 'alert' ? Colors.cherry : Colors.plum;
  return (
    <PressCard label={`${t('yourVisit')}. ${title}. ${line}.`} onPress={() => router.push('/mother/doctors')} style={[styles.visit, slot ? styles.visitBooked : null]}>
      <View style={styles.iconWell}>
        <AppIcon name="calendar" size={38} />
      </View>
      <View style={styles.flex}>
        <AppText variant="overline" color={Colors.inkMuted}>
          {t('yourVisit').toLocaleUpperCase()}
        </AppText>
        <AppText variant="heading" numberOfLines={1}>
          {title}
        </AppText>
        {!!pill && (
          <AppText variant="label" color={pillColor} numberOfLines={1}>
            {`● ${t(pill.key)}`}
          </AppText>
        )}
        <AppText variant="caption" color={Colors.inkMuted} numberOfLines={2}>
          {line}
        </AppText>
        {!!reminder && !!slot && (
          <AppText variant="label" color={Colors.cherry} numberOfLines={1}>
            {t('remindedYou', { asha: asha?.name ?? '', time: fmtTime(reminder.at) })}
          </AppText>
        )}
      </View>
      <Icon name="forward" size={20} color={Colors.cherry} weight="bold" />
    </PressCard>
  );
}

/** The doctor's advice, once he sent it: numbered steps, who, when. */
/** The language a text is written in (the doctor writes in English, or in her language). */
const scriptLang = (text: string, fallback: Lang): Lang => (/[ಀ-೿]/.test(text) ? 'kn' : /[ऀ-ॿ]/.test(text) ? 'hi' : /[a-z]/i.test(text) ? 'en' : fallback);

function DoctorAdvice({ mother }: { mother: MotherRecord }) {
  const t = useT();
  const { language } = useOnboarding();
  // In her language when the advice is one of the known lines; a doctor's own words as he wrote them.
  const advice = (mother.advice ?? []).map((a) => tx(a, mother.language ?? language));
  const text = advice.map((a, i) => `${i + 1}. ${a}`).join(' ');
  // Read in the language the advice is written in, so the voice says the words right.
  const speech = useSpeech(scriptLang(text, mother.language ?? language));
  const playing = speech.speakingId === 'advice';
  const pulse = useSharedValue(1);
  const pulseStyle = useAnimatedStyle(() => ({ transform: [{ scale: pulse.get() }] }));
  // The icon pulses while she listens, and rests when the reading ends (or is stopped).
  useEffect(() => {
    if (playing) pulse.set(withRepeat(withTiming(1.18, { duration: 600 }), -1, true));
    else {
      cancelAnimation(pulse);
      pulse.set(withTiming(1));
    }
  }, [playing, pulse]);
  if (!mother.advice?.length || !mother.adviceAt) return null;
  const toggle = () => {
    Haptic.light();
    speech.speak('advice', text);
  };
  return (
    <View style={styles.advice} accessible={false} accessibilityLabel={`${t('doctorAdvice')}. ${advice.join('. ')}`}>
      <View style={styles.adviceHead}>
        <AppIcon name="doctor" size={36} />
        <View style={styles.flex}>
          <AppText variant="heading">{t('doctorAdvice')}</AppText>
          <AppText variant="caption" color={Colors.inkMuted}>
            {t('adviceFrom', { doctor: staff(mother.adviceBy ?? '')?.name ?? '', when: whenLabel(mother.adviceAt, language) })}
          </AppText>
        </View>
      </View>
      {/* Listen: the phone reads the advice aloud (she may not read). */}
      <Pressable
        accessibilityRole="button"
        accessibilityState={{ selected: playing }}
        accessibilityLabel={playing ? t('stopListening') : t('listenResults')}
        onPress={toggle}
        style={({ pressed }) => [styles.listen, playing && styles.listenOn, pressed && styles.pressedSoft]}
      >
        <Animated.View style={pulseStyle}>
          <Icon name={playing ? 'close' : 'speaker'} size={18} color={playing ? Colors.onCherry : Colors.cherry} />
        </Animated.View>
        <AppText variant="buttonSmall" color={playing ? Colors.onCherry : Colors.cherry}>
          {playing ? t('stopListening') : t('listenResults')}
        </AppText>
      </Pressable>
      {advice.map((a, i) => (
        <View key={`${i}-${a}`} style={styles.adviceRow}>
          <AppText variant="label" color={Colors.cherry} style={styles.adviceNum}>
            {`${i + 1}.`}
          </AppText>
          <AppText variant="body" style={styles.flex}>
            {a}
          </AppText>
        </View>
      ))}
    </View>
  );
}

type Contact = { id: string; name: string; role: string; icon: AppIconName; action: 'call' | 'book'; phone?: string };
type T = ReturnType<typeof useT>;

/** Her Care Circle: HER village's ASHA (call), her PHC's doctors (book), 108 (call). */
function careCircle(mother: MotherRecord, t: T, lang: Lang): Contact[] {
  const asha = ashaForZone(mother.zoneId);
  const phc = zone(mother.zoneId)?.phcId ?? 'hosahalli';
  return [
    ...(asha ? [{ id: asha.id, name: asha.name, role: t('yourAsha'), phone: asha.phone, icon: 'ashaWorker' as const, action: 'call' as const }] : []),
    ...doctorsOf(phc).map((d) => ({ id: d.id, name: d.name, role: tx(d.title ?? 'Doctor', lang), icon: 'doctor' as const, action: 'book' as const })),
    { id: '108', name: '108', role: t('ambulance'), phone: '108', icon: 'ambulance' as const, action: 'call' as const },
  ];
}

export default function MotherHome() {
  const { demoMother } = useOnboarding();
  const mother = useMother(demoMother);
  if (!mother) return <MissingMother />;
  // The baby's hello plays over these tabs (see (tabs)/_layout.tsx).
  // Keyed by mother so everything resets when the mother changes.
  return <Dashboard key={mother.id} mother={mother} />;
}

function Dashboard({ mother }: { mother: MotherRecord }) {
  const week = weekOf(mother.weeksLabel);
  const t = useT();
  const { language } = useOnboarding();
  const net = useNetwork();
  const unread = inbox(net, { kind: 'mother', id: mother.id }).filter((n) => !n.read).length;
  const plan = useCarePlan(mother.id);
  const [checklist, setChecklist] = useState(false);
  // Bumped when she saves a fully ticked day: re-mounts <Burst> so it plays once.
  const [cheers, setCheers] = useState(0);
  const sheet = useSharedValue(0);

  return (
    <Screen
      inTabs
      eyebrow={mother.weeksLabel}
      title={t('homeTitle')}
      titleVariant="greeting"
      compactTitle={t('weekN', { n: week })}
      right={{ label: t('profile'), icon: 'profile', art: 'profile', onPress: () => router.push('/profile') }}
      rightExtra={{ label: t('notifications'), icon: 'bell', badge: unread, onPress: () => router.push('/mother/notifications') }}
      sheetProgress={sheet}
      overlay={
        <BottomSheet open={checklist} onClose={() => setChecklist(false)} progress={sheet} title={t('todayChecklist')} closeLabel={t('close')}>
          {checklist && (
            <MotherChecklistSheet
              plan={plan}
              asha={mother.asha}
              onSaved={(allDone) => {
                setChecklist(false);
                if (allDone) setCheers((c) => c + 1);
              }}
            />
          )}
        </BottomSheet>
      }
    >
      {/* 2 Talk to Tara: the main action */}
      <Rise index={0}>
        <PressCard label={`${t('talkTara')}. ${t('talkTaraHint')}`} onPress={() => router.navigate('/mother/talk')} style={styles.talk}>
          <View style={styles.talkWell}>
            <AppIcon name="talk" size={52} />
          </View>
          <View style={styles.flex}>
            <AppText variant="heading" color={Colors.onCherry}>
              {t('talkTara')}
            </AppText>
            <AppText variant="caption" color={Colors.onCherry}>
              {t('talkTaraHint')}
            </AppText>
          </View>
          <View style={styles.talkGo}>
            <Icon name="arrow" size={22} color={Colors.cherry} weight="bold" />
          </View>
        </PressCard>
      </Rise>

      {/* 3 Quick tiles: three equal pictures */}
      <Rise index={1} style={styles.tiles}>
        <QuickTile icon="scan" label={t('myHealth')} hint={t('myHealthHint')} onPress={() => router.push('/mother/health')} />
        <QuickTile icon="doctor" label={t('bookDoctor')} hint={t('bookDoctorHint')} onPress={() => router.push('/mother/doctors')} />
        <QuickTile icon="kicks" label={t('countKicks')} hint={t('countKicksHint')} onPress={() => router.navigate({ pathname: '/mother/pregnancy', params: { open: 'kicks' } })} />
      </Rise>

      {/* 4 Today */}
      <Rise index={2} style={styles.section}>
        <AppText variant="heading" accessibilityRole="header">
          {t('today')}
        </AppText>
        <NextVisit mother={mother} />
        <DoctorAdvice mother={mother} />
        <ChecklistCard plan={plan} cheers={cheers} onOpen={() => setChecklist(true)} />
        <MedicinesCard />
      </Rise>

      {/* 5 Care Circle */}
      <Rise index={3} style={styles.section}>
        <AppText variant="heading" accessibilityRole="header">
          {t('careCircle')}
        </AppText>
        <View style={styles.circle}>
          {careCircle(mother, t, language).map((c, i) => (
            <View key={c.id} style={[styles.contact, i > 0 && styles.divider]}>
              <AppIcon name={c.icon} size={40} />
              <View style={styles.flex}>
                <AppText variant="bodyStrong">{c.name}</AppText>
                <AppText variant="caption" color={Colors.inkMuted}>
                  {c.role}
                </AppText>
              </View>
              <Pressable
                accessibilityRole="button"
                accessibilityLabel={`${c.action === 'book' ? t('book') : t('call')}: ${c.name}`}
                onPress={() => {
                  Haptic.medium();
                  if (c.action === 'book') router.push({ pathname: '/mother/doctors', params: { doctor: c.id } });
                  else Linking.openURL(`tel:${c.phone}`).catch(() => {});
                }}
                style={({ pressed }) => [styles.action, c.action === 'book' && styles.actionBook, pressed && styles.pressed]}
              >
                <Icon name={c.action === 'book' ? 'calendar' : 'phone'} size={18} color={c.action === 'book' ? Colors.onCherry : Colors.cherry} weight="fill" />
                <AppText variant="buttonSmall" color={c.action === 'book' ? Colors.onCherry : Colors.cherry}>
                  {c.action === 'book' ? t('book') : t('call')}
                </AppText>
              </Pressable>
            </View>
          ))}
        </View>
      </Rise>
    </Screen>
  );
}

const styles = StyleSheet.create({
  flex: { flex: 1, minWidth: 0 },
  pressed: { opacity: 0.7 },
  section: { gap: 10 },
  iconWell: { width: 52, height: 52, borderRadius: 16, alignItems: 'center', justifyContent: 'center', backgroundColor: Colors.surface },
  talk: { minHeight: 96, flexDirection: 'row', alignItems: 'center', gap: 14, padding: 16, borderRadius: 24, backgroundColor: Colors.cherry },
  talkWell: { width: 68, height: 68, borderRadius: 22, alignItems: 'center', justifyContent: 'center', backgroundColor: Colors.surface },
  talkGo: { width: 44, height: 44, borderRadius: 22, alignItems: 'center', justifyContent: 'center', backgroundColor: Colors.surface },
  tiles: { flexDirection: 'row', gap: 16 },
  tileCell: { flex: 1 },
  tile: { minHeight: 120, alignItems: 'center', justifyContent: 'center', gap: 8, paddingHorizontal: 6, paddingVertical: 12, borderRadius: 20, borderWidth: 1, borderColor: Colors.bandLine, backgroundColor: Colors.surface },
  tileWell: { width: 60, height: 60, borderRadius: 18, alignItems: 'center', justifyContent: 'center', backgroundColor: Colors.shell },
  visit: { flexDirection: 'row', alignItems: 'center', gap: 14, padding: 14, borderRadius: 18, backgroundColor: Colors.band, borderWidth: 1, borderColor: Colors.bandLine },
  advice: { gap: 10, padding: 14, borderRadius: 18, backgroundColor: Colors.surface, borderWidth: 1, borderColor: Colors.bandLine },
  adviceHead: { flexDirection: 'row', alignItems: 'center', gap: 12 },
  listen: { alignSelf: 'flex-start', minHeight: 44, flexDirection: 'row', alignItems: 'center', gap: 8, paddingHorizontal: 16, borderRadius: 22, borderWidth: 1.5, borderColor: Colors.cherry, backgroundColor: Colors.surface },
  listenOn: { backgroundColor: Colors.cherry },
  pressedSoft: { opacity: 0.8 },
  adviceRow: { flexDirection: 'row', alignItems: 'flex-start', gap: 8 },
  adviceNum: { width: 22, paddingTop: 2 },
  visitBooked: { backgroundColor: Colors.sageTint, borderColor: `${Colors.sage}33` },
  circle: { borderRadius: 20, backgroundColor: Colors.shell, overflow: 'hidden' },
  contact: { flexDirection: 'row', alignItems: 'center', gap: 12, paddingHorizontal: 14, paddingVertical: 12 },
  divider: { borderTopWidth: 1, borderTopColor: Colors.hairline },
  action: {
    minWidth: 88,
    minHeight: 44,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 6,
    paddingHorizontal: 14,
    borderRadius: 22,
    borderWidth: 1.5,
    borderColor: Colors.cherry,
    backgroundColor: Colors.surface,
  },
  actionBook: { backgroundColor: Colors.cherry },
});
