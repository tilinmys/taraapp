import { useEffect, useRef, useState } from 'react';
import { Pressable, StyleSheet, View } from 'react-native';
import Animated, { useAnimatedStyle, useReducedMotion, useSharedValue, withSpring, withTiming } from 'react-native-reanimated';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { AppIcon, type AppIconName } from '@/components/system/app-icon';
import { AppText } from '@/components/ui/app-text';
import { Colors, Haptic } from '@/constants/theme';
import { network, type Notice } from '@/data/care-network';
import { useOnboarding, useT } from '@/features/onboarding/context';
import { noticeText } from '@/lib/notice-text';

// The live part of notifications, inside the app: the moment a new notice
// for THIS user arrives (a booking, a reminder, a doctor's reply, a
// high-risk alert), a banner slides down from the top for 5 seconds, with
// a gentle haptic. Tap it to open the notifications page; it never blocks
// the screen. Only notices created after the workspace opened are shown
// (no flood of old ones), and one at a time (the newest wins).
// Outside the app the same notice goes out as a phone push
// (docs/NOTIFICATIONS_DESIGN.md).

const SHOW_MS = 5000;

const ICON: Record<Notice['kind'], AppIconName> = {
  slots_published: 'calendar',
  booking_confirmed: 'calendar',
  booking_cancelled: 'calendar',
  visit_reminder: 'calendar',
  visit_comment: 'talk',
  high_risk_flag: 'alert',
  visit_today: 'ashaWorker',
  checklist_updated: 'checklist',
  appointment_update: 'calendar',
  advice_sent: 'checklist',
  sos: 'ambulance',
};

export function LiveBanner({ to, onOpen }: { to: Notice['to'] | null; onOpen: () => void }) {
  const insets = useSafeAreaInsets();
  const t = useT();
  const { language } = useOnboarding();
  const reduceMotion = useReducedMotion();
  const [notice, setNotice] = useState<Notice | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  // Slide down + fade in, run by hand on transform / opacity (Reanimated's
  // layout "entering" presets stall off-screen on web).
  const shown = useSharedValue(0);
  const style = useAnimatedStyle(() => ({ opacity: shown.get(), transform: [{ translateY: (1 - shown.get()) * -24 }] }));
  useEffect(() => {
    if (!notice) return;
    shown.set(0);
    shown.set(reduceMotion ? withTiming(1, { duration: 150 }) : withSpring(1, { stiffness: 220, damping: 20 }));
  }, [notice, reduceMotion, shown]);

  useEffect(() => {
    if (!to) return;
    const since = Date.now();
    const seen = new Set<string>();
    // A subscription callback (not render) decides what to show.
    const unsub = network.subscribe(() => {
      const fresh = network
        .get()
        .notices.filter((n) => n.to.kind === to.kind && n.to.id === to.id && !n.read && n.at >= since && !seen.has(n.id))
        .sort((a, b) => b.at - a.at)[0];
      if (!fresh) return;
      seen.add(fresh.id);
      Haptic.light();
      setNotice(fresh);
      if (timer.current) clearTimeout(timer.current);
      timer.current = setTimeout(() => setNotice(null), SHOW_MS);
    });
    return () => {
      unsub();
      if (timer.current) clearTimeout(timer.current);
    };
  }, [to?.kind, to?.id]); // eslint-disable-line react-hooks/exhaustive-deps

  if (!notice) return null;
  const text = noticeText(notice, t, language);
  return (
    <View pointerEvents="box-none" style={[styles.anchor, { top: insets.top + 8 }]}>
      <Animated.View
        style={[styles.banner, style]}
        accessibilityLiveRegion="polite"
      >
        <Pressable
          accessibilityRole="button"
          accessibilityLabel={`${text.title}. ${text.body}`}
          onPress={() => {
            setNotice(null);
            onOpen();
          }}
          style={styles.row}
        >
          <View style={styles.well}>
            <AppIcon name={ICON[notice.kind]} size={30} />
          </View>
          <View style={styles.flex}>
            <AppText variant="bodyStrong" numberOfLines={2}>
              {text.title}
            </AppText>
            <AppText variant="caption" color={Colors.inkMuted} numberOfLines={2}>
              {text.body}
            </AppText>
          </View>
        </Pressable>
      </Animated.View>
    </View>
  );
}

const styles = StyleSheet.create({
  flex: { flex: 1, minWidth: 0 },
  anchor: { position: 'absolute', left: 12, right: 12, alignItems: 'center', zIndex: 50 },
  banner: {
    width: '100%',
    maxWidth: 520,
    borderRadius: 20,
    borderWidth: 1,
    borderColor: Colors.bandLine,
    backgroundColor: Colors.surface,
    shadowColor: Colors.ink,
    shadowOpacity: 0.12,
    shadowRadius: 16,
    shadowOffset: { width: 0, height: 6 },
    elevation: 6,
  },
  row: { flexDirection: 'row', alignItems: 'center', gap: 12, padding: 12 },
  well: { width: 48, height: 48, borderRadius: 14, alignItems: 'center', justifyContent: 'center', backgroundColor: Colors.cherryMist },
});
