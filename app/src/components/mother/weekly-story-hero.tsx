import { useIsFocused } from 'expo-router';
import { useEffect, useRef, useState } from 'react';
import { Image, Platform, Pressable, StyleSheet, View } from 'react-native';
import Animated, {
  cancelAnimation,
  Easing,
  FadeIn,
  FadeInDown,
  LinearTransition,
  useAnimatedStyle,
  useReducedMotion,
  useSharedValue,
  withDelay,
  withRepeat,
  withSequence,
  withSpring,
  withTiming,
} from 'react-native-reanimated';

import { Icon } from '@/components/system/icon';
import { AppText } from '@/components/ui/app-text';
import { Brand } from '@/constants/brand';
import { fontFor } from '@/constants/typography';
import { Haptic } from '@/lib/haptics';
import { artKey, babyFor, clampWeek, MAX_SCRUB_WEEK, MIN_SCRUB_WEEK, usePrefetchWeekWindow, useStoryLayout, weekInfo } from '@/services/canva-media-engine';

// "It's been a week" story, inside the mother's hero card (peak-end rule:
// a small, warm moment every time a new week starts).
//   Step 1  the week's fruit glides in from the right (x 180 -> 0, scale
//           0.75 -> 1.06 -> 1, rotate 12deg -> 0, spring 13/200) and the size
//           badge pops underneath. "What can baby do now? · Next".
//   Step 2  the fruit shrinks away, the baby appears in the 92 pt circle and
//           her speech bubble opens: what she can do now, said to Amma.
//           The baby floats (+/-3 pt, 2.4 s).
// In the banner, < > pills step through this week and past weeks (next week
// is a locked teaser): the art glides in from the side, and the baby's
// words are re-written word by word with a light shimmer.
// The story plays once per app session; "Replay week reveal" plays it again.

type Step = 1 | 2;
let storySeen = false;
/** The baby's hello overlay (components/mother/baby-hello.tsx) already revealed this week. */
export const markStorySeen = () => {
  storySeen = true;
};
export const isStorySeen = () => storySeen;

let helloClaim: boolean | null = null;
/**
 * Once per app session: should the baby's hello play now? Idempotent (safe
 * under StrictMode double calls) and marks the story seen, so the weekly
 * hero opens straight on its banner under the hello.
 */
export const claimHello = (skip: boolean) => {
  if (helloClaim === null) {
    helloClaim = !storySeen && !skip;
    storySeen = true;
  }
  return helloClaim;
};

const GLIDE = { damping: 13, stiffness: 200 };
const FLOAT = Easing.inOut(Easing.sin);

type Props = {
  realWeek: number;
  week: number;
  onWeekChange: (w: number) => void;
  /** Open straight on the banner (demos, deep links: /mother?story=skip). */
  skipStory?: boolean;
};

