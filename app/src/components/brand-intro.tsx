import * as SplashScreen from 'expo-splash-screen';
import { useCallback, useEffect, useRef } from 'react';
import { StyleSheet, useWindowDimensions, View } from 'react-native';
import Animated, {
  cancelAnimation,
  interpolate,
  useAnimatedStyle,
  useReducedMotion,
  useSharedValue,
  withDelay,
  withRepeat,
  withSequence,
  withSpring,
  withTiming,
} from 'react-native-reanimated';
import { scheduleOnRN } from 'react-native-worklets';

import { Brand, LEAF_BOX, LEAF_MARK_WIDTH, LOGO_ASPECT, LOGO_SOURCE_WIDTH, SPLASH_LOGO_WIDTH } from '@/constants/brand';
import { Curves, Intro, Springs } from '@/constants/motion';

// tara brand intro: phases 1-3 of the launch sequence. Full-screen overlay
// that sits on top of the welcome screen and removes itself when done.
//
//  Phase 1  Pre-loader. The blush leaf, the logo's seed, sits alone at the
//           centre (identical to the native splash) and breathes while the
//           logo layers decode. Shown for at least one breath.
//  Phase 2  Kinetic build. The leaf pops (spring), then springs into its
//           place in the logo. The four letters t-a-r-a rise and grow into
//           place one after another on soft springs, and the woman's figure
//           draws on upward from her shoulders, as if growing from the leaf.
//  Hold     The complete logo rests, perfectly still, until t = 2 s.
//  Phase 3  Bloom. A cherry-mist circle grows out of the leaf until it
//           covers the screen (a clean mask, always vector-sharp), the
//           welcome screen is arranged behind it, and the mask dissolves.
//
// The caller runs phase 4 (interface entrance) when `onCovered` fires.
// Everything animates on the UI thread; JS only hears the phase changes.

const LOGO_W = SPLASH_LOGO_WIDTH;
const LOGO_H = LOGO_W / LOGO_ASPECT;
const K = LOGO_W / LOGO_SOURCE_WIDTH; // logo px -> pt

// Leaf as drawn inside the logo (pt, relative to the logo box).
const LEAF_IN_LOGO = {
  x: LEAF_BOX.x * K,
  y: LEAF_BOX.y * K,
  width: LEAF_BOX.width * K,
  height: LEAF_BOX.height * K,
};
// Leaf as the stand-alone mark (pre-loader / native splash).
const MARK_W = LEAF_MARK_WIDTH;
// The figure (hair, face, shoulders) ends at y = 372 px in the logo; the
// draw-on grows upward from there.
const FIGURE_H = 372 * K;
const MARK_SCALE = MARK_W / LEAF_IN_LOGO.width;

// Each letter grows from its own foot on the baseline (x = letter centre,
// y = baseline), measured from the layer bounds in logo px. Given as numeric
// [x, y, z] points: iOS rejects long-decimal percentage strings.
const BASELINE_PX = 542;
const LETTER_ORIGINS = [99, 322, 534, 750].map(
  (cx) => [Math.round(cx * (SPLASH_LOGO_WIDTH / LOGO_SOURCE_WIDTH) * 100) / 100, Math.round(BASELINE_PX * (SPLASH_LOGO_WIDTH / LOGO_SOURCE_WIDTH) * 100) / 100, 0] as [number, number, number],
);

function letterPose(progress: number, opacity: number, index: number) {
  'worklet';
  return {
    transformOrigin: LETTER_ORIGINS[index],
    opacity,
    transform: [
      { translateY: (1 - progress) * Intro.letterRise },
      { scale: Intro.letterFrom + (1 - Intro.letterFrom) * progress },
    ],
  };
}

const LAYERS = {
  t: require('@/assets/images/logo-layers/tara-logo-t.webp'),
  a1: require('@/assets/images/logo-layers/tara-logo-a1.webp'),
  r: require('@/assets/images/logo-layers/tara-logo-r.webp'),
  a2: require('@/assets/images/logo-layers/tara-logo-a2.webp'),
  figure: require('@/assets/images/logo-layers/tara-logo-figure.webp'),
};
const LEAF = require('@/assets/images/logo-layers/tara-leaf.webp');
const LAYER_COUNT = Object.keys(LAYERS).length + 1;
const READY_FALLBACK_MS = 1500; // never wait longer than this for decoding

type Props = {
  /** The bloom fully covers the screen: arrange and start the interface entrance. */
  onCovered: () => void;
  /** The intro has dissolved away and can be unmounted. */
  onDone: () => void;
};

