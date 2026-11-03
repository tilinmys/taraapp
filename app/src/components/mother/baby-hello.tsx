import { Asset } from 'expo-asset';
import { BlurView } from 'expo-blur';
import { useCallback, useEffect, useRef, useState, type ComponentRef, type RefObject } from 'react';
import { BackHandler, Image, Platform, Pressable, StyleSheet, useWindowDimensions, View } from 'react-native';
import Animated, {
  cancelAnimation,
  Easing,
  useAnimatedStyle,
  useReducedMotion,
  useSharedValue,
  withDelay,
  withRepeat,
  withSequence,
  withSpring,
  withTiming,
} from 'react-native-reanimated';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { scheduleOnRN } from 'react-native-worklets';

import { AppText } from '@/components/ui/app-text';
import { Brand } from '@/constants/brand';
import { useT } from '@/features/onboarding/context';
import type { MotherRecord } from '@/data/store';
import { Haptic } from '@/lib/haptics';
import { HELLO_ART } from '@/lib/preload-images';

// "Hi Amma, I'm here!" The baby welcomes the mother into her workspace.
// An overlay above the mother's tabs (not a separate screen): her home stays
// visible behind a blush blur, so the hello feels like it happens IN the app.
//
// Motion (transform + opacity only, all on the UI thread):
//   Backdrop  blur + blush scrim fade in, 280 ms ease-out. The blur strength
//             is fixed; only the wrapper's opacity animates (no per-frame
//             re-blur cost).
//   Arrive    +120 ms: the baby rises from below the bottom-right edge,
//             tilting in from the side (y +105 % -> 0, x +28 -> 0,
//             rotate -10deg -> 0) on a soft overshoot spring (170 / 15):
//             a "pop", not a slide. A blush glow swells behind her.
//   Hello     +560 ms: a wave wiggle pivoting at her base (-6, 5, -3, 0deg)
//             + light haptic; +700 ms her bubble pops from its tail with ONE
//             line: "Hi Amma, I'm here!". No buttons, no further beats.
//   Idle      a slow breath (y -5 pt, scale 1.015, 2.8 s sine) while she
//             reads it; cancelled on exit and on unmount.
//   Leave     by itself HOLD_MS after the bubble (or at once on a tap
//             anywhere / Android back): she dips back below the edge
//             (320 ms ease-in), the bubble fades, then the blur, and her
//             home is simply there underneath.
// Reduce Motion: no travel, tilt, wave or breath; short cross-fades only.
//
// Sizing: baby = 62 % of the short side on phones, 50 % on tablets (200..400
// pt); in landscape the bubble moves to the left of the baby. The art is
// one 600 px WebP (37 KB), warmed at app start (lib/preload-images.ts).

const ARRIVE = { stiffness: 170, damping: 15, mass: 0.9 };
const TACTILE = { stiffness: 300, damping: 25, mass: 0.8 };
const SINE = Easing.inOut(Easing.sin);
const GUTTER = 20;

/** How long "Hi Amma" stays up before she leaves by herself. */
const HOLD_MS = 1700;

type Props = {
  mother: MotherRecord | undefined;
  /** Android: the view behind the overlay that the blur samples. */
  blurTarget: RefObject<ComponentRef<typeof View> | null>;
  onDone: () => void;
};

