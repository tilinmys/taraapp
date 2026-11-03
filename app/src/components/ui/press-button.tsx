import type { ReactNode } from 'react';
import { Platform, Pressable, StyleSheet, type StyleProp, type ViewStyle } from 'react-native';
import Animated, {
  Easing,
  interpolate,
  interpolateColor,
  useAnimatedStyle,
  useReducedMotion,
  useSharedValue,
  withSpring,
  withTiming,
} from 'react-native-reanimated';

import { AppText } from '@/components/ui/app-text';
import { Brand } from '@/constants/brand';
import { Physics, Press } from '@/constants/motion';
import { haptic as fireHaptic, type HapticKind } from '@/lib/haptics';

// Tactile button for tara.
//   press    springs to 0.97 on press-in and bounces back to 1.0 on release
//   haptic   calibrated by weight (lib/haptics.ts); primary defaults to heavy
//   hover /  magnetic 2 pt lift with a soft cherry glow (tablets with a
//   focus    pointer, keyboards, web); a visible focus ring for keyboards
// Variants: primary (filled cherry), secondary (outlined), social (outlined,
// icon + label), link (text only, for low-emphasis actions).

type Variant = 'primary' | 'secondary' | 'social' | 'link';

type Props = {
  label: string;
  onPress?: () => void;
  variant?: Variant;
  icon?: ReactNode;
  disabled?: boolean;
  accessibilityHint?: string;
  style?: StyleProp<ViewStyle>;
  /** Dim to 45 % when disabled (default). Off when a parent animates the dim. */
  dimWhenDisabled?: boolean;
  /** Custom label content (e.g. an animated label); `label` stays the a11y name. */
  children?: ReactNode;
  /** Feedback weight on press. Default: heavy for primary, light otherwise. */
  haptic?: HapticKind;
};

const AnimatedPressable = Animated.createAnimatedComponent(Pressable);
const pressEase = Easing.bezier(0, 0, 0.2, 1); // ease-out

export function PressButton({
  label,
  onPress,
  variant = 'primary',
  icon,
  disabled,
  accessibilityHint,
  style,
  dimWhenDisabled = true,
  children,
  haptic,
}: Props) {
  const reduceMotion = useReducedMotion();
  const pressed = useSharedValue(1); // scale
  const lifted = useSharedValue(0); // 0..1 hover/focus
  const focused = useSharedValue(0);

  const animatedStyle = useAnimatedStyle(() => {
    const l = Math.max(lifted.get(), focused.get());
    const restBorder = variant === 'primary' ? Brand.cherry : variant === 'link' ? Brand.porcelain : Brand.outline;
    return {
      transform: [{ translateY: reduceMotion ? 0 : Press.lift * lifted.get() }, { scale: pressed.get() }],
      // Radiant glow: a soft cherry shadow that blooms on hover / focus.
      shadowOpacity: variant === 'link' ? 0 : interpolate(l, [0, 1], [variant === 'primary' ? 0.16 : 0, 0.34]),
      borderColor: variant === 'link' ? 'transparent' : interpolateColor(l, [0, 1], [restBorder, Brand.cherry]),
    };
  });

  const lift = (on: boolean) => lifted.set(withTiming(on ? 1 : 0, { duration: Press.liftMs, easing: pressEase }));

  return (
    <AnimatedPressable
      accessibilityRole="button"
      accessibilityLabel={label}
      accessibilityHint={accessibilityHint}
      accessibilityState={{ disabled: !!disabled }}
      disabled={disabled}
      onPress={() => {
        fireHaptic(haptic ?? (variant === 'primary' ? 'heavy' : 'light'));
        onPress?.();
      }}
      onPressIn={() => pressed.set(withSpring(Physics.pressScale, Physics.press))}
      onPressOut={() => pressed.set(withSpring(1, Physics.press))}
      onHoverIn={() => lift(true)}
      onHoverOut={() => lift(false)}
      onFocus={() => {
        focused.set(1);
        lift(true);
      }}
      onBlur={() => {
        focused.set(0);
        lift(false);
      }}
      android_ripple={variant === 'primary' ? { color: 'rgba(255,255,255,0.16)' } : { color: Brand.cherryMist }}
      style={[styles.base, styles[variant], disabled && dimWhenDisabled && styles.disabled, animatedStyle, style]}
    >
      {icon}
      {children ?? (
        <AppText
          variant={variant === 'primary' || variant === 'secondary' ? 'button' : 'buttonSmall'}
          color={labelColor[variant]}
          numberOfLines={1}
          maxFontSizeMultiplier={1.4}
        >
          {label}
        </AppText>
      )}
    </AnimatedPressable>
  );
}

const styles = StyleSheet.create({
  base: {
    minHeight: 56, // above the 48 dp touch-target minimum
    borderRadius: 16,
    borderWidth: 1.5,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 10,
    paddingHorizontal: 20,
    overflow: Platform.OS === 'android' ? 'hidden' : 'visible', // ripple clip on Android, glow on iOS
    shadowColor: Brand.cherry,
    shadowOffset: { width: 0, height: 8 },
    shadowRadius: 18,
  },
  primary: {
    backgroundColor: Brand.cherry,
    borderColor: Brand.cherry,
  },
  secondary: {
    backgroundColor: Brand.surface,
    borderColor: Brand.outline,
  },
  social: {
    backgroundColor: Brand.surface,
    borderColor: Brand.outline,
    minHeight: 52,
    flex: 1,
  },
  link: {
    backgroundColor: 'transparent',
    borderColor: 'transparent',
    minHeight: 48,
  },
  disabled: {
    opacity: 0.45,
  },
});

const labelColor: Record<Variant, string> = {
  primary: Brand.onCherry,
  secondary: Brand.ink,
  social: Brand.ink,
  link: Brand.cherry,
};