export function BrandIntro({ onCovered, onDone }: Props) {
  const { width, height } = useWindowDimensions();
  const reduceMotion = useReducedMotion();

  // Phase 1
  const breath = useSharedValue(1); // 1 = full, Intro.pulseScale = breathed in
  // Phase 2
  const pop = useSharedValue(1);
  const travel = useSharedValue(0); // 0 = centred mark, 1 = in the logo
  // Letter build progress (spring) and opacity, t -> a -> r -> a.
  const tIn = useSharedValue(0);
  const a1In = useSharedValue(0);
  const rIn = useSharedValue(0);
  const a2In = useSharedValue(0);
  const tFade = useSharedValue(0);
  const a1Fade = useSharedValue(0);
  const rFade = useSharedValue(0);
  const a2Fade = useSharedValue(0);
  const figure = useSharedValue(0); // draw-on progress
  // Phase 3
  const bloom = useSharedValue(0);
  const veil = useSharedValue(1); // whole overlay opacity

  const loaded = useRef(0);
  const pulsing = useRef(false);
  const mountedAt = useRef(0);
  const built = useRef(false);

  // Geometry: logo centred on screen, leaf centre in screen coordinates.
  const logoLeft = (width - LOGO_W) / 2;
  const logoTop = (height - LOGO_H) / 2;
  const leafCx = logoLeft + LEAF_IN_LOGO.x + LEAF_IN_LOGO.width / 2;
  const leafCy = logoTop + LEAF_IN_LOGO.y + LEAF_IN_LOGO.height / 2;
  // Offset that moves the in-logo leaf to the screen centre (pre-loader pose).
  const toCentreX = width / 2 - leafCx;
  const toCentreY = height / 2 - leafCy;
  // Bloom circle: big enough to cover the farthest corner from the leaf.
  const bloomR = Math.ceil(
    Math.max(
      Math.hypot(leafCx, leafCy),
      Math.hypot(width - leafCx, leafCy),
      Math.hypot(leafCx, height - leafCy),
      Math.hypot(width - leafCx, height - leafCy),
    ),
  );

  const finish = useCallback(() => onDone(), [onDone]);
  const covered = useCallback(() => {
    onCovered();
    veil.set(
      withTiming(0, { duration: reduceMotion ? Intro.reducedFadeMs : Intro.dissolveMs, easing: Curves.exit }, (done) => {
        if (done) scheduleOnRN(finish);
      }),
    );
  }, [finish, onCovered, reduceMotion, veil]);

  const runBloom = useCallback(() => {
    bloom.set(
      withDelay(
        Intro.holdUntil,
        withTiming(1, { duration: reduceMotion ? Intro.reducedFadeMs : Intro.bloomMs, easing: Curves.bloom }, (done) => {
          if (done) scheduleOnRN(covered);
        }),
      ),
    );
  }, [bloom, covered, reduceMotion]);

  const build = useCallback(() => {
    if (built.current) return;
    built.current = true;
    cancelAnimation(breath);
    breath.set(withTiming(1, { duration: 160 }));

    if (reduceMotion) {
      // The complete logo simply appears in place: no travel, scaling or wipes.
      travel.set(1);
      figure.set(1);
      [tIn, a1In, rIn, a2In, tFade, a1Fade, rFade, a2Fade].forEach((v) => v.set(1));
      runBloom();
      return;
    }

    // The seed acknowledges the load, then springs into the logo.
    // Quick timed pop up, then spring back down while it travels, so it
    // arrives in the logo at its true size.
    pop.set(
      withSequence(
        withTiming(Intro.popScale, { duration: Intro.popMs, easing: Curves.settle }),
        withSpring(1, Springs.pop),
      ),
    );
    travel.set(withDelay(Intro.popMs, withSpring(1, Springs.leaf)));
    // t -> a -> r -> a, each on its own soft spring.
    const fades = [tFade, a1Fade, rFade, a2Fade];
    [tIn, a1In, rIn, a2In].forEach((v, i) => {
      const delay = Intro.letterDelay + i * Intro.letterStagger;
      v.set(withDelay(delay, withSpring(1, Springs.letter)));
      fades[i].set(withDelay(delay, withTiming(1, { duration: Intro.letterFadeMs, easing: Curves.settle })));
    });
    // The figure grows upward out of the leaf's shoulder line.
    figure.set(withDelay(Intro.figureDelay, withTiming(1, { duration: Intro.figureDrawMs, easing: Curves.settle })));
    runBloom();
  }, [a1Fade, a1In, a2Fade, a2In, breath, figure, pop, rFade, rIn, reduceMotion, runBloom, tFade, tIn, travel]);

  const startBuildWhenReady = useCallback(() => {
    const waited = Date.now() - mountedAt.current;
    const minWait = reduceMotion ? 0 : Intro.minPreloadMs;
    setTimeout(build, Math.max(0, minWait - waited));
  }, [build, reduceMotion]);

  const onLayerLoad = useCallback(() => {
    loaded.current += 1;
    if (loaded.current === LAYER_COUNT) startBuildWhenReady();
  }, [startBuildWhenReady]);

  const onLeafLoad = useCallback(() => {
    // The first JS frame now matches the native splash exactly: swap them.
    SplashScreen.hideAsync().catch(() => {});
    onLayerLoad();
  }, [onLayerLoad]);

  // Start the pre-loader breath exactly once, whatever re-renders happen.
  useEffect(() => {
    if (pulsing.current || built.current) return;
    pulsing.current = true;
    mountedAt.current = Date.now();
    if (!reduceMotion) {
      breath.set(
        withRepeat(
          withSequence(
            withTiming(Intro.pulseScale, { duration: Intro.pulseMs, easing: Curves.breathe }),
            withTiming(1, { duration: Intro.pulseMs, easing: Curves.breathe }),
          ),
          -1,
        ),
      );
    }
  }, [breath, reduceMotion]);

  useEffect(() => {
    const fallback = setTimeout(() => {
      SplashScreen.hideAsync().catch(() => {});
      build();
    }, READY_FALLBACK_MS);
    return () => clearTimeout(fallback);
  }, [build]);

  // ---- styles (UI thread) ----

  const leafStyle = useAnimatedStyle(() => {
    const t = travel.get();
    const pulse = interpolate(breath.get(), [Intro.pulseScale, 1], [Intro.pulseOpacity, 1]);
    return {
      opacity: t > 0 ? 1 : pulse,
      transform: [
        { translateX: toCentreX * (1 - t) },
        { translateY: toCentreY * (1 - t) },
        { scale: interpolate(t, [0, 1], [MARK_SCALE, 1]) * pop.get() * breath.get() },
      ],
    };
  });

  const tStyle = useAnimatedStyle(() => letterPose(tIn.get(), tFade.get(), 0));
  const a1Style = useAnimatedStyle(() => letterPose(a1In.get(), a1Fade.get(), 1));
  const rStyle = useAnimatedStyle(() => letterPose(rIn.get(), rFade.get(), 2));
  const a2Style = useAnimatedStyle(() => letterPose(a2In.get(), a2Fade.get(), 3));

  const figureClipStyle = useAnimatedStyle(() => ({ height: FIGURE_H * figure.get() }));
  const figureFadeStyle = useAnimatedStyle(() => ({ opacity: Math.min(1, figure.get() * 2.5) }));

  const bloomStyle = useAnimatedStyle(() => ({
    opacity: reduceMotion ? bloom.get() : 1,
    transform: [{ scale: reduceMotion ? 1 : Math.max(bloom.get(), 0.0001) }],
  }));
  const veilStyle = useAnimatedStyle(() => ({ opacity: veil.get() }));

  return (
    <Animated.View
      style={[StyleSheet.absoluteFill, styles.canvas, veilStyle]}
      pointerEvents="none"
      accessible
      accessibilityLabel="tara"
    >
      <View style={[styles.logo, { left: logoLeft, top: logoTop }]}>
        <Animated.Image source={LAYERS.t} onLoad={onLayerLoad} fadeDuration={0} style={[styles.layer, tStyle]} />
        <Animated.Image source={LAYERS.a1} onLoad={onLayerLoad} fadeDuration={0} style={[styles.layer, a1Style]} />
        <Animated.Image source={LAYERS.r} onLoad={onLayerLoad} fadeDuration={0} style={[styles.layer, rStyle]} />
        <Animated.Image source={LAYERS.a2} onLoad={onLayerLoad} fadeDuration={0} style={[styles.layer, a2Style]} />
        {/* Draw-on: a clip anchored at her shoulders that grows upward. */}
        <View style={styles.figureBox}>
          <Animated.View style={[styles.figureClip, figureClipStyle]}>
            <Animated.Image
              source={LAYERS.figure}
              onLoad={onLayerLoad}
              fadeDuration={0}
              style={[styles.figure, figureFadeStyle]}
            />
          </Animated.View>
        </View>
        <Animated.Image
          source={LEAF}
          onLoad={onLeafLoad}
          fadeDuration={0}
          style={[
            styles.leaf,
            {
              left: LEAF_IN_LOGO.x,
              top: LEAF_IN_LOGO.y,
              width: LEAF_IN_LOGO.width,
              height: LEAF_IN_LOGO.height,
            },
            leafStyle,
          ]}
        />
      </View>

      <Animated.View
        style={[
          styles.bloom,
          { left: leafCx - bloomR, top: leafCy - bloomR, width: bloomR * 2, height: bloomR * 2, borderRadius: bloomR },
          bloomStyle,
        ]}
      />
    </Animated.View>
  );
}

const styles = StyleSheet.create({
  canvas: {
    backgroundColor: Brand.porcelain,
    zIndex: 10,
  },
  logo: {
    position: 'absolute',
    width: LOGO_W,
    height: LOGO_H,
  },
  layer: {
    position: 'absolute',
    left: 0,
    top: 0,
    width: LOGO_W,
    height: LOGO_H,
  },
  figureBox: {
    position: 'absolute',
    left: 0,
    top: 0,
    width: LOGO_W,
    height: FIGURE_H,
  },
  figureClip: {
    position: 'absolute',
    left: 0,
    bottom: 0,
    width: LOGO_W,
    overflow: 'hidden',
  },
  figure: {
    // Full logo-sized layer, bottom-pinned so its top stays at the logo's top
    // (the clip's bottom edge is at FIGURE_H) while the clip grows upward.
    position: 'absolute',
    left: 0,
    bottom: -(LOGO_H - FIGURE_H),
    width: LOGO_W,
    height: LOGO_H,
  },
  leaf: {
    position: 'absolute',
  },
  bloom: {
    position: 'absolute',
    backgroundColor: Brand.cherryMist,
  },
});
