import { router, Stack } from 'expo-router';
import { View } from 'react-native';

import { SosLayer } from '@/components/mother/sos';
import { LiveBanner } from '@/components/system/live-banner';
import { Colors, Nav } from '@/constants/theme';
import { useMother } from '@/data/store';
import { useOnboarding } from '@/features/onboarding/context';

// Sign-in lands on the tabs, where the baby says hello in an overlay (see
// (tabs)/_layout.tsx). Topic pages push over the tabs with the iOS slide.
// The live banner sits above every mother screen: a new notification (her
// ASHA reminds her, a visit is booked) slides in and opens Notifications.
// SOS (components/mother/sos.tsx) is above everything, on every screen.
export default function MotherLayout() {
  const { demoMother } = useOnboarding();
  const mother = useMother(demoMother);
  return (
    <View style={{ flex: 1, backgroundColor: Colors.porcelain }}>
      <Stack screenOptions={{ headerShown: false, contentStyle: { backgroundColor: Colors.porcelain }, animation: 'ios_from_right', animationDuration: Nav.push }}>
        <Stack.Screen name="(tabs)" options={{ animation: 'fade' }} />
        {/* Swipe back from anywhere on the page, following the finger. */}
        <Stack.Screen name="topic/[id]" options={{ gestureEnabled: true, fullScreenGestureEnabled: true }} />
        <Stack.Screen name="doctors" options={{ gestureEnabled: true, fullScreenGestureEnabled: true }} />
        <Stack.Screen name="health" options={{ gestureEnabled: true, fullScreenGestureEnabled: true }} />
        <Stack.Screen name="notifications" options={{ gestureEnabled: true, fullScreenGestureEnabled: true }} />
      </Stack>
      <LiveBanner to={demoMother ? { kind: 'mother', id: demoMother } : null} onOpen={() => router.push('/mother/notifications')} />
      <SosLayer mother={mother} />
    </View>
  );
}
