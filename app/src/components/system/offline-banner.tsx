import { useNetworkState } from 'expo-network';
import { useEffect, useRef } from 'react';
import { StyleSheet, View } from 'react-native';
import Animated, { FadeInUp, FadeOutUp } from 'react-native-reanimated';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { Icon } from '@/components/system/icon';
import { AppText } from '@/components/ui/app-text';
import { Brand } from '@/constants/brand';
import { flushOfflineOutbox } from '@/voice/outbox-worker';

// Global offline notice. Shown only when the OS reports no internet; answers
// and reports keep saving on the phone, so the message reassures instead of
// blocking. It never covers controls: a slim pill under the status bar.
// It also starts the outbox (voice/outbox-worker.ts): answers recorded with
// no network are sent when the phone comes back online, and once at start.

export function OfflineBanner() {
  const net = useNetworkState();
  const insets = useSafeAreaInsets();
  const offline = net.isConnected === false || net.isInternetReachable === false;
  // null until the OS has answered: neither "was offline" nor "came online".
  const known = net.isConnected !== undefined;
  const wasOffline = useRef<boolean | null>(null);
  useEffect(() => {
    if (!known) return;
    const before = wasOffline.current;
    wasOffline.current = offline;
    // offline -> online, or the first answer of the app run being "online".
    if (!offline && before !== false) flushOfflineOutbox().catch(() => {});
  }, [known, offline]);
  if (!offline) return null;
  return (
    <View pointerEvents="none" style={[styles.host, { top: insets.top + 4 }]}>
      <Animated.View
        entering={FadeInUp.duration(260)}
        exiting={FadeOutUp.duration(200)}
        style={styles.pill}
        accessibilityRole="alert"
        accessibilityLiveRegion="polite"
      >
        <Icon name="offline" size={16} color={Brand.onCherry} />
        <AppText variant="caption" color={Brand.onCherry} numberOfLines={1}>
          Offline · saved on this phone, will sync
        </AppText>
      </Animated.View>
    </View>
  );
}

const styles = StyleSheet.create({
  host: { position: 'absolute', left: 0, right: 0, alignItems: 'center', zIndex: 100 },
  pill: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
    paddingHorizontal: 14,
    paddingVertical: 6,
    borderRadius: 16,
    backgroundColor: Brand.ink,
  },
});
