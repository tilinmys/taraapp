import { router, Stack } from 'expo-router';
import { View } from 'react-native';

import { LiveBanner } from '@/components/system/live-banner';
import { Colors, Nav } from '@/constants/theme';
import { ME_ASHA } from '@/data/care-network';

// ASHA flow: home -> village -> her page -> Sakhi conversation -> summary.
// Parallax push on both platforms ('ios_from_right' gives Android the same
// spring-like iOS slide). Swipe-back is off inside the conversation so a
// stray edge swipe never abandons a check-in; the labelled Exit button and
// its confirmation are the way out. The live banner sits above every ASHA
// screen: a new notification slides in and opens Notifications.
const ME = { kind: 'staff', id: ME_ASHA } as const;

export default function AshaLayout() {
  return (
    <View style={{ flex: 1, backgroundColor: Colors.porcelain }}>
      <Stack
        screenOptions={{ headerShown: false, contentStyle: { backgroundColor: Colors.porcelain }, animation: 'ios_from_right', animationDuration: Nav.push }}
      >
        <Stack.Screen name="add" options={{ animation: 'slide_from_bottom' }} />
        <Stack.Screen name="session/[id]" options={{ gestureEnabled: false }} />
        <Stack.Screen name="summary/[id]" options={{ gestureEnabled: false, animation: 'fade' }} />
      </Stack>
      <LiveBanner to={ME} onOpen={() => router.push('/asha/messages')} />
    </View>
  );
}
