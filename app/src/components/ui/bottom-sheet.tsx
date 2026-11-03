import { useEffect, type ReactNode } from 'react';
import { BackHandler, Pressable, ScrollView, StyleSheet, useWindowDimensions, View } from 'react-native';
import Animated, {
  useAnimatedStyle,
  useReducedMotion,
  withSpring,
  withTiming,
  type SharedValue,
} from 'react-native-reanimated';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { Icon as Glyph } from '@/components/system/icon';
import { AppText } from '@/components/ui/app-text';
import { Colors, Layout, OnboardingSprings } from '@/constants/theme';

// Contextual bottom sheet. The caller owns `progress` (0 closed, 1 open) so
// it can also scale its own background to 0.96 while the sheet is up. Always
// mounted; when closed it rests below the screen and ignores touches and
// screen readers. No blur: a solid ink scrim keeps text contrast intact.
//   footer       pinned under the scrolling body: the sheet's primary action
//                stays visible however long the content is
//   dismissible  false: no x, and the scrim / Android back do nothing; the
//                sheet closes only through its own buttons (a required step)

type Props = {
  open: boolean;
  onClose: () => void;
  progress: SharedValue<number>;
  title: string;
  closeLabel: string;
  children: ReactNode;
  footer?: ReactNode;
  dismissible?: boolean;
};

export function BottomSheet({ open, onClose, progress, title, closeLabel, children, footer, dismissible = true }: Props) {
  const reduceMotion = useReducedMotion();
  const insets = useSafeAreaInsets();
  const { height } = useWindowDimensions();

  useEffect(() => {
    progress.set(
      open
        ? reduceMotion
          ? withTiming(1, { duration: 150 })
          : withSpring(1, OnboardingSprings.sheet)
        : withTiming(0, { duration: 220 }),
    );
  }, [open, progress, reduceMotion]);

  useEffect(() => {
    if (!open) return;
    const sub = BackHandler.addEventListener('hardwareBackPress', () => {
      if (dismissible) onClose();
      return true;
    });
    return () => sub.remove();
  }, [dismissible, onClose, open]);

  const scrim = useAnimatedStyle(() => ({ opacity: Math.min(1, progress.get()) * 0.36 }));
  const sheet = useAnimatedStyle(() => ({
    transform: [{ translateY: reduceMotion ? 0 : (1 - progress.get()) * height }],
    opacity: reduceMotion ? progress.get() : 1,
  }));

  return (
    <View
      // Clipped, so the parked (off-screen) sheet never adds scrollable area.
      style={[StyleSheet.absoluteFill, styles.host]}
      pointerEvents={open ? 'auto' : 'none'}
      accessibilityViewIsModal={open}
      aria-hidden={!open}
    >
      <Animated.View style={[StyleSheet.absoluteFill, styles.scrim, scrim]}>
        {dismissible && <Pressable style={StyleSheet.absoluteFill} accessibilityRole="button" accessibilityLabel={closeLabel} onPress={onClose} />}
      </Animated.View>
      <Animated.View style={[styles.sheet, { maxHeight: height * 0.82, paddingBottom: Math.max(insets.bottom, 16) + 8 }, sheet]}>
        <View style={styles.grabber} />
        <View style={styles.head}>
          <AppText variant="heading" accessibilityRole="header" style={styles.title}>
            {title}
          </AppText>
          {dismissible && (
            <Pressable
              accessibilityRole="button"
              accessibilityLabel={closeLabel}
              onPress={onClose}
              hitSlop={6}
              style={({ pressed }) => [styles.close, pressed && styles.pressed]}
            >
              <Glyph name="close" size={22} color={Colors.ink} weight="bold" />
            </Pressable>
          )}
        </View>
        <ScrollView contentContainerStyle={styles.body} showsVerticalScrollIndicator={false} keyboardShouldPersistTaps="handled">
          {children}
        </ScrollView>
        {!!footer && <View style={styles.footer}>{footer}</View>}
      </Animated.View>
    </View>
  );
}

const styles = StyleSheet.create({
  host: {
    overflow: 'hidden',
  },
  scrim: {
    backgroundColor: Colors.ink,
  },
  sheet: {
    position: 'absolute',
    left: 0,
    right: 0,
    bottom: 0,
    alignSelf: 'center',
    width: '100%',
    maxWidth: 560,
    paddingTop: 10,
    borderTopLeftRadius: 24,
    borderTopRightRadius: 24,
    backgroundColor: Colors.porcelain,
  },
  grabber: {
    alignSelf: 'center',
    width: 40,
    height: 5,
    borderRadius: 3,
    backgroundColor: Colors.outline,
    opacity: 0.6,
  },
  head: {
    minHeight: 52, // the same with or without the x
    flexDirection: 'row',
    alignItems: 'center',
    gap: 12,
    paddingHorizontal: Layout.gutter,
    paddingTop: 8,
    paddingBottom: 4,
  },
  title: {
    flex: 1,
  },
  close: {
    width: 44,
    height: 44,
    borderRadius: 22,
    alignItems: 'center',
    justifyContent: 'center',
  },
  pressed: {
    opacity: 0.7,
  },
  body: {
    paddingHorizontal: Layout.gutter,
    paddingTop: 8,
    paddingBottom: 8,
    gap: 16,
  },
  footer: {
    gap: 8,
    paddingHorizontal: Layout.gutter,
    paddingTop: 12,
    borderTopWidth: 1,
    borderTopColor: Colors.hairline,
  },
});
