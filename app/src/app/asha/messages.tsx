import { router } from 'expo-router';
import { useEffect, useState } from 'react';
import { StyleSheet, View } from 'react-native';
import { useSharedValue } from 'react-native-reanimated';

import { goBack } from '@/lib/nav';
import { VisitThread } from '@/components/care/visit-thread';
import { NavCard } from '@/components/system/nav-card';
import { Screen } from '@/components/system/screen';
import { StateView } from '@/components/system/state-view';
import { BottomSheet } from '@/components/ui/bottom-sheet';
import { ME_ASHA, NOTICE_ICONS, fmtTime, inbox, network, useNetwork, type Notice } from '@/data/care-network';
import { sosStore } from '@/data/sos';
import { useMothers } from '@/data/store';
import { useOnboarding, useT } from '@/features/onboarding/context';
import { dayLabel } from '@/lib/day-label';
import { noticeText } from '@/lib/notice-text';
import type { AppIconName } from '@/components/system/app-icon';

// ASHA: messages (a doctor opened times, a booking, a doctor's reply).
// Newest first; unread ones are highlighted and marked read when she
// leaves. One tap does the obvious thing:
//   doctor's reply           -> the visit's message thread (answer him)
//   about one of her women   -> that woman's page
//   doctor opened times      -> Doctor visits
//   SOS                      -> "I am going" (the mother sees it) + her page

// The ASHA's inbox marks three kinds by what she does next, not as a date.
const ICON: Record<Notice['kind'], AppIconName> = {
  ...NOTICE_ICONS,
  booking_confirmed: 'checklist',
  booking_cancelled: 'alert',
  visit_reminder: 'voice',
};

export default function Messages() {
  const t = useT();
  const { language } = useOnboarding();
  const net = useNetwork();
  const mothers = useMothers();
  const sheet = useSharedValue(0);
  const [threadId, setThreadId] = useState<string | null>(null);
  const list = inbox(net, { kind: 'staff', id: ME_ASHA }).sort((a, b) => b.at - a.at);
  const threadMother = mothers.find((m) => m.id === net.bookings.find((b) => b.id === threadId)?.motherId)?.name ?? '';

  // Seen once she has looked (on leaving, so the highlight is visible now).
  useEffect(() => () => network.markRead({ kind: 'staff', id: ME_ASHA }), []);

  const open = (n: Notice) => {
    // SOS: tapping it means "I am going": the mother sees who is coming.
    if (n.kind === 'sos' && n.ref?.motherId)
      return () => {
        sosStore.accept(ME_ASHA);
        router.push({ pathname: '/asha/mother/[id]', params: { id: n.ref!.motherId! } });
      };
    if (n.kind === 'visit_comment' && n.ref?.bookingId) return () => setThreadId(n.ref!.bookingId!);
    if (n.ref?.motherId) return () => router.push({ pathname: '/asha/mother/[id]', params: { id: n.ref!.motherId! } });
    if (n.kind === 'slots_published') return () => router.push('/asha/visits');
    return undefined;
  };

  return (
    <Screen
      title={t('notifications')}
      compactTitle={t('notifications')}
      back={{ label: t('back'), onPress: () => goBack('/asha') }}
      sheetProgress={sheet}
      overlay={
        <BottomSheet open={!!threadId} onClose={() => setThreadId(null)} progress={sheet} title={t('visitMessages')} closeLabel={t('back')}>
          {threadId && <VisitThread bookingId={threadId} me="asha" myId={ME_ASHA} motherName={threadMother} />}
        </BottomSheet>
      }
    >
      {list.length === 0 ? (
        <StateView title={t('noMessages')} body="" icon="chat" />
      ) : (
        <View style={styles.list}>
          {list.map((n) => (
            <NavCard
              key={n.id}
              icon={ICON[n.kind]}
              title={noticeText(n, t, language).title}
              titleLines={2}
              line={`${noticeText(n, t, language).body}\n${dayLabel(n.at, language)}, ${fmtTime(n.at)}`}
              emphasis={!n.read}
              status={!n.read ? { label: '•', tone: 'alert' } : undefined}
              onPress={open(n)}
            />
          ))}
        </View>
      )}
    </Screen>
  );
}

const styles = StyleSheet.create({
  list: { gap: 10 },
});
