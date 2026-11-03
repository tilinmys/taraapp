import { useEffect, useRef } from 'react';
import { StyleSheet } from 'react-native';
import Animated, {
  interpolateColor,
  useAnimatedStyle,
  useReducedMotion,
  useSharedValue,
  withSequence,
  withSpring,
  withTiming,
} from 'react-native-reanimated';

import { Icon } from '@/components/system/icon';
import { Brand } from '@/constants/brand';

// Checkbox with a tactile bounce: on tick it squashes to 0.85, overshoots to
// 1.1 and settles at 1 while filling with pumpkin. Unticking just fades.
export function SpringCheck({ checked, size = 26 }: { checked: boolean; size?: number }) {
  const reduceMotion = useReducedMotion();
  const scale = useSharedValue(1);
  const fill = useSharedValue(checked ? 1 : 0);
  const first = useRef(true);
  useEffect(() => {
    fill.set(withTiming(checked ? 1 : 0, { duration: reduceMotion ? 0 : 160 }));
    if (first.current) {
      first.current = false;
      return;
    }
    if (checked && !reduceMotion) {
      scale.set(
        withSequence(
          withTiming(0.85, { duration: 70 }),
          withSpring(1.1, { stiffness: 400, damping: 10 }),
          withSpring(1, { stiffness: 300, damping: 20 }),
        ),
      );
    }
  }, [checked, fill, reduceMotion, scale]);
  const style = useAnimatedStyle(() => ({
    transform: [{ scale: scale.get() }],
    backgroundColor: interpolateColor(fill.get(), [0, 1], [Brand.surface, Brand.pumpkin]),
    borderColor: interpolateColor(fill.get(), [0, 1], [Brand.outline, Brand.pumpkin]),
  }));
  return (
    <Animated.View style={[styles.box, { width: size, height: size, borderRadius: size * 0.3 }, style]} aria-hidden>
      {checked && <Icon name="check" size={size * 0.55} color={Brand.onCherry} weight="bold" />}
    </Animated.View>
  );
}

const styles = StyleSheet.create({
  box: { borderWidth: 2, alignItems: 'center', justifyContent: 'center' },
});
