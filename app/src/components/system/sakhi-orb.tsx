import { useEffect } from 'react';
import { StyleSheet, View } from 'react-native';
import Animated, {
  cancelAnimation,
  Easing,
  useAnimatedStyle,
  useReducedMotion,
  useSharedValue,
  withDelay,
  withRepeat,
  withSequence,
  withTiming,
  type SharedValue,
} from 'react-native-reanimated';

import { Icon } from '@/components/system/icon';
import { Brand } from '@/constants/brand';

// Sakhi's presence. Not a generic "AI blob": a rangoli. A cherry core with a
// pumpkin centre, a ring of 12 kolam dots, and three concentric rings.
//   idle       the core breathes; dots and rings rest
//   speaking   the dot ring turns slowly (Sakhi is talking); rings swell
//              together on a speech-like rhythm
//   listening  a wave travels round the dots and the three rings ripple
//              outward (scale 1.0 -> 1.35, opacity 0.8 -> 0.2), so the ASHA
//              can see from across the room that Sakhi is listening
//   thinking   the dots turn quickly while the answer is saved
// Reduced motion: still, and the state is carried by the icon + caption.

export type OrbState = 'idle' | 'speaking' | 'listening' | 'thinking';

const DOTS = 12;
const RING_COLORS = [Brand.pumpkin, Brand.cherry, Brand.pumpkin];

function Ring({ v, color, size }: { v: SharedValue<number>; color: string; size: number }) {
  const style = useAnimatedStyle(() => ({
    opacity: 0.8 - 0.6 * v.get(),
    transform: [{ scale: 1 + 0.35 * v.get() }],
  }));
  return (
    <Animated.View
      style={[styles.ring, { width: size, height: size, borderRadius: size / 2, borderColor: color }, style]}
    />
  );
}

function Dot({ i, wave, amp, radius, dot }: { i: number; wave: SharedValue<number>; amp: SharedValue<number>; radius: number; dot: number }) {
  const angle = (i / DOTS) * Math.PI * 2;
  const style = useAnimatedStyle(() => {
    // A crest that travels round the ring: each dot lifts as it passes.
    const d = (((wave.get() - i / DOTS) % 1) + 1) % 1;
    const lift = amp.get() * Math.max(0, Math.cos(d * Math.PI * 2)) ** 3;
    return { transform: [{ scale: 1 + 0.9 * lift }], opacity: 0.45 + 0.55 * lift };
  });
  return (
    <Animated.View
      style={[
        styles.dot,
        {
          width: dot,
          height: dot,
          borderRadius: dot / 2,
          backgroundColor: i % 3 === 0 ? Brand.cherry : Brand.pumpkin,
          left: radius + Math.cos(angle) * radius - dot / 2,
          top: radius + Math.sin(angle) * radius - dot / 2,
        },
        style,
      ]}
    />
  );
}

