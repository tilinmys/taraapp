import { router } from 'expo-router';
import * as SplashScreen from 'expo-splash-screen';
import { useCallback, useEffect } from 'react';
import { Platform, StyleSheet, useWindowDimensions, View } from 'react-native';
import Animated, {
  Easing,
  useAnimatedStyle,
  useReducedMotion,
  useSharedValue,
  withDelay,
  withSequence,
  withTiming,
} from 'react-native-reanimated';
import { scheduleOnRN } from 'react-native-worklets';

import { Brand, LOGO_ASPECT, LOGO_ZOOM_ORIGIN, SPLASH_LOGO_WIDTH, TAGLINE } from '@/constants/brand';

// Cinematic zoom-through splash.
//
//   0 -  800 ms  hold. The logo is already on screen (native splash, same
//                size and position), the tagline rises in beneath it and the
//                mark "breathes" once (1.00 -> 1.03 -> 1.00).
// 800 - 1300 ms  zoom through the stem of the "r" (scale 35, inOut cubic):
//                the Alabaster letterform floods the screen, the canvas
//                settles on Alabaster and the app replaces this route.
//
// Everything animates on the UI thread (Reanimated shared values); the only
// JS-thread hop is the final navigation. Reduced-motion users get a plain
// cross-fade instead of the zoom.

const HOLD_MS = 800;
const ZOOM_MS = 500;
const ZOOM_SCALE = 35;
const zoomEase = Easing.inOut(Easing.cubic);
const softOut = Easing.out(Easing.cubic);

// On native the logo is already visible in the OS splash, so it must not fade
// in again (that reads as a flicker). The web has no native splash.
const LOGO_STARTS_VISIBLE = Platform.OS !== 'web';

const LOGO_HEIGHT = Math.round(SPLASH_LOGO_WIDTH / LOGO_ASPECT);
const TAGLINE_GAP = 28;

export default function Splash() {
  const { width, height } = useWindowDimensions();
  const reduceMotion = useReducedMotion();
  const taglineSize = Math.round(Math.min(Math.max(Math.min(width, height) * 0.04, 15), 18));

  const intro = useSharedValue(LOGO_STARTS_VISIBLE ? 1 : 0);
  const scale = useSharedValue(1);
  const tagline = useSharedValue(0);
  const flood = useSharedValue(0); // Alabaster canvas over everything

  const goNext = useCallback(() => {
    router.replace('/welcome');
  }, []);

  useEffect(() => {
    // The JS splash has mounted with the logo in the identical spot: hand over.
    SplashScreen.hideAsync().catch(() => {});

    if (!LOGO_STARTS_VISIBLE) {
      intro.value = withTiming(1, { duration: 300, easing: softOut });
    }
    tagline.value = withSequence(
      withDelay(120, withTiming(1, { duration: 420, easing: softOut })),
      withDelay(HOLD_MS - 540, withTiming(0, { duration: 180, easing: Easing.in(Easing.quad) })),
    );

    if (reduceMotion) {
      flood.value = withDelay(
        HOLD_MS,
        withTiming(1, { duration: 300 }, (finished) => {
          if (finished) scheduleOnRN(goNext);
        }),
      );
      return;
    }

    scale.value = withSequence(
      withDelay(200, withTiming(1.03, { duration: 300, easing: Easing.inOut(Easing.sin) })),
      withTiming(1, { duration: 300, easing: Easing.inOut(Easing.sin) }),
      withTiming(ZOOM_SCALE, { duration: ZOOM_MS, easing: zoomEase }),
    );
    // The letterform already fills the screen by ~85% of the zoom; the canvas
    // fade only guarantees a clean Alabaster frame before navigating.
    flood.value = withDelay(
      HOLD_MS + ZOOM_MS * 0.7,
      withTiming(1, { duration: ZOOM_MS * 0.3, easing: Easing.linear }, (finished) => {
        if (finished) scheduleOnRN(goNext);
      }),
    );
  }, [flood, goNext, intro, reduceMotion, scale, tagline]);

  const logoStyle = useAnimatedStyle(() => ({
    opacity: intro.value,
    transform: [{ scale: scale.value }],
  }));
  const taglineStyle = useAnimatedStyle(() => ({
    opacity: tagline.value,
    transform: [{ translateY: (1 - tagline.value) * 6 }],
  }));
  const floodStyle = useAnimatedStyle(() => ({ opacity: flood.value }));

  return (
    <View style={styles.canvas} accessible accessibilityLabel={`tara. ${TAGLINE}`}>
      {/* Logo sits at the exact optical centre, like the native splash. */}
      <Animated.Image
        source={require('@/assets/images/tara-logo-light.png')}
        style={[
          {
            width: SPLASH_LOGO_WIDTH,
            height: LOGO_HEIGHT,
            // Array form ([x, y]): the pivot is parsed the same on Android
            // (Fabric) and iOS; the zoom goes through the stem of the "r".
            transformOrigin: [`${(LOGO_ZOOM_ORIGIN.x * 100).toFixed(1)}%`, `${(LOGO_ZOOM_ORIGIN.y * 100).toFixed(1)}%`],
          },
          logoStyle,
        ]}
        resizeMode="contain"
        accessibilityIgnoresInvertColors
      />
      {/* Tagline hangs below the centred logo without pushing it off centre. */}
      <Animated.Text
        style={[
          styles.tagline,
          { fontSize: taglineSize, top: height / 2 + LOGO_HEIGHT / 2 + TAGLINE_GAP },
          taglineStyle,
        ]}
        numberOfLines={2}
        maxFontSizeMultiplier={1.25}
      >
        {TAGLINE}
      </Animated.Text>
      <Animated.View pointerEvents="none" style={[StyleSheet.absoluteFill, styles.flood, floodStyle]} />
    </View>
  );
}

const styles = StyleSheet.create({
  canvas: {
    flex: 1,
    backgroundColor: Brand.botanicalMidnight,
    alignItems: 'center',
    justifyContent: 'center',
    overflow: 'hidden',
  },
  tagline: {
    position: 'absolute',
    left: 32,
    right: 32,
    color: Brand.cadetSlate,
    textAlign: 'center',
    letterSpacing: 0.6,
    lineHeight: 24,
    fontWeight: '400',
  },
  flood: {
    backgroundColor: Brand.alabaster,
  },
});
