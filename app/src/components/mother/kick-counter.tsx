import { Pressable, StyleSheet, View } from 'react-native';
import Animated, {
  Easing,
  useAnimatedStyle,
  useReducedMotion,
  useSharedValue,
  withDelay,
  withSequence,
  withSpring,
  withTiming,
} from 'react-native-reanimated';
import Svg, { Circle, Ellipse, G, Path, Rect } from 'react-native-svg';

import { AppText } from '@/components/ui/app-text';
import { PressButton } from '@/components/ui/press-button';
import { Colors, Haptic } from '@/constants/theme';
import { trackerStore, useTracker } from '@/data/pregnancy';
import { useT } from '@/features/onboarding/context';

// Kick counter (opened from "Count kicks" on Pregnancy, or Home's Kicks
// tile). A tiny drawn scene, not a picture: her hand rests on her belly and
// every tap is a kick from inside, felt in her palm:
//   kick      the baby's foot thrusts up toward the hand (90 ms) and
//             springs back
//   impact    +70 ms: the hand jolts up off the belly and settles (spring),
//             the belly bulges 4 % and a ripple rings out from the contact
//             point; light haptic (success on the 10th)
//   count     the ring fills, the number ticks, "+1" floats up
// All transform/opacity on the UI thread, vector art (crisp at any size).
// 10 kicks gives a calm "well done" and ticks "Log 10 kick counts" on the
// daily checklist. Reduced motion: ring and number update, nothing moves.

const RING = 196;
const STROKE = 10;
const R = (RING - STROKE) / 2;
const C = 2 * Math.PI * R;
const BELLY = 142;

const SKIN = '#EDB592';
const SKIN_DARK = '#D99772';
const FOOT = '#F6C9AB';
const FOOT_LINE = '#DE9C7A';

/** The baby's foot, sole towards us, toes up. */
function Foot() {
  return (
    <Svg width={34} height={50} viewBox="0 0 40 60">
      <Path
        d="M20 58 C11 58 7 50 7 41 C7 31 9 22 13 18 C16 15 26 15 29 19 C33 24 34 33 33 42 C32 51 28 58 20 58 Z"
        fill={FOOT}
        stroke={FOOT_LINE}
        strokeWidth={1.5}
      />
      <Ellipse cx={26} cy={10} rx={5} ry={6} fill={FOOT} stroke={FOOT_LINE} strokeWidth={1.5} />
      <Ellipse cx={18} cy={7.5} rx={3.6} ry={4.3} fill={FOOT} stroke={FOOT_LINE} strokeWidth={1.5} />
      <Circle cx={12} cy={9.8} r={3.1} fill={FOOT} stroke={FOOT_LINE} strokeWidth={1.5} />
      <Circle cx={7.4} cy={13.6} r={2.6} fill={FOOT} stroke={FOOT_LINE} strokeWidth={1.5} />
      <Circle cx={4.6} cy={18.6} r={2.2} fill={FOOT} stroke={FOOT_LINE} strokeWidth={1.5} />
    </Svg>
  );
}

/** Her hand, palm down on the belly, fingers spread a little. */
function Hand() {
  return (
    <Svg width={66} height={66} viewBox="0 0 64 64">
      <G>
        <Rect x={4} y={30} width={20} height={10} rx={5} fill={SKIN_DARK} transform="rotate(-38 14 35)" />
        <Rect x={14} y={7} width={8.5} height={28} rx={4.25} fill={SKIN} />
        <Rect x={23.5} y={2} width={8.5} height={32} rx={4.25} fill={SKIN} />
        <Rect x={33} y={4} width={8.5} height={30} rx={4.25} fill={SKIN} />
        <Rect x={42.5} y={10} width={7.5} height={24} rx={3.75} fill={SKIN} />
        <Rect x={13} y={24} width={38} height={32} rx={14} fill={SKIN} />
        <Path d="M22 34 Q32 38 44 34" stroke={SKIN_DARK} strokeWidth={1.4} fill="none" strokeLinecap="round" opacity={0.6} />
      </G>
    </Svg>
  );
}