export function BabyHello({ mother, blurTarget, onDone }: Props) {
  const { width, height } = useWindowDimensions();
  const insets = useSafeAreaInsets();
  const reduceMotion = useReducedMotion();
  const [ready, setReady] = useState(false);
  const t = useT();
  const leaving = useRef(false);

  // ---- responsive layout
  const short = Math.min(width, height);
  const tablet = short >= 600;
  const landscape = width > height;
  let size = Math.round(Math.min(Math.max(short * (tablet ? 0.5 : 0.62), 200), 400));
  if (landscape && !tablet) size = Math.round(Math.min(size, height * 0.62));
  const column = Math.min(width - GUTTER * 2, 520);
  const sideInset = (width - column) / 2;

  // ---- shared values
  const back = useSharedValue(0);
  const enter = useSharedValue(0);
  const wave = useSharedValue(0);
  const breath = useSharedValue(0);
  const bubble = useSharedValue(0);
  const leave = useSharedValue(0);

  // Decode the art before the first frame; never wait more than 1.2 s.
  useEffect(() => {
    let alive = true;
    const go = () => alive && setReady(true);
    Asset.loadAsync(HELLO_ART).then(go, go);
    const t = setTimeout(go, 1200);
    return () => {
      alive = false;
      clearTimeout(t);
    };
  }, []);

  useEffect(() => {
    if (!ready) return;
    if (reduceMotion) {
      const fade = { duration: 220 };
      back.set(withTiming(1, fade));
      enter.set(withTiming(1, fade));
      bubble.set(withDelay(120, withTiming(1, fade)));
      return;
    }
    back.set(withTiming(1, { duration: 280, easing: Easing.out(Easing.quad) }));
    enter.set(withDelay(120, withSpring(1, ARRIVE)));
    wave.set(
      withDelay(
        560,
        withSequence(
          withTiming(-6, { duration: 140, easing: SINE }),
          withTiming(5, { duration: 170, easing: SINE }),
          withTiming(-3, { duration: 150, easing: SINE }),
          withTiming(0, { duration: 160, easing: SINE }),
        ),
      ),
    );
    bubble.set(withDelay(700, withSpring(1, TACTILE)));
    breath.set(withDelay(1400, withRepeat(withTiming(1, { duration: 2800, easing: SINE }), -1, true)));
    const tick = setTimeout(Haptic.light, 560);
    return () => {
      clearTimeout(tick);
      cancelAnimation(breath);
    };
  }, [back, breath, bubble, enter, ready, reduceMotion, wave]);

  const close = useCallback(() => {
    if (leaving.current) return;
    leaving.current = true;
    cancelAnimation(breath);
    const d = reduceMotion ? 180 : 320;
    leave.set(withTiming(1, { duration: d, easing: Easing.in(Easing.cubic) }));
    bubble.set(withTiming(0, { duration: 160 }));
    // The overlay unmounts when the blur is gone: her home is already there.
    back.set(withDelay(reduceMotion ? 0 : 140, withTiming(0, { duration: 240 }, (done) => done && scheduleOnRN(onDone))));
  }, [back, breath, bubble, leave, onDone, reduceMotion]);

  // She says hello, then goes by herself: straight to the page.
  useEffect(() => {
    if (!ready) return;
    const timer = setTimeout(close, (reduceMotion ? 300 : 700) + HOLD_MS);
    return () => clearTimeout(timer);
  }, [close, ready, reduceMotion]);

  // Android back: leave gracefully instead of jumping away.
  useEffect(() => {
    const sub = BackHandler.addEventListener('hardwareBackPress', () => {
      close();
      return true;
    });
    return () => sub.remove();
  }, [close]);

  // ---- styles
  const backStyle = useAnimatedStyle(() => ({ opacity: back.get() }));
  const glowStyle = useAnimatedStyle(() => ({
    opacity: enter.get() * (1 - leave.get()) * 0.9,
    transform: [{ scale: 0.6 + 0.4 * Math.min(1, enter.get()) }],
  }));
  const babyStyle = useAnimatedStyle(() => {
    const e = enter.get();
    if (reduceMotion) return { opacity: e * (1 - leave.get()) };
    return {
      transform: [
        { translateX: (1 - e) * 28 },
        { translateY: (1 - e) * size * 1.05 + leave.get() * size * 1.05 - breath.get() * 5 },
        { rotate: `${(1 - e) * -10 + wave.get()}deg` },
        { scale: 1 + breath.get() * 0.015 },
      ],
    };
  });
  const bubbleStyle = useAnimatedStyle(() => {
    const b = bubble.get();
    return {
      opacity: Math.min(1, b),
      transform: [{ translateY: reduceMotion ? 0 : (1 - b) * 12 }, { scale: reduceMotion ? 1 : 0.85 + 0.15 * b }],
    };
  });

  const babyRight = Math.max(GUTTER, sideInset) - size * 0.06;

  return (
    // A tap anywhere skips the rest of the hello.
    <Pressable style={StyleSheet.absoluteFill} accessibilityViewIsModal accessibilityLabel={t('hello1T')} onPress={close}>
      {/* Backdrop: her home, softly out of focus. */}
      <Animated.View style={[StyleSheet.absoluteFill, backStyle]} pointerEvents="none">
        <BlurView
          intensity={Platform.OS === 'ios' ? 30 : 60}
          tint="light"
          blurMethod="dimezisBlurViewSdk31Plus"
          blurTarget={blurTarget}
          style={StyleSheet.absoluteFill}
        />
        <View style={[StyleSheet.absoluteFill, styles.scrim]} />
      </Animated.View>

      {/* The baby, peeking up from the bottom-right edge. */}
      <Animated.View
        pointerEvents="none"
        style={[styles.glow, { width: size * 1.2, height: size * 1.2, borderRadius: size * 0.6, right: babyRight - size * 0.1, bottom: -size * 0.35 }, glowStyle]}
      />
      <Animated.View
        pointerEvents="none"
        aria-hidden
        style={[styles.baby, { width: size, height: size, right: babyRight }, babyStyle]}
      >
        <Image source={HELLO_ART} style={styles.art} resizeMode="contain" fadeDuration={0} />
      </Animated.View>

      {/* Words and actions. */}
      <View
        style={[
          styles.content,
          {
            paddingTop: insets.top + 12,
            paddingLeft: Math.max(GUTTER, sideInset),
            // Landscape: the bubble stops just short of the baby's left edge.
            paddingRight: landscape ? babyRight + size * 0.94 + 12 : Math.max(GUTTER, sideInset),
            paddingBottom: landscape ? Math.max(insets.bottom, 16) : size * 0.92,
            justifyContent: landscape ? 'center' : 'flex-end',
          },
        ]}
        pointerEvents="box-none"
      >
        {/* Her one line: nothing else competes with her. */}
        <Animated.View style={[styles.bubble, bubbleStyle]} accessibilityLiveRegion="polite" accessible>
          <View style={[styles.pointer, landscape ? styles.pointerSide : { right: size * 0.3 }]} />
          <AppText variant="greetingSmall" color={Brand.cherry}>
            {t('hello1T')}
          </AppText>
        </Animated.View>
      </View>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  scrim: { backgroundColor: 'rgba(253, 240, 245, 0.62)' },
  glow: { position: 'absolute', backgroundColor: Brand.cherryMist },
  baby: { position: 'absolute', bottom: 0, transformOrigin: '50% 100%' },
  art: { width: '100%', height: '100%' },
  content: { flex: 1 },
  bubble: {
    alignSelf: 'flex-start',
    maxWidth: 440,
    padding: 18,
    borderRadius: 24,
    backgroundColor: Brand.surface,
    borderWidth: 1,
    borderColor: Brand.cherryMist,
    shadowColor: '#5B2A3A',
    shadowOpacity: 0.12,
    shadowRadius: 18,
    shadowOffset: { width: 0, height: 8 },
    elevation: 4,
  },
  pointer: {
    position: 'absolute',
    bottom: -8,
    width: 16,
    height: 16,
    borderRadius: 2,
    backgroundColor: Brand.surface,
    borderRightWidth: 1,
    borderBottomWidth: 1,
    borderColor: Brand.cherryMist,
    transform: [{ rotate: '45deg' }],
  },
  pointerSide: { bottom: undefined, top: '50%', right: -8, marginTop: -8, transform: [{ rotate: '-45deg' }] },
});
