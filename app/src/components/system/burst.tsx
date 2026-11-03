import { useEffect } from 'react';
import { StyleSheet, View } from 'react-native';
import Animated, { Easing, useAnimatedStyle, useReducedMotion, useSharedValue, withTiming, type SharedValue } from 'react-native-reanimated';

import { Brand } from '@/constants/brand';

// A one-shot micro-celebration (motion spec #9's particle payoff): 10 soft
// dots fly out from the centre, grow, then fade. ONE shared value drives
// every particle, transform + opacity only, ~700 ms, then nothing runs.
// Mount it with a new `key` each time you want it to play.

const COLORS = [Brand.cherry, Brand.pumpkin, Brand.blush, Brand.sage, Brand.plum];
const COUNT = 10;

function Particle({ i, p }: { i: number; p: SharedValue<number> }) {
  const angle = (i / COUNT) * Math.PI * 2 + (i % 2 ? 0.25 : 0);
  const dist = 46 + (i % 3) * 14;
  const size = 6 + (i % 3) * 2;
  const a = useAnimatedStyle(() => {
    const t = p.get();
    return {
      opacity: t < 0.6 ? 1 : 1 - (t - 0.6) / 0.4,
      transform: [{ translateX: Math.cos(angle) * dist * t }, { translateY: Math.sin(angle) * dist * t }, { scale: t < 0.3 ? t / 0.3 : 1 - 0.5 * t }],
    };
  });
  return <Animated.View style={[styles.dot, { width: size, height: size, borderRadius: size / 2, backgroundColor: COLORS[i % COLORS.length] }, a]} />;
}

export function Burst() {
  const reduceMotion = useReducedMotion();
  const p = useSharedValue(0);
  useEffect(() => {
    if (!reduceMotion) p.set(withTiming(1, { duration: 720, easing: Easing.out(Easing.cubic) }));
  }, [p, reduceMotion]);
  if (reduceMotion) return null;
  return (
    <View pointerEvents="none" style={styles.wrap} aria-hidden>
      {Array.from({ length: COUNT }, (_, i) => (
        <Particle key={i} i={i} p={p} />
      ))}
    </View>
  );
}

const styles = StyleSheet.create({
  wrap: { position: 'absolute', top: 0, right: 0, bottom: 0, left: 0, alignItems: 'center', justifyContent: 'center' },
  dot: { position: 'absolute' },
});
