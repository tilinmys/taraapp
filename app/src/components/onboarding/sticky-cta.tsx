import { useEffect, useRef, useState } from 'react';
import { StyleSheet, View } from 'react-native';
import Animated, {
  useAnimatedStyle,
  useReducedMotion,
  useSharedValue,
  withSequence,
  withSpring,
  withTiming,
} from 'react-native-reanimated';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { scheduleOnRN } from 'react-native-worklets';

import { Icon, type IconName } from '@/components/system/icon';
import { AppText } from '@/components/ui/app-text';
import { PressButton } from '@/components/ui/press-button';
import { Brand } from '@/constants/brand';
import { Onboarding, OnboardingSprings, Signature } from '@/constants/motion';
import type { Cta } from '@/features/onboarding/context';
import { useContentWidth } from '@/hooks/use-content-width';

// The screen's single primary action, in a sticky bottom bar above the home
// indicator. Button copy rules (see docs/UI_PATTERNS.md):
//   - Label: verb + object, one line, short ("Start check-in", "Confirm
//     referral"). Specifics never go in the label.
//   - Context: the specifics go on one quiet line ABOVE the button
//     ("Lakshmi D. · 7 months · High risk"), so the user knows exactly what
//     the tap will act on.
//   - Disabled = 50 % opacity; enabling springs it to full with a small pop.
//   - Label changes cross-fade in place; the icon morphs (rotate + scale).

function MorphIcon({ name }: { name: IconName }) {
  const reduceMotion = useReducedMotion();
  const t = useSharedValue(1);
  const first = useRef(true);
  useEffect(() => {
    if (first.current) {
      first.current = false;
      return;
    }
    if (reduceMotion) return;
    t.set(0);
    t.set(withSpring(1, Signature.morph));
  }, [name, reduceMotion, t]);
  const style = useAnimatedStyle(() => ({
    opacity: Math.min(1, t.get() * 1.5),
    transform: [{ rotate: `${-90 * (1 - t.get())}deg` }, { scale: 0.5 + 0.5 * t.get() }],
  }));
  return (
    <Animated.View style={style}>
      <Icon name={name} size={22} color={Brand.onCherry} active />
    </Animated.View>
  );
}

/** `inTabs`: sits above a tab bar, which already owns the bottom safe area. */
export function StickyCta({ cta, inTabs = false }: { cta: Cta | null; inTabs?: boolean }) {
  const insets = useSafeAreaInsets();
  const { maxWidth } = useContentWidth();
  const reduceMotion = useReducedMotion();
  const enabled = !!cta?.enabled;
  const label = cta?.label ?? '';

  const dim = useSharedValue(enabled ? 1 : Onboarding.ctaDisabledOpacity);
  const pop = useSharedValue(1);
  const labelOpacity = useSharedValue(1);
  const [shown, setShown] = useState(label);
  const wasEnabled = useRef(enabled);

  useEffect(() => {
    dim.set(withTiming(enabled ? 1 : Onboarding.ctaDisabledOpacity, { duration: 180 }));
    if (enabled && !wasEnabled.current && !reduceMotion) {
      pop.set(withSequence(withTiming(1.03, { duration: 90 }), withSpring(1, OnboardingSprings.ctaWake)));
    }
    wasEnabled.current = enabled;
  }, [dim, enabled, pop, reduceMotion]);

  useEffect(() => {
    if (label === shown) return;
    labelOpacity.set(
      withTiming(0, { duration: reduceMotion ? 0 : Onboarding.ctaLabelOutMs }, (done) => {
        if (done) scheduleOnRN(setShown, label);
      }),
    );
  }, [label, labelOpacity, reduceMotion, shown]);
  useEffect(() => {
    labelOpacity.set(withTiming(1, { duration: reduceMotion ? 0 : Onboarding.ctaLabelInMs }));
  }, [labelOpacity, reduceMotion, shown]);

  const wrapStyle = useAnimatedStyle(() => ({ opacity: dim.get(), transform: [{ scale: pop.get() }] }));
  const labelStyle = useAnimatedStyle(() => ({ opacity: labelOpacity.get() }));

  return (
    <View
      style={[
        styles.bar,
        {
          paddingBottom: inTabs ? 12 : Math.max(insets.bottom, 14) + 6,
          paddingLeft: Math.max(24, insets.left + 16),
          paddingRight: Math.max(24, insets.right + 16),
        },
      ]}
    >
      <View style={[styles.inner, { maxWidth }]}>
        {cta?.dock}
        {!!cta?.context && (
          <AppText variant="caption" color={Brand.inkMuted} align="center" numberOfLines={1} style={styles.context}>
            {cta.context}
          </AppText>
        )}
        <Animated.View style={wrapStyle}>
          <PressButton
            label={label}
            disabled={!enabled}
            dimWhenDisabled={false}
            icon={cta?.icon ? <MorphIcon name={cta.icon} /> : undefined}
            haptic={cta?.haptic ?? 'heavy'}
            onPress={() => cta?.onPress()}
          >
            <Animated.View style={[styles.labelWrap, labelStyle]}>
              <AppText
                variant="button"
                color={Brand.onCherry}
                align="center"
                numberOfLines={1}
                adjustsFontSizeToFit
                minimumFontScale={0.85}
                maxFontSizeMultiplier={1.25}
              >
                {shown}
              </AppText>
            </Animated.View>
          </PressButton>
        </Animated.View>
        {cta?.secondary && (
          <PressButton
            label={cta.secondary.label}
            variant="link"
            haptic="light"
            onPress={cta.secondary.onPress}
            style={styles.secondary}
          />
        )}
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  bar: {
    paddingTop: 10,
    alignItems: 'center',
    backgroundColor: Brand.porcelain,
    borderTopWidth: StyleSheet.hairlineWidth,
    borderTopColor: Brand.hairline,
  },
  inner: { width: '100%', gap: 8 },
  context: { paddingHorizontal: 8 },
  labelWrap: { flexShrink: 1 },
  secondary: { minHeight: 44 },
});
