import { useEffect } from 'react';
import { Pressable, StyleSheet, View } from 'react-native';
import Animated, { useAnimatedStyle, useReducedMotion, useSharedValue, withTiming } from 'react-native-reanimated';

import { AppIcon } from '@/components/system/app-icon';
import { Icon } from '@/components/system/icon';
import { NavButton } from '@/components/system/screen';
import { AppText } from '@/components/ui/app-text';
import { Brand } from '@/constants/brand';
import { Curves, Onboarding } from '@/constants/motion';
import { useOnboarding, useT } from '@/features/onboarding/context';
import { progressFor, STEPS, TOTAL_STEPS } from '@/features/onboarding/flow';
import { LANGUAGES } from '@/i18n/strings';

// Persistent onboarding header: back (always; from step 1 it returns to the
// welcome screen) and the language pill (from step 2) share the top row;
// "Step n of 3 • name" and the progress bar sit on their own row below, so
// the pill can never hide the step text.

export function ProgressHeader({ step, onBack }: { step: number; onBack: () => void }) {
  const t = useT();
  const { language, setSheetOpen } = useOnboarding();
  const reduceMotion = useReducedMotion();
  const fill = useSharedValue(progressFor(step));
  const native = LANGUAGES.find((l) => l.id === language)?.native ?? 'English';
  const showPill = step > 0; // on step 1 the language list itself is the switcher

  useEffect(() => {
    fill.set(withTiming(progressFor(step), { duration: reduceMotion ? 0 : Onboarding.progressMs, easing: Curves.settle }));
  }, [fill, reduceMotion, step]);

  // scaleX from the left edge (GPU), not width (re-layout every frame).
  const fillStyle = useAnimatedStyle(() => ({ transform: [{ scaleX: fill.get() }] }));

  return (
    <View style={styles.wrap}>
      <View style={styles.row}>
        <View style={styles.side}>
          <NavButton action={{ label: t('back'), onPress: onBack }} align="left" />
        </View>

        <View style={[styles.side, styles.sideEnd]}>
          {showPill && (
            <Pressable
              accessibilityRole="button"
              accessibilityLabel={`${t('changeLanguage')}: ${native}`}
              onPress={() => setSheetOpen(true)}
              style={({ pressed }) => [styles.pill, pressed && styles.pressed]}
            >
              <AppIcon name="language" size={22} />
              <AppText variant="buttonSmall" color={Brand.ink} numberOfLines={1}>
                {native}
              </AppText>
              <Icon name="down" size={14} color={Brand.inkMuted} weight="bold" />
            </Pressable>
          )}
        </View>
      </View>

      {/* Row 2: the step label and bar get their own line so the pill never covers them. */}
      <View style={styles.stepRow}>
        <AppText variant="caption" color={Brand.inkMuted} numberOfLines={1} accessibilityRole="text">
          {`${t('stepOf', { n: step + 1, total: TOTAL_STEPS })} • ${t(STEPS[step].name)}`}
        </AppText>
      </View>
      <View
        style={styles.track}
        accessibilityRole="progressbar"
        accessibilityValue={{ min: 0, max: 100, now: Math.round(progressFor(step) * 100) }}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={Math.round(progressFor(step) * 100)}
      >
        <Animated.View style={[styles.fill, fillStyle]} />
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  wrap: {
    paddingHorizontal: 24,
    gap: 8,
  },
  stepRow: {
    flexDirection: 'row',
    alignItems: 'center',
  },
  row: {
    minHeight: 48,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
  },
  side: {
    flexShrink: 1,
    flexDirection: 'row',
  },
  sideEnd: {
    justifyContent: 'flex-end',
  },
  pill: {
    minHeight: 44,
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
    paddingHorizontal: 14,
    borderRadius: 22,
    borderWidth: 1.5,
    borderColor: Brand.outline,
    backgroundColor: Brand.surface,
  },
  pressed: {
    opacity: 0.7,
  },
  track: {
    height: 6,
    borderRadius: 3,
    backgroundColor: Brand.cherryMist,
    overflow: 'hidden',
  },
  fill: {
    width: '100%',
    height: '100%',
    transformOrigin: 'left',
    borderRadius: 3,
    backgroundColor: Brand.cherry,
  },
});
