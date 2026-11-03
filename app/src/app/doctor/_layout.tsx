import { router, Stack } from 'expo-router';
import { View } from 'react-native';

import { LiveBanner } from '@/components/system/live-banner';
import { Colors, Nav } from '@/constants/theme';
import { ME_DOCTOR } from '@/data/care-network';

// Doctor flow. The live banner sits above every doctor screen: a new alert,
// booking or ASHA message slides in and opens Notifications.
const ME = { kind: 'staff', id: ME_DOCTOR } as const;

export default function DoctorLayout() {
  return (
    <View style={{ flex: 1, backgroundColor: Colors.porcelain }}>
      <Stack screenOptions={{ headerShown: false, contentStyle: { backgroundColor: Colors.porcelain }, animation: 'ios_from_right', animationDuration: Nav.push }} />
      <LiveBanner to={ME} onOpen={() => router.push('/doctor/inbox')} />
    </View>
  );
}
