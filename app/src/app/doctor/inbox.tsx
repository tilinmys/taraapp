import { router } from 'expo-router';
import { useEffect, useState } from 'react';
import { StyleSheet, View } from 'react-native';
import { useSharedValue } from 'react-native-reanimated';

import { goBack } from '@/lib/nav';
import { VisitThread } from '@/components/care/visit-thread';
import { NavCard } from '@/components/system/nav-card';
import { Screen } from '@/components/system/screen';
import { StateView } from '@/components/system/state-view';
import { AppText } from '@/components/ui/app-text';
import { BottomSheet } from '@/components/ui/bottom-sheet';
import { Colors } from '@/constants/theme';
import { ME_DOCTOR, fmtWhen, inbox, isActive, network, useNetwork, type Notice } from '@/data/care-network';
import { useMothers } from '@/data/store';
import type { AppIconName } from '@/components/system/app-icon';

// Doctor: notifications (the bell on his home). Newest first; new ones are
// highlighted until he leaves the page. One tap does the obvious thing:
//   high-risk alert from an ASHA   -> opens that mother's case
//   booking / ASHA message         -> opens the visit's message thread (reply)
//   anything else                  -> nothing to open

const ICON: Record<Notice['kind'], AppIconName> = {
  high_risk_flag: 'alert',
  visit_comment: 'talk',
  booking_confirmed: 'calendar',
  booking_cancelled: 'calendar',
  slots_published: 'calendar',
  visit_reminder: 'calendar',
  visit_today: 'ashaWorker',
  checklist_updated: 'checklist',
  appointment_update: 'calendar',
  advice_sent: 'checklist',
  sos: 'ambulance',
};

export default function DoctorInbox() {
  const net = useNetwork();
  const mothers = useMothers();
  const sheet = useSharedValue(0);
  const [threadId, setThreadId] = useState<string | null>(null);
  const list = inbox(net, { kind: 'staff', id: ME_DOCTOR }).sort((a, b) => b.at - a.at);
  const threadBooking = net.bookings.find((b) => b.id === threadId);
  const threadMother = mothers.find((m) => m.id === threadBooking?.motherId)?.name ?? '';

  // Seen once he leaves the page (the highlight stays while he reads).
  useEffect(() => () => network.markRead({ kind: 'staff', id: ME_DOCTOR }), []);

  const open = (n: Notice) => {
    if (n.kind === 'high_risk_flag' && n.ref?.motherId) return () => router.push({ pathname: '/doctor/case/[id]', params: { id: n.ref!.motherId! } });
    if (n.ref?.bookingId && net.bookings.some((b) => b.id === n.ref?.bookingId && isActive(b))) return () => setThreadId(n.ref!.bookingId!);
    return undefined;
  };

  return (
    <Screen
      title="Notifications"
      compactTitle="Notifications"
      back={{ label: 'Home', onPress: () => goBack('/doctor') }}
      sheetProgress={sheet}
      overlay={
        <BottomSheet open={!!threadId} onClose={() => setThreadId(null)} progress={sheet} title="Messages about this visit" closeLabel="Close">
          {threadId && <VisitThread bookingId={threadId} me="doctor" myId={ME_DOCTOR} motherName={threadMother} />}
        </BottomSheet>
      }
    >
      {list.length === 0 ? (
        <StateView title="No notifications" body="ASHA alerts, bookings and messages show here." icon="bell" />
      ) : (
        <View style={styles.list}>
          {list.map((n) => (
            <NavCard
              key={n.id}
              icon={ICON[n.kind]}
              title={n.title}
              line={`${n.body.split('\n').join(' ')}\n${fmtWhen(n.at)}`}
              status={!n.read ? { label: 'New', tone: n.kind === 'high_risk_flag' ? 'alert' : 'info' } : undefined}
              emphasis={!n.read && n.kind === 'high_risk_flag'}
              onPress={open(n)}
            />
          ))}
        </View>
      )}
      <AppText variant="caption" color={Colors.inkMuted}>
        Tap an alert to open the case, or a booking to reply to the ASHA.
      </AppText>
    </Screen>
  );
}

const styles = StyleSheet.create({
  list: { gap: 10 },
});
