import { useEffect } from 'react';
import { StyleSheet, View } from 'react-native';
import Animated, {
  cancelAnimation,
  Easing,
  useAnimatedStyle,
  useReducedMotion,
  useSharedValue,
  withRepeat,
  withSpring,
  withTiming,
} from 'react-native-reanimated';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { Icon } from '@/components/system/icon';
import { AppText } from '@/components/ui/app-text';
import { Colors, Physics } from '@/constants/theme';

// Dynamic-Island-style processing pill. Sits just under the top safe area
// (the notch / island), expands from a dot into a dark pill and shows the
// current step of a background task, so the user watches progress without
// being blocked. Collapses back when `visible` turns false.

type Props = { visible: boolean; label: string; done?: boolean };

export function StatusPill({ visible, label, done }: Props) {
  const insets = useSafeAreaInsets();
  const reduceMotion = useReducedMotion();
  const open = useSharedValue(0);
  const spin = useSharedValue(0);

  useEffect(() => {
    open.set(reduceMotion ? withTiming(visible ? 1 : 0, { duration: 150 }) : withSpring(visible ? 1 : 0, Physics.page));
  }, [open, reduceMotion, visible]);
  useEffect(() => {
    cancelAnimation(spin);
    if (!done && visible && !reduceMotion) {
      spin.set(0);
      spin.set(withRepeat(withTiming(1, { duration: 900, easing: Easing.linear }), -1));
    }
  }, [done, reduceMotion, spin, visible]);

  const pillStyle = useAnimatedStyle(() => ({
    opacity: Math.min(1, open.get() * 2),
    transform: [{ scaleX: 0.3 + 0.7 * open.get() }, { scaleY: 0.6 + 0.4 * open.get() }],
  }));
  const spinStyle = useAnimatedStyle(() => ({ transform: [{ rotate: `${spin.get() * 360}deg` }] }));

  return (
    <View pointerEvents="none" style={[styles.anchor, { top: insets.top + 6 }]}>
      <Animated.View style={[styles.pill, pillStyle]} accessibilityLiveRegion="polite" accessibilityLabel={label}>
        {done ? (
          <Icon name="checkCircle" size={18} color={Colors.onCherry} active />
        ) : (
          <Animated.View style={[styles.spinner, spinStyle]} />
        )}
        <AppText variant="caption" color={Colors.onCherry} numberOfLines={1}>
          {label}
        </AppText>
      </Animated.View>
    </View>
  );
}

const styles = StyleSheet.create({
  anchor: { position: 'absolute', left: 0, right: 0, alignItems: 'center', zIndex: 20 },
  pill: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 10,
    minHeight: 36,
    maxWidth: 320,
    paddingHorizontal: 16,
    borderRadius: 18,
    backgroundColor: Colors.ink,
  },
  spinner: {
    width: 16,
    height: 16,
    borderRadius: 8,
    borderWidth: 2,
    borderColor: 'rgba(255,255,255,0.3)',
    borderTopColor: Colors.onCherry,
  },
});
