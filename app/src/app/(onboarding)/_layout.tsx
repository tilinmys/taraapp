import { Slot, usePathname } from 'expo-router';
import { StatusBar } from 'expo-status-bar';
import { useEffect, useMemo } from 'react';
import { BackHandler, KeyboardAvoidingView, Platform, StyleSheet, useWindowDimensions, View } from 'react-native';
import Animated from 'react-native-reanimated';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { LanguageSheet } from '@/components/onboarding/language-sheet';
import { ProgressHeader } from '@/components/onboarding/progress-header';
import { StickyCta } from '@/components/onboarding/sticky-cta';
import { Brand } from '@/constants/brand';
import { useOnboarding, useOnboardingNav } from '@/features/onboarding/context';
import { pageAnimations, stepIndex } from '@/features/onboarding/flow';

// Onboarding shell: language -> role -> auth.
//
// The header (progress + language pill) and the sticky CTA live here, outside
// the pages, so they persist across steps: the progress bar fills, the CTA
// keeps its place and only morphs its label. Pages are keyed by route and
// slide with a horizontal parallax (see features/onboarding/flow.ts).

function Shell() {
  const pathname = usePathname();
  const insets = useSafeAreaInsets();
  const { width } = useWindowDimensions();
  const { cta, direction, sheetOpen } = useOnboarding();
  const nav = useOnboardingNav();
  const step = stepIndex(pathname);
  const { entering, exiting } = useMemo(() => pageAnimations(direction, width), [direction, width]);

  // Android hardware back steps backwards with the same reverse transition.
  useEffect(() => {
    const sub = BackHandler.addEventListener('hardwareBackPress', () => {
      if (sheetOpen) return false; // the sheet handles its own back press
      nav.back();
      return true;
    });
    return () => sub.remove();
  }, [nav, sheetOpen]);

  return (
    <View style={styles.canvas}>
      <StatusBar style="dark" />
      <KeyboardAvoidingView style={styles.flex} behavior={Platform.OS === 'ios' ? 'padding' : undefined}>
        <View style={{ paddingTop: insets.top + 4 }}>
          <ProgressHeader step={step} onBack={nav.back} />
        </View>

        <View style={styles.stage}>
          <Animated.View key={pathname} entering={entering} exiting={exiting} style={styles.page}>
            <Slot />
          </Animated.View>
        </View>

        <StickyCta cta={cta} />
      </KeyboardAvoidingView>
      <LanguageSheet />
    </View>
  );
}

// Session state comes from OnboardingProvider at the app root.
export default function OnboardingLayout() {
  return <Shell />;
}

const styles = StyleSheet.create({
  canvas: {
    flex: 1,
    backgroundColor: Brand.porcelain,
  },
  flex: {
    flex: 1,
  },
  stage: {
    flex: 1,
    overflow: 'hidden',
  },
  page: {
    ...StyleSheet.absoluteFill,
    backgroundColor: Brand.porcelain,
  },
});
