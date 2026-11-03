import { router } from 'expo-router';
import { useState } from 'react';
import { Pressable, StyleSheet, View } from 'react-native';

import { goBack } from '@/lib/nav';
import { NavCard } from '@/components/system/nav-card';
import { Screen } from '@/components/system/screen';
import { StateView } from '@/components/system/state-view';
import { AppText } from '@/components/ui/app-text';
import { Colors, Haptic } from '@/constants/theme';
import { ME_ASHA, fmtTime, isActive, network, staff, useNetwork, useNow, type Slot } from '@/data/care-network';
import { useAshaMothers } from '@/data/store';
import { useOnboarding, useT } from '@/features/onboarding/context';
import { whenLabel } from '@/lib/day-label';

// ASHA: the reminders dashboard. Every upcoming doctor visit of her women,
// soonest first; visits in the next 2 days are highlighted. Each row: the
// woman, the doctor and time, when she was last reminded, and ONE button,
// "Remind". The sticky button reminds everyone due in the next 2 days who
// has not been reminded today. The mother gets it in her app (SMS later).

const SOON = 2 * 86_400_000;
const REMINDED_RECENTLY = 12 * 60 * 60_000;

export default function Reminders() {
  const t = useT();
  const { language } = useOnboarding();
  const net = useNetwork();
  const women = useAshaMothers(ME_ASHA);
  const [sent, setSent] = useState<number | null>(null);
  const now = useNow();

  const visits = net.bookings
    .filter((b) => isActive(b) && women.some((m) => m.id === b.motherId))
    .map((b) => ({ b, s: net.slots.find((x) => x.id === b.slotId) }))
    .filter((x): x is { b: typeof x.b; s: Slot } => !!x.s && x.s.start > now)
    .sort((a, c) => a.s.start - c.s.start);
  const due = visits.filter(({ b, s }) => s.start < now + SOON && !(b.reminders[0] && now - b.reminders[0] < REMINDED_RECENTLY));

  const remind = (bookingId: string) => {
    Haptic.success();
    network.remindVisit(bookingId);
  };

  return (
    <Screen
      title={t('remindersTitle')}
      compactTitle={t('remindersTitle')}
      back={{ label: t('back'), onPress: () => goBack('/asha') }}
      cta={{
        label: t('remindAll'),
        enabled: due.length > 0,
        icon: 'send',
        haptic: 'success',
        context: due.length ? t('remindersHint', { n: due.length }) : undefined,
        onPress: () => {
          due.forEach(({ b }) => network.remindVisit(b.id));
          setSent(due.length);
          Haptic.success();
        },
      }}
    >
      {sent !== null && (
        <View style={styles.done} accessibilityLiveRegion="polite">
          <AppText variant="bodyStrong" color={Colors.sage}>
            {`${t('remindDone')} · ${sent}`}
          </AppText>
        </View>
      )}
      {visits.length === 0 ? (
        <StateView title={t('noVisits')} body={t('visitsTitle')} icon="calendar" action={{ label: t('visitsTitle'), onPress: () => router.replace('/asha/visits') }} />
      ) : (
        <View style={styles.list}>
          {visits.map(({ b, s }) => {
            const m = women.find((x) => x.id === b.motherId);
            const soon = s.start < now + SOON;
            const last = b.reminders[0];
            const recent = !!last && now - last < REMINDED_RECENTLY;
            return (
              <NavCard
                key={b.id}
                initial={m?.name.slice(0, 1)}
                title={m?.name ?? ''}
                line={`${staff(s.doctorId)?.name} · ${whenLabel(s.start, language)}\n${last ? t('remindedAt', { time: fmtTime(last) }) : t('notReminded')}`}
                emphasis={soon && !recent}
                action={
                  <Pressable
                    accessibilityRole="button"
                    accessibilityLabel={`${t('remind')} ${m?.name ?? ''}`}
                    onPress={() => remind(b.id)}
                    style={({ pressed }) => [styles.remind, recent && styles.remindDone, pressed && styles.pressed]}
                  >
                    <AppText variant="buttonSmall" color={recent ? Colors.sage : Colors.onCherry}>
                      {recent ? '✓' : t('remind')}
                    </AppText>
                  </Pressable>
                }
              />
            );
          })}
        </View>
      )}
    </Screen>
  );
}

const styles = StyleSheet.create({
  pressed: { transform: [{ scale: 0.96 }] },
  list: { gap: 10 },
  done: { padding: 14, borderRadius: 16, backgroundColor: Colors.sageTint },
  remind: { minWidth: 88, minHeight: 44, alignItems: 'center', justifyContent: 'center', paddingHorizontal: 14, borderRadius: 22, backgroundColor: Colors.cherry },
  remindDone: { backgroundColor: Colors.sageTint },
});
