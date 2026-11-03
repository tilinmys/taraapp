import { router } from 'expo-router';
import { useEffect } from 'react';
import { StyleSheet, View } from 'react-native';

import { goBack } from '@/lib/nav';
import { MissingMother } from '@/components/mother/missing-mother';
import type { AppIconName } from '@/components/system/app-icon';
import { NavCard } from '@/components/system/nav-card';
import { Screen } from '@/components/system/screen';
import { StateView } from '@/components/system/state-view';
import { fmtTime, inbox, network, useNetwork, type Notice } from '@/data/care-network';
import { useMother } from '@/data/store';
import { useOnboarding, useT } from '@/features/onboarding/context';
import { dayLabel } from '@/lib/day-label';
import { noticeText } from '@/lib/notice-text';

// Mother: notifications (the bell on her Home). What her ASHA and doctor did
// for her, newest first, in HER language: a visit booked or cancelled, a
// reminder, "Sunita will visit you today", a changed checklist. New ones are
// highlighted until she leaves. One tap opens the matching page.

const ICON: Record<Notice['kind'], AppIconName> = {
  booking_confirmed: 'calendar',
  booking_cancelled: 'calendar',
  visit_reminder: 'calendar',
  visit_today: 'ashaWorker',
  checklist_updated: 'checklist',
  appointment_update: 'calendar',
  advice_sent: 'checklist',
  sos: 'ambulance',
  slots_published: 'calendar',
  visit_comment: 'talk',
  high_risk_flag: 'alert',
};

export default function MotherNotifications() {
  const t = useT();
  const { demoMother, language } = useOnboarding();
  const mother = useMother(demoMother);
  const net = useNetwork();
  const id = mother?.id;

  // Seen once she leaves the page (the highlight stays while she reads).
  useEffect(() => () => void (id && network.markRead({ kind: 'mother', id })), [id]);

  if (!mother) return <MissingMother />;
  const list = inbox(net, { kind: 'mother', id: mother.id }).sort((a, b) => b.at - a.at);
  const open = (n: Notice) =>
    n.kind === 'checklist_updated'
      ? () => router.navigate('/mother')
      : n.kind.startsWith('booking') || n.kind === 'visit_reminder'
        ? () => router.push('/mother/doctors')
        : undefined;

  return (
    <Screen
      title={t('notifications')}
      titleVariant="greeting"
      compactTitle={t('notifications')}
      back={{ label: t('back'), onPress: () => goBack('/mother') }}
    >
      {list.length === 0 ? (
        <StateView title={t('noNotifications')} body={t('noNotificationsMother')} icon="bell" />
      ) : (
        <View style={styles.list}>
          {list.map((n) => {
            const x = noticeText(n, t, language);
            return (
              <NavCard
                key={n.id}
                icon={ICON[n.kind]}
                title={x.title}
                line={`${x.body}\n${dayLabel(n.at, language)}, ${fmtTime(n.at)}`}
                titleLines={2}
                status={!n.read ? { label: t('newBadge'), tone: 'info' } : undefined}
                emphasis={!n.read}
                onPress={open(n)}
              />
            );
          })}
        </View>
      )}
    </Screen>
  );
}

const styles = StyleSheet.create({
  list: { gap: 10 },
});