export function WeeklyStoryHero({ realWeek, week, onWeekChange, skipStory = false }: Props) {
  const reduceMotion = useReducedMotion();
  const { circle } = useStoryLayout();
  const [step, setStep] = useState<Step>(storySeen || skipStory ? 2 : 1);
  const info = weekInfo(week);
  usePrefetchWeekWindow(week);

  // ---- Step 2: fruit glide-in + badge pop; then the morph into the circle
  const fx = useSharedValue(180);
  const fs = useSharedValue(0.75);
  const fr = useSharedValue(12);
  const fy = useSharedValue(0);
  const badge = useSharedValue(0);
  useEffect(() => {
    if (step !== 1) return;
    if (reduceMotion) {
      fx.set(0);
      fs.set(1);
      fr.set(0);
      badge.set(1);
      return;
    }
    fx.set(180);
    fs.set(0.75);
    fr.set(12);
    fy.set(0);
    badge.set(0);
    fx.set(withSpring(0, GLIDE));
    fs.set(withSequence(withSpring(1.06, GLIDE), withSpring(1, GLIDE)));
    fr.set(withSpring(0, GLIDE));
    badge.set(withDelay(260, withSpring(1, { damping: 12, stiffness: 240 })));
  }, [badge, fr, fs, fx, fy, reduceMotion, step]);
  const fruitStyle = useAnimatedStyle(() => ({
    transform: [{ translateX: fx.get() }, { translateY: fy.get() }, { scale: fs.get() }, { rotate: `${fr.get()}deg` }],
  }));
  const badgeStyle = useAnimatedStyle(() => ({ opacity: badge.get(), transform: [{ scale: 0.8 + 0.2 * badge.get() }] }));

  const toBanner = () => {
    Haptic.medium();
    storySeen = true;
    if (reduceMotion) {
      setStep(2);
      return;
    }
    // Morph: the big fruit shrinks toward the circle position, then the banner takes over.
    fx.set(withSpring(-90, GLIDE));
    fy.set(withSpring(-10, GLIDE));
    fs.set(withSpring(0.55, GLIDE));
    setTimeout(() => setStep(2), 240);
  };

  // ---- Step 3: banner (circle fruit float + week glide, bubble open, quote shimmer)
  const cx = useSharedValue(0);
  const cs = useSharedValue(1);
  const cr = useSharedValue(0);
  const float = useSharedValue(0);
  const open = useSharedValue(storySeen || skipStory ? 1 : 0);
  const bubbleW = useSharedValue(0);
  const shimmer = useSharedValue(-1);
  // Runs on every focus of the Home tab: a hidden tab gets no style writes,
  // so leaving mid-animation could strand the bubble at opacity 0. Blur
  // snaps everything final and stops the float loop (no work off-screen);
  // focus replays a short settle that repaints while visible.
  const focused = useIsFocused();
  const opened = useRef(false);
  useEffect(() => {
    if (step !== 2) return;
    if (!focused) {
      cancelAnimation(float);
      cancelAnimation(open);
      cancelAnimation(cs);
      open.set(1);
      cs.set(1);
      return;
    }
    if (reduceMotion) {
      open.set(0.999);
      open.set(withTiming(1, { duration: 16 }));
      return;
    }
    const first = !opened.current;
    opened.current = true;
    cs.set(first ? 1.3 : 1.04);
    cs.set(withSpring(1, GLIDE));
    if (first) open.set(withSpring(1, { damping: 16, stiffness: 180 }, (done) => !done && open.set(1)));
    else {
      open.set(0.95);
      open.set(withTiming(1, { duration: 160 }));
    }
    float.set(withRepeat(withSequence(withTiming(-3, { duration: 1200, easing: FLOAT }), withTiming(3, { duration: 1200, easing: FLOAT })), -1, true));
    return () => cancelAnimation(float);
  }, [cs, float, focused, open, reduceMotion, step]);

  const lastWeek = useRef(week);
  useEffect(() => {
    const prev = lastWeek.current;
    lastWeek.current = week;
    if (step !== 2 || prev === week || reduceMotion) return;
    const dir = week > prev ? 1 : -1;
    if (artKey(prev) !== artKey(week)) {
      cx.set(48 * dir);
      cs.set(0.75);
      cr.set(12 * dir);
      cx.set(withSpring(0, GLIDE));
      cs.set(withSequence(withSpring(1.06, GLIDE), withSpring(1, GLIDE)));
      cr.set(withSpring(0, GLIDE));
    } else {
      cs.set(withSequence(withTiming(0.94, { duration: 70 }), withSpring(1, GLIDE)));
    }
    shimmer.set(-1);
    shimmer.set(withDelay(120, withTiming(1, { duration: 700, easing: Easing.out(Easing.quad) })));
  }, [cr, cs, cx, reduceMotion, shimmer, step, week]);

  const circleArt = useAnimatedStyle(() => ({
    transform: [{ translateX: cx.get() }, { translateY: float.get() }, { scale: cs.get() }, { rotate: `${cr.get()}deg` }],
  }));
  // The bubble opens from its left edge (where it touches the circle).
  const bubbleStyle = useAnimatedStyle(() => {
    const s = 0.6 + 0.4 * open.get();
    return { opacity: open.get(), transform: [{ translateX: (-(1 - s) * bubbleW.get()) / 2 }, { scale: s }] };
  });
  const shimmerStyle = useAnimatedStyle(() => ({
    opacity: shimmer.get() > -1 && shimmer.get() < 1 ? 0.5 : 0,
    transform: [{ translateX: shimmer.get() * bubbleW.get() }, { rotate: '14deg' }],
  }));

  const replay = () => {
    Haptic.light();
    storySeen = false;
    onWeekChange(realWeek);
    setStep(1);
  };
  const go = (d: number) => {
    // Only this week and earlier weeks: the future stays a surprise.
    const w = Math.min(realWeek, clampWeek(week + d));
    if (w === week) return;
    Haptic.select();
    onWeekChange(w);
  };

  const layout = reduceMotion ? undefined : LinearTransition.springify().damping(18).stiffness(200);
  const words = info.quote.split(' ');

  return (
    <View style={styles.wrap}>
      <Animated.View layout={layout} style={styles.card}>
        {step === 1 && (
          <View style={styles.reveal}>
            <Animated.View style={[styles.bigFruit, fruitStyle]}>
              <Image source={info.art} style={styles.bigFruitImg} resizeMode="contain" fadeDuration={0} accessibilityLabel={`Baby is about the size of ${info.like}`} />
            </Animated.View>
            <Animated.View style={[styles.sizeBadge, badgeStyle]}>
              <AppText variant="label" color={Brand.cherry} numberOfLines={1} adjustsFontSizeToFit minimumFontScale={0.85}>
                {`Size of ${info.like} · ${info.weightLabel}`}
              </AppText>
            </Animated.View>
            <StoryPill label="What can baby do now? · Next →" onPress={toBanner} />
          </View>
        )}

        {step === 2 && (
          <View style={styles.bannerWrap}>
            <View style={styles.bannerHead}>
              <AppText variant="overline" color={Brand.inkMuted}>
                {week === realWeek ? 'THIS WEEK' : `YOU ARE IN WEEK ${realWeek}`}
              </AppText>
              <Pressable accessibilityRole="button" accessibilityLabel="Replay week reveal" onPress={replay} hitSlop={6} style={({ pressed }) => [styles.replay, pressed && styles.pressed]}>
                <Icon name="reset" size={14} color={Brand.cherry} weight="bold" />
                <AppText variant="caption" color={Brand.cherry}>
                  Replay week reveal
                </AppText>
              </Pressable>
            </View>
            <View style={styles.banner}>
              <View style={[styles.circle, { width: circle, height: circle, borderRadius: circle / 2 }]} aria-hidden>
                <Animated.View style={circleArt}>
                  {/* The baby is the one speaking, so she sits beside her words. */}
                  <Image source={babyFor(week).src} style={{ width: circle * 0.86, height: circle * 0.86 }} resizeMode="contain" fadeDuration={0} />
                </Animated.View>
              </View>
              <Animated.View style={[styles.bubbleWrap, bubbleStyle]} onLayout={(e) => bubbleW.set(e.nativeEvent.layout.width)}>
                <View style={styles.bubble} accessible accessibilityLabel={`Week ${info.week}. Size of ${info.like}, ${info.weightLabel}. Your baby says: ${info.quote}`}>
                  <AppText style={styles.watermark} aria-hidden>
                    “
                  </AppText>
                  <AppText style={styles.weekTitle} numberOfLines={1}>{`Week ${info.week}`}</AppText>
                  <View style={styles.sizePill}>
                    <AppText style={styles.sizePillText} numberOfLines={1} adjustsFontSizeToFit minimumFontScale={0.85}>
                      {`Size of ${info.like} · ${info.weightLabel}`}
                    </AppText>
                  </View>
                  <View style={styles.quote} key={`q-${week}`}>
                    {words.map((wd, i) => (
                      <Animated.Text
                        key={`${week}-${i}`}
                        // Web: Reanimated leaves entering words position:absolute, which
                        // collapses the bubble, so the word cascade is native-only.
                        entering={reduceMotion || Platform.OS === 'web' ? undefined : FadeInDown.duration(380).delay(i * 35).withInitialValues({ opacity: 0, transform: [{ translateY: 8 }] })}
                        style={[fontFor('input'), styles.quoteWord]}
                      >
                        {`${i === 0 ? '“' : ''}${wd}${i === words.length - 1 ? '”' : ''} `}
                      </Animated.Text>
                    ))}
                  </View>
                  <View style={styles.wave} aria-hidden>
                    <Icon name="wave" size={14} color={Brand.cherry} />
                  </View>
                  <Animated.View pointerEvents="none" style={[styles.shimmer, shimmerStyle]} />
                </View>
                <View style={styles.pointer} />
              </Animated.View>
            </View>
          </View>
        )}
      </Animated.View>

      {step === 2 && (
        <Animated.View entering={reduceMotion ? undefined : FadeIn.duration(260)} style={styles.stepper}>
          <WeekPill dir={-1} week={week - 1} disabled={week <= MIN_SCRUB_WEEK} onPress={() => go(-1)} />
          {week >= realWeek ? (
            <LockedPill week={realWeek + 1} />
          ) : (
            <WeekPill dir={1} week={week + 1} disabled={week >= MAX_SCRUB_WEEK} onPress={() => go(1)} />
          )}
        </Animated.View>
      )}
      {step === 2 && week !== realWeek && (
        <Pressable accessibilityRole="button" onPress={() => { Haptic.light(); onWeekChange(realWeek); }} style={styles.backToMine}>
          <AppText variant="buttonSmall" color={Brand.cherry}>{`Back to my week ${realWeek}`}</AppText>
        </Pressable>
      )}
    </View>
  );
}

