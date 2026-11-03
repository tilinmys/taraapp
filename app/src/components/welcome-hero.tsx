import { useEffect } from 'react';
import { StyleSheet, View } from 'react-native';
import Animated, {
  useAnimatedStyle,
  useReducedMotion,
  useSharedValue,
  withRepeat,
  withSequence,
  withTiming,
} from 'react-native-reanimated';
import Svg, { Defs, RadialGradient, Rect, Stop } from 'react-native-svg';

import { Brand, LOGO_ASPECT } from '@/constants/brand';
import { Curves, Welcome } from '@/constants/motion';

// Hero: the tara logo floating over a soft gradient orb.
// Idle motion: the logo drifts up 6 pt and back over 4 s (ease-in-out,
// forever); the orb breathes slowly out of phase, so the pair never moves in
// lockstep. Reduced motion: both stay still.

const LOGO = require('@/assets/images/tara-logo.webp');

export function WelcomeHero({ logoWidth }: { logoWidth: number }) {
  const reduceMotion = useReducedMotion();
  const float = useSharedValue(0); // 0 = rest, 1 = lifted
  const breathe = useSharedValue(0);
  const orbSize = Math.round(logoWidth * 1.55);
  const logoHeight = Math.round(logoWidth / LOGO_ASPECT);

  useEffect(() => {
    if (reduceMotion) return;
    float.set(
      withRepeat(
        withSequence(
          withTiming(1, { duration: Welcome.floatHalfMs, easing: Curves.breathe }),
          withTiming(0, { duration: Welcome.floatHalfMs, easing: Curves.breathe }),
        ),
        -1,
      ),
    );
    breathe.set(
      withRepeat(
        withSequence(
          withTiming(1, { duration: Welcome.orbHalfMs, easing: Curves.breathe }),
          withTiming(0, { duration: Welcome.orbHalfMs, easing: Curves.breathe }),
        ),
        -1,
      ),
    );
  }, [breathe, float, reduceMotion]);

  const logoStyle = useAnimatedStyle(() => ({
    transform: [{ translateY: -Welcome.floatRise * float.get() }],
  }));
  const orbStyle = useAnimatedStyle(() => ({
    opacity: 0.85 + 0.15 * breathe.get(),
    transform: [{ scale: 1 + (Welcome.orbScale - 1) * breathe.get() }],
  }));

  return (
    <View style={{ width: orbSize, height: Math.max(orbSize * 0.78, logoHeight + 40) }}>
      <Animated.View
        style={[styles.orb, { width: orbSize, height: orbSize, top: -orbSize * 0.11 }, orbStyle]}
        pointerEvents="none"
      >
        <Svg width="100%" height="100%" viewBox="0 0 100 100">
          <Defs>
            {/* Warm blush core, eased falloff (many stops) so the orb melts into the canvas with
                no visible rim; it reaches zero well inside its own box. */}
            <RadialGradient id="orb" cx="50" cy="50" r="50" fx="56" fy="42" gradientUnits="userSpaceOnUse">
              <Stop offset="0" stopColor={Brand.blush} stopOpacity={0.62} />
              <Stop offset="0.25" stopColor={Brand.blush} stopOpacity={0.42} />
              <Stop offset="0.5" stopColor={Brand.cherryMist} stopOpacity={0.3} />
              <Stop offset="0.72" stopColor={Brand.cherryMist} stopOpacity={0.12} />
              <Stop offset="0.9" stopColor={Brand.cherryMist} stopOpacity={0.03} />
              <Stop offset="1" stopColor={Brand.cherryMist} stopOpacity={0} />
            </RadialGradient>
          </Defs>
          <Rect x="0" y="0" width="100" height="100" fill="url(#orb)" />
        </Svg>
      </Animated.View>
      <Animated.Image
        source={LOGO}
        accessibilityLabel="tara"
        resizeMode="contain"
        fadeDuration={0}
        style={[
          styles.logo,
          { width: logoWidth, height: logoHeight, left: (orbSize - logoWidth) / 2, top: orbSize * 0.39 - logoHeight / 2 },
          logoStyle,
        ]}
      />
    </View>
  );
}

const styles = StyleSheet.create({
  orb: {
    position: 'absolute',
    left: 0,
  },
  logo: {
    position: 'absolute',
  },
});
