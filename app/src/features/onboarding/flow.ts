import type { Href } from 'expo-router';
import { Platform } from 'react-native';
import {
  Keyframe,
  withSpring,
  type EntryAnimationsValues,
  type ExitAnimationsValues,
  type LayoutAnimation,
} from 'react-native-reanimated';

import { Curves, Onboarding, Physics } from '@/constants/motion';
import type { StringKey } from '@/i18n/strings';

// Onboarding step order, progress and page transitions.
//
// Goal-gradient progress: the welcome screen counts as begun, so the bar is
// never empty and visibly advances 25 % -> 50 % -> 75 %; the last quarter
// fills when the user lands in their workspace.

export const STEPS = [
  { route: '/language', name: 'stepLanguage' },
  { route: '/role', name: 'stepWorkspace' },
  { route: '/auth', name: 'stepVerify' },
] as const satisfies readonly { route: Href; name: StringKey }[];

export const TOTAL_STEPS = STEPS.length;

export function stepIndex(pathname: string) {
  const i = STEPS.findIndex((s) => pathname.endsWith(s.route));
  return i < 0 ? 0 : i;
}

export function progressFor(index: number) {
  return (index + 1) / (TOTAL_STEPS + 1);
}

export type Direction = 1 | -1;

// Horizontal parallax: the incoming page slides in from the right while the
// outgoing one shifts 35 % left, scales to 0.96 and dims to 0.8 (mirrored
// for back). On iOS / Android it runs on the spec spring (damping 20,
// stiffness 180) on the UI thread. Reanimated's web build cannot run custom
// layout springs, so the web preview uses an equivalent timed keyframe.

function nativePage(direction: Direction, width: number) {
  const away = -width * Onboarding.parallax;
  const entering = (_: EntryAnimationsValues): LayoutAnimation => {
    'worklet';
    const fromX = direction === 1 ? width : away;
    const fromScale = direction === 1 ? 1 : Onboarding.recedeScale;
    const fromOpacity = direction === 1 ? 1 : Onboarding.recedeOpacity;
    return {
      initialValues: { transform: [{ translateX: fromX }, { scale: fromScale }], opacity: fromOpacity },
      animations: {
        transform: [{ translateX: withSpring(0, Physics.page) }, { scale: withSpring(1, Physics.page) }],
        opacity: withSpring(1, Physics.page),
      },
    };
  };
  const exiting = (_: ExitAnimationsValues): LayoutAnimation => {
    'worklet';
    const toX = direction === 1 ? away : width;
    const toScale = direction === 1 ? Onboarding.recedeScale : 1;
    const toOpacity = direction === 1 ? Onboarding.recedeOpacity : 1;
    return {
      initialValues: { transform: [{ translateX: 0 }, { scale: 1 }], opacity: 1 },
      animations: {
        transform: [{ translateX: withSpring(toX, Physics.page) }, { scale: withSpring(toScale, Physics.page) }],
        opacity: withSpring(toOpacity, Physics.page),
      },
    };
  };
  return { entering, exiting };
}

function webPage(direction: Direction, width: number) {
  const recede = {
    transform: [{ translateX: -width * Onboarding.parallax }, { scale: Onboarding.recedeScale }],
    opacity: Onboarding.recedeOpacity,
  };
  const rest = { transform: [{ translateX: 0 }, { scale: 1 }], opacity: 1 };
  const offRight = { transform: [{ translateX: width }, { scale: 1 }], opacity: 1 };
  const entering =
    direction === 1
      ? new Keyframe({ 0: offRight, 100: { ...rest, easing: Curves.page } })
      : new Keyframe({ 0: recede, 100: { ...rest, easing: Curves.page } });
  const exiting =
    direction === 1
      ? new Keyframe({ 0: rest, 100: { ...recede, easing: Curves.page } })
      : new Keyframe({ 0: rest, 100: { ...offRight, easing: Curves.page } });
  return { entering: entering.duration(Onboarding.pageMs), exiting: exiting.duration(Onboarding.pageMs) };
}

export function pageAnimations(direction: Direction, width: number) {
  return Platform.OS === 'web' ? webPage(direction, width) : nativePage(direction, width);
}