export function SakhiOrb({ state, size = 168 }: { state: OrbState; size?: number }) {
  const reduceMotion = useReducedMotion();
  const r1 = useSharedValue(0);
  const r2 = useSharedValue(0);
  const r3 = useSharedValue(0);
  const spin = useSharedValue(0);
  const wave = useSharedValue(0);
  const core = useSharedValue(0);
  const amp = useSharedValue(0); // wave height: only while listening / thinking

  useEffect(() => {
    const all = [r1, r2, r3, spin, wave, core, amp];
    all.forEach((v) => cancelAnimation(v));
    if (reduceMotion) {
      all.forEach((v) => v.set(0));
      return;
    }
    const sine = Easing.inOut(Easing.sin);
    const loop = (ms: number) => withRepeat(withTiming(1, { duration: ms, easing: Easing.linear }), -1, false);

    if (state === 'listening') {
      [r1, r2, r3].forEach((v, i) => {
        v.set(0);
        v.set(withDelay(i * 380, withRepeat(withTiming(1, { duration: 1400, easing: Easing.out(Easing.quad) }), -1, false)));
      });
      amp.set(withTiming(1, { duration: 200 }));
      wave.set(0);
      wave.set(loop(1200));
      spin.set(withTiming(0, { duration: 300 }));
      core.set(withRepeat(withSequence(withTiming(1, { duration: 500, easing: sine }), withTiming(0.4, { duration: 500, easing: sine })), -1));
    } else if (state === 'speaking') {
      // Syllable rhythm: uneven swells, all rings together.
      const swell = withRepeat(
        withSequence(
          withTiming(0.28, { duration: 220, easing: sine }),
          withTiming(0.08, { duration: 180, easing: sine }),
          withTiming(0.2, { duration: 260, easing: sine }),
          withTiming(0, { duration: 340, easing: sine }),
        ),
        -1,
      );
      [r1, r2, r3].forEach((v) => v.set(swell));
      amp.set(withTiming(0, { duration: 200 }));
      wave.set(0);
      spin.set(0);
      spin.set(loop(9000));
      core.set(withRepeat(withSequence(withTiming(0.6, { duration: 400, easing: sine }), withTiming(0.2, { duration: 400, easing: sine })), -1));
    } else if (state === 'thinking') {
      [r1, r2, r3].forEach((v) => v.set(withTiming(0, { duration: 200 })));
      spin.set(0);
      spin.set(loop(1600));
      amp.set(withTiming(0.6, { duration: 200 }));
      wave.set(0);
      wave.set(loop(800));
    } else {
      [r1, r2, r3].forEach((v) => v.set(withTiming(0, { duration: 300 })));
      amp.set(withTiming(0, { duration: 300 }));
      spin.set(withTiming(0, { duration: 300 }));
      core.set(withRepeat(withSequence(withTiming(1, { duration: 1600, easing: sine }), withTiming(0, { duration: 1600, easing: sine })), -1));
    }
  }, [amp, core, r1, r2, r3, reduceMotion, spin, state, wave]);

  const coreSize = size * 0.42;
  const dotRing = size * 0.62;
  const dot = Math.max(5, size * 0.04);
  const ringStyle = useAnimatedStyle(() => ({ transform: [{ rotate: `${spin.get() * 360}deg` }] }));
  const coreStyle = useAnimatedStyle(() => ({ transform: [{ scale: 1 + 0.06 * core.get() }] }));
  const icon = state === 'listening' ? 'mic' : state === 'speaking' ? 'speaker' : state === 'thinking' ? 'wave' : 'chat';

  return (
    <View style={{ width: size * 1.35, height: size * 1.35, alignItems: 'center', justifyContent: 'center' }} aria-hidden>
      {[r1, r2, r3].map((v, i) => (
        <Ring key={i} v={v} color={RING_COLORS[i]} size={size * (0.74 + i * 0.13)} />
      ))}
      <Animated.View style={[{ position: 'absolute', width: dotRing, height: dotRing }, ringStyle]}>
        {Array.from({ length: DOTS }, (_, i) => (
          <Dot key={i} i={i} wave={wave} amp={amp} radius={dotRing / 2} dot={dot} />
        ))}
      </Animated.View>
      <Animated.View
        style={[styles.core, { width: coreSize, height: coreSize, borderRadius: coreSize / 2 }, coreStyle]}
      >
        <View
          style={[
            styles.sun,
            { width: coreSize * 0.7, height: coreSize * 0.7, borderRadius: coreSize * 0.35 },
          ]}
        />
        <Icon name={icon} size={coreSize * 0.4} color={Brand.onCherry} weight="fill" />
      </Animated.View>
    </View>
  );
}

const styles = StyleSheet.create({
  ring: { position: 'absolute', borderWidth: 1.5 },
  dot: { position: 'absolute' },
  core: { alignItems: 'center', justifyContent: 'center', backgroundColor: Brand.cherry, overflow: 'hidden' },
  // Concentric pumpkin disc inside the cherry core: a bindi / kolam centre.
  sun: { position: 'absolute', backgroundColor: Brand.pumpkin, opacity: 0.9 },
});