function StoryPill({ label, onPress }: { label: string; onPress: () => void }) {
  const s = useSharedValue(1);
  const style = useAnimatedStyle(() => ({ transform: [{ scale: s.get() }] }));
  return (
    <Animated.View style={style}>
      <Pressable
        accessibilityRole="button"
        accessibilityLabel={label.replace(' →', '')}
        onPressIn={() => s.set(withSpring(0.96, { stiffness: 300, damping: 20 }))}
        onPressOut={() => s.set(withSpring(1, { stiffness: 300, damping: 20 }))}
        onPress={onPress}
        style={styles.storyPill}
      >
        <AppText variant="buttonSmall" color={Brand.cherry} numberOfLines={1} adjustsFontSizeToFit minimumFontScale={0.85}>
          {label}
        </AppText>
      </Pressable>
    </Animated.View>
  );
}

function WeekPill({ dir, week, disabled, onPress }: { dir: 1 | -1; week: number; disabled: boolean; onPress: () => void }) {
  const info = weekInfo(week);
  return (
    <Pressable
      accessibilityRole="button"
      accessibilityLabel={dir < 0 ? `Previous week, week ${week}` : `Next week, week ${week}`}
      disabled={disabled}
      onPress={onPress}
      style={({ pressed }) => [styles.weekPill, dir > 0 && styles.weekPillNext, disabled && styles.disabled, pressed && styles.pressed]}
    >
      {dir < 0 && <Icon name="back" size={16} color={Brand.cherry} weight="bold" />}
      <View style={styles.weekPillText}>
        <AppText variant="label" numberOfLines={1}>{disabled ? '—' : `Week ${week}`}</AppText>
        {!disabled && (
          <AppText variant="caption" color={Brand.inkMuted} numberOfLines={1}>
            {info.fruit}
          </AppText>
        )}
      </View>
      {dir > 0 && <Icon name="forward" size={16} color={Brand.cherry} weight="bold" />}
    </Pressable>
  );
}

