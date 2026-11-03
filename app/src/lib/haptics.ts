import * as Haptics from 'expo-haptics';
import { Platform } from 'react-native';

// Calibrated haptics: the weight of the feedback matches the weight of the
// interaction, so the phone "feels" consistent.
//
//   select   the lightest tick: crossing a week while scrubbing a slider
//   light    typing milestones, mode toggles, scrubbing table rows
//   medium   choosing something: a role card, a mother in the list
//   heavy    committing: the 56 pt primary button
//   success  a completed milestone: OTP verified, clinical action confirmed
//   warning  revealing a danger alert
//
// All calls are fire-and-forget and silently no-op where unsupported (web).

const supported = Platform.OS === 'ios' || Platform.OS === 'android';
const swallow = () => {};

export const Haptic = {
  select: () => supported && Haptics.selectionAsync().catch(swallow),
  light: () => supported && Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light).catch(swallow),
  medium: () => supported && Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Medium).catch(swallow),
  heavy: () => supported && Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Heavy).catch(swallow),
  success: () => supported && Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success).catch(swallow),
  warning: () => supported && Haptics.notificationAsync(Haptics.NotificationFeedbackType.Warning).catch(swallow),
} as const;

export type HapticKind = keyof typeof Haptic | 'none';

export function haptic(kind: HapticKind) {
  if (kind !== 'none') Haptic[kind]();
}
