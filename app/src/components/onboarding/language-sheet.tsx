import { useEffect } from 'react';
import { BackHandler, Pressable, StyleSheet, View } from 'react-native';
import Animated, {
  useAnimatedStyle,
  useReducedMotion,
  useSharedValue,
  withSpring,
  withTiming,
} from 'react-native-reanimated';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { Icon } from '@/components/system/icon';
import { AppText } from '@/components/ui/app-text';
import { Brand } from '@/constants/brand';
import { Onboarding, OnboardingSprings } from '@/constants/motion';
import { useOnboarding, useT } from '@/features/onboarding/context';
import { LANGUAGES, type Lang } from '@/i18n/strings';

// Quick language switcher: a bottom sheet over the current step. Picking a
// language updates every string in place and closes the sheet; the step,
// role and anything typed so far are kept. Always mounted: when closed it
// rests off-screen and ignores touches and screen readers.

const SHEET_OFFSET = 520; // fully below the screen edge when closed

export function LanguageSheet() {
  const { sheetOpen, setSheetOpen, language, setLanguage } = useOnboarding();
  const t = useT();
  const insets = useSafeAreaInsets();
  const reduceMotion = useReducedMotion();
  const progress = useSharedValue(0);

  useEffect(() => {
    if (sheetOpen) {
      progress.set(reduceMotion ? withTiming(1, { duration: 150 }) : withSpring(1, OnboardingSprings.sheet));
    } else {
      progress.set(withTiming(0, { duration: Onboarding.sheetBackdropMs }));
    }
  }, [progress, reduceMotion, sheetOpen]);

  useEffect(() => {
    if (!sheetOpen) return;
    const sub = BackHandler.addEventListener('hardwareBackPress', () => {
      setSheetOpen(false);
      return true;
    });
    return () => sub.remove();
  }, [setSheetOpen, sheetOpen]);

  const backdropStyle = useAnimatedStyle(() => ({ opacity: Math.min(1, progress.get()) * 0.36 }));
  const sheetStyle = useAnimatedStyle(() => ({
    transform: [{ translateY: reduceMotion ? 0 : (1 - progress.get()) * SHEET_OFFSET }],
    opacity: reduceMotion ? progress.get() : 1,
  }));

  const choose = (id: Lang) => {
    setLanguage(id);
    setSheetOpen(false);
  };

  return (
    <View
      style={StyleSheet.absoluteFill}
      pointerEvents={sheetOpen ? 'auto' : 'none'}
      accessibilityViewIsModal={sheetOpen}
      aria-hidden={!sheetOpen}
    >
      <Animated.View style={[StyleSheet.absoluteFill, styles.backdrop, backdropStyle]}>
        <Pressable
          style={StyleSheet.absoluteFill}
          accessibilityRole="button"
          accessibilityLabel={t('close')}
          onPress={() => setSheetOpen(false)}
        />
      </Animated.View>

      <Animated.View style={[styles.sheet, { paddingBottom: Math.max(insets.bottom, 16) + 8 }, sheetStyle]}>
        <View style={styles.grabber} />
        <AppText variant="heading" accessibilityRole="header">
          {t('changeLanguage')}
        </AppText>
        <View style={styles.options} accessibilityRole="radiogroup">
          {LANGUAGES.map((l) => {
            const selected = l.id === language;
            return (
              <Pressable
                key={l.id}
                accessibilityRole="radio"
                accessibilityState={{ checked: selected }}
                aria-checked={selected}
                accessibilityLabel={l.english === l.native ? l.native : `${l.native}, ${l.english}`}
                onPress={() => choose(l.id)}
                style={({ pressed }) => [styles.option, selected && styles.optionOn, pressed && styles.pressed]}
              >
                <View style={styles.optionText}>
                  <AppText variant="button">{l.native}</AppText>
                  {l.english !== l.native && (
                    <AppText variant="caption" color={Brand.inkMuted}>
                      {l.english}
                    </AppText>
                  )}
                </View>
                {selected && <Icon name="checkCircle" size={24} color={Brand.cherry} active />}
              </Pressable>
            );
          })}
        </View>
      </Animated.View>
    </View>
  );
}

const styles = StyleSheet.create({
  backdrop: {
    backgroundColor: Brand.ink,
  },
  sheet: {
    position: 'absolute',
    left: 0,
    right: 0,
    bottom: 0,
    alignSelf: 'center',
    width: '100%',
    maxWidth: 560,
    paddingHorizontal: 24,
    paddingTop: 10,
    gap: 16,
    borderTopLeftRadius: 24,
    borderTopRightRadius: 24,
    backgroundColor: Brand.porcelain,
  },
  grabber: {
    alignSelf: 'center',
    width: 40,
    height: 5,
    borderRadius: 3,
    backgroundColor: Brand.outline,
    opacity: 0.6,
  },
  options: {
    gap: 10,
  },
  option: {
    minHeight: 56,
    flexDirection: 'row',
    alignItems: 'center',
    paddingHorizontal: 18,
    paddingVertical: 10,
    borderRadius: 16,
    borderWidth: 1.5,
    borderColor: Brand.outline,
    backgroundColor: Brand.surface,
  },
  optionOn: {
    borderWidth: 2,
    borderColor: Brand.cherry,
    backgroundColor: Brand.cherryMist,
  },
  optionText: {
    flex: 1,
    gap: 2,
  },
  pressed: {
    opacity: 0.75,
  },
});