/** Next week, not yet reached: a quiet teaser instead of a spoiler. */
function LockedPill({ week }: { week: number }) {
  const done = week > MAX_SCRUB_WEEK;
  return (
    <View
      accessible
      accessibilityLabel={done ? 'Your baby is due' : `Week ${week} unlocks next week`}
      style={[styles.weekPill, styles.weekPillNext, styles.locked]}
    >
      <View style={styles.weekPillText}>
        <AppText variant="label" numberOfLines={1} color={Brand.inkMuted}>
          {done ? 'Due any day' : `Week ${week}`}
        </AppText>
        <AppText variant="caption" color={Brand.inkMuted} numberOfLines={1}>
          {done ? 'Meet your baby soon' : 'Unlocks next week'}
        </AppText>
      </View>
      <Icon name="lock" size={16} color={Brand.inkMuted} weight="bold" />
    </View>
  );
}

const styles = StyleSheet.create({
  wrap: { gap: 12 },
  pressed: { opacity: 0.7 },
  disabled: { opacity: 0.35 },
  card: { padding: 20, borderRadius: 20, backgroundColor: Brand.band, overflow: 'hidden' },
  storyPill: {
    minHeight: 48,
    paddingHorizontal: 20,
    borderRadius: 24,
    alignItems: 'center',
    justifyContent: 'center',
    borderWidth: 2,
    borderColor: Brand.cherry,
    backgroundColor: Brand.cherryMist,
  },
  // Step 2
  reveal: { alignItems: 'center', gap: 12 },
  bigFruit: { width: 168, height: 168, alignItems: 'center', justifyContent: 'center' },
  bigFruitImg: { width: 168, height: 168 },
  sizeBadge: { paddingHorizontal: 14, paddingVertical: 6, borderRadius: 16, backgroundColor: Brand.surface },
  // Step 3
  bannerWrap: { gap: 10 },
  bannerHead: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: 8 },
  replay: { flexDirection: 'row', alignItems: 'center', gap: 4, paddingHorizontal: 10, paddingVertical: 4, borderRadius: 12, backgroundColor: Brand.surface },
  banner: { flexDirection: 'row', alignItems: 'center' },
  circle: { borderWidth: 2, borderColor: Brand.cherry, backgroundColor: Brand.surface, alignItems: 'center', justifyContent: 'center', overflow: 'hidden' },
  bubbleWrap: { flex: 1, minWidth: 0, marginLeft: 14 },
  bubble: { borderRadius: 24, paddingHorizontal: 16, paddingVertical: 12, paddingRight: 34, backgroundColor: Brand.cherryMist, overflow: 'hidden', gap: 4 },
  pointer: {
    position: 'absolute',
    left: -7,
    top: '50%',
    marginTop: -8,
    width: 16,
    height: 16,
    borderRadius: 2,
    backgroundColor: Brand.cherryMist,
    transform: [{ rotate: '45deg' }],
  },
  watermark: { position: 'absolute', top: -4, right: 10, fontSize: 56, lineHeight: 64, color: Brand.cherry, opacity: 0.14 },
  weekTitle: { ...fontFor('heading'), fontSize: 18, lineHeight: 24, color: Brand.ink },
  sizePill: { alignSelf: 'flex-start', maxWidth: '100%', paddingHorizontal: 10, paddingVertical: 3, borderRadius: 10, backgroundColor: Brand.surface },
  sizePillText: { ...fontFor('label'), fontSize: 14, lineHeight: 19, color: Brand.cherry },
  quote: { flexDirection: 'row', flexWrap: 'wrap', marginTop: 4 },
  quoteWord: { fontSize: 16, lineHeight: 23, color: Brand.ink },
  wave: {
    position: 'absolute',
    right: 8,
    bottom: 8,
    width: 24,
    height: 24,
    borderRadius: 12,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: Brand.surface,
  },
  shimmer: { position: 'absolute', top: -20, bottom: -20, left: 0, width: 36, backgroundColor: Brand.surface },
  // Stepper
  stepper: { flexDirection: 'row', gap: 12 },
  weekPill: {
    flex: 1,
    minHeight: 52,
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
    paddingHorizontal: 14,
    borderRadius: 16,
    borderWidth: 1.5,
    borderColor: Brand.bandLine,
    backgroundColor: Brand.surface,
  },
  weekPillNext: { justifyContent: 'flex-end' },
  locked: { borderStyle: 'dashed', backgroundColor: Brand.band },
  weekPillText: { flexShrink: 1 },
  backToMine: { alignSelf: 'center', minHeight: 40, justifyContent: 'center', paddingHorizontal: 12 },
});