export function KickCounter() {
  const { kicks, kickStart, lastKick } = useTracker();
  const t = useT();
  const reduceMotion = useReducedMotion();
  const kick = useSharedValue(0);
  const jolt = useSharedValue(0);
  const ripple = useSharedValue(0);
  const bulge = useSharedValue(0);
  const plus = useSharedValue(0);

  const footStyle = useAnimatedStyle(() => ({
    transform: [{ translateX: 16 * kick.get() }, { translateY: -22 * kick.get() }, { rotate: `${18 + 10 * kick.get()}deg` }],
  }));
  const handStyle = useAnimatedStyle(() => ({
    transform: [{ translateX: 5 * jolt.get() }, { translateY: -9 * jolt.get() }, { rotate: `${-28 - 7 * jolt.get()}deg` }],
  }));
  const bellyStyle = useAnimatedStyle(() => ({ transform: [{ scale: 1 + 0.04 * bulge.get() }] }));
  const rippleStyle = useAnimatedStyle(() => ({
    opacity: ripple.get() > 0 && ripple.get() < 1 ? 0.7 * (1 - ripple.get()) : 0,
    transform: [{ scale: 0.3 + 1.3 * ripple.get() }],
  }));
  const plusStyle = useAnimatedStyle(() => ({ opacity: plus.get() > 0 ? 1 - plus.get() : 0, transform: [{ translateY: -36 * plus.get() }] }));

  const done = kicks >= 10;
  // Time from the first to the latest kick (no clock reads during render).
  const minutes = kickStart && lastKick ? Math.max(1, Math.round((lastKick - kickStart) / 60000)) : 0;

  const onKick = () => {
    trackerStore.kick();
    if (kicks + 1 === 10) Haptic.success();
    else Haptic.light();
    if (reduceMotion) return;
    const out = Easing.out(Easing.cubic);
    kick.set(withSequence(withTiming(1, { duration: 90, easing: out }), withSpring(0, { damping: 9, stiffness: 240 })));
    jolt.set(withDelay(70, withSequence(withTiming(1, { duration: 80, easing: out }), withSpring(0, { damping: 7, stiffness: 200 }))));
    bulge.set(withDelay(70, withSequence(withTiming(1, { duration: 80 }), withSpring(0, { damping: 10, stiffness: 260 }))));
    ripple.set(0);
    ripple.set(withDelay(70, withTiming(1, { duration: 520, easing: out })));
    plus.set(0);
    plus.set(withTiming(1, { duration: 700 }));
  };

  return (
    <View style={styles.kick}>
      <Pressable accessibilityRole="button" accessibilityLabel={t('countAKick', { n: kicks })} onPress={onKick}>
        <View style={styles.target}>
          <Svg width={RING} height={RING} style={StyleSheet.absoluteFill}>
            <Circle cx={RING / 2} cy={RING / 2} r={R} stroke={Colors.surface} strokeWidth={STROKE} fill="none" />
            <Circle
              cx={RING / 2}
              cy={RING / 2}
              r={R}
              stroke={done ? Colors.sage : Colors.pumpkin}
              strokeWidth={STROKE}
              strokeLinecap="round"
              fill="none"
              strokeDasharray={`${C}`}
              strokeDashoffset={C * (1 - Math.min(kicks, 10) / 10)}
              transform={`rotate(-90 ${RING / 2} ${RING / 2})`}
            />
          </Svg>
          {/* Belly: the stage. Foot inside, her hand on top, the ripple where they meet. */}
          <Animated.View style={[styles.belly, bellyStyle]} aria-hidden>
            <Animated.View style={[styles.ripple, rippleStyle]} />
            <Animated.View style={[styles.foot, footStyle]}>
              <Foot />
            </Animated.View>
            <Animated.View style={[styles.hand, handStyle]}>
              <Hand />
            </Animated.View>
          </Animated.View>
          <Animated.View style={[styles.plus, plusStyle]} pointerEvents="none" aria-hidden>
            <AppText variant="heading" color={Colors.pumpkinInk}>
              +1
            </AppText>
          </Animated.View>
        </View>
      </Pressable>
      <View style={styles.count}>
        <AppText variant="display" color={Colors.ink}>
          {String(kicks)}
        </AppText>
        <AppText variant="bodyStrong" color={Colors.inkMuted}>
          {t('ofTenKicks')}
        </AppText>
      </View>
      <View style={styles.flex}>
        <AppText variant="heading" align="center">
          {done ? t('wellDone') : t('tapEachKick')}
        </AppText>
        <AppText variant="caption" color={Colors.inkMuted} align="center">
          {done ? t('kicksDone', { n: minutes }) : kickStart ? t('kicksCounting', { n: minutes }) : t('kicksStart')}
        </AppText>
        {kicks > 0 && <PressButton label={t('startAgain')} variant="link" onPress={() => trackerStore.resetKicks()} />}
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  flex: { alignItems: 'center', gap: 4 },
  kick: { alignItems: 'center', gap: 12, padding: 20, borderRadius: 20, backgroundColor: Colors.orangeTint },
  target: { width: RING, height: RING, alignItems: 'center', justifyContent: 'center' },
  belly: {
    width: BELLY,
    height: BELLY,
    borderRadius: BELLY / 2,
    backgroundColor: '#FBE1D2',
    borderWidth: 2,
    borderColor: '#F3C9B2',
    overflow: 'visible',
  },
  // Contact point: between the foot's toes and the hand's palm.
  ripple: { position: 'absolute', left: 58, top: 30, width: 54, height: 54, borderRadius: 27, borderWidth: 3, borderColor: Colors.pumpkin },
  foot: { position: 'absolute', left: 38, top: 62 },
  hand: { position: 'absolute', left: 70, top: -8 },
  plus: { position: 'absolute', top: 14, right: 18 },
  count: { flexDirection: 'row', alignItems: 'baseline', gap: 8 },
});
