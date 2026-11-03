import { useIsFocused } from 'expo-router';
import { useEffect, useRef, type ReactNode } from 'react';
import type { StyleProp, ViewStyle } from 'react-native';
import Animated, {
  cancelAnimation,
  useAnimatedStyle,
  useReducedMotion,
  useSharedValue,
  Easing,
  withDelay,
  withTiming,
} from 'react-native-reanimated';

import { Nav } from '@/constants/motion';

// Staggered entrance for blocks inside tab screens: translateY 10 -> 0,
// opacity 0 -> 1, 200 ms ease-out, 30 ms apart, at most 4 steps (Nav in
// constants/motion.ts). Short and close on purpose: the page is ready to
// read almost at once and nothing drifts while she starts reading.
//
// The blank-screen bug: a hidden tab stops receiving animated style writes.
// If she switched tabs mid-entrance, the value finished in the background
// but its last frame never painted, so on return the block sat at opacity 0
// and nothing ever repainted it. So:
//   blur   -> cancel and snap to the final value
//   focus  -> first time: the full staggered entrance; afterwards a tiny
//             0.92 -> 1 settle, which forces a fresh paint while visible
// The spring callback also snaps to 1 if it is ever interrupted.

export const SPRING_TACTILE = { stiffness: 300, damping: 25, mass: 0.8 };

export function Rise({ index = 0, style, children }: { index?: number; style?: StyleProp<ViewStyle>; children: ReactNode }) {
  const reduceMotion = useReducedMotion();
  const focused = useIsFocused();
  const p = useSharedValue(0);
  const firstRun = useRef(true);

  useEffect(() => {
    if (!focused) {
      cancelAnimation(p);
      p.set(1);
      return;
    }
    if (reduceMotion) {
      p.set(0.999);
      p.set(withTiming(1, { duration: 16 }));
      return;
    }
    const first = firstRun.current;
    firstRun.current = false;
    p.set(first ? 0 : 0.92);
    p.set(
      withDelay(
        first ? Math.min(index, Nav.maxStagger) * Nav.stagger : 0,
        withTiming(1, { duration: Nav.rise, easing: Easing.out(Easing.cubic) }, (finished) => {
          if (!finished) p.set(1);
        }),
      ),
    );
  }, [focused, index, p, reduceMotion]);

  const a = useAnimatedStyle(() => ({
    opacity: Math.min(1, p.get()),
    transform: [{ translateY: (1 - p.get()) * Nav.riseTravel }],
  }));
  return <Animated.View style={[style, a]}>{children}</Animated.View>;
}
