import { StatusBar } from 'expo-status-bar';
import { Image, Pressable, ScrollView, StyleSheet, Text, useWindowDimensions, View } from 'react-native';
import Animated, { Easing, FadeIn, FadeInDown } from 'react-native-reanimated';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { Brand, LOGO_ASPECT, TAGLINE } from '@/constants/brand';

// First app screen after the splash. Alabaster canvas, Botanical Midnight
// type, Brand Flame primary action. Single column, capped at 560 pt so it
// reads the same on a 320 pt phone and a 12" tablet; the action sits in the
// thumb zone above the bottom safe area. Next screens attach to the button.

const MAX_CONTENT_WIDTH = 560;

// Arrival choreography after the splash flood: the logo settles first, then
// the copy and the action rise in on a short stagger. Small travel (12 pt),
// ease-out, under 700 ms in total; reduced-motion users get no movement
// (Reanimated honours the system setting by default).
const settle = Easing.out(Easing.cubic);
const rise = (order: number) =>
  FadeInDown.duration(480).delay(120 + order * 90).easing(settle).withInitialValues({ transform: [{ translateY: 12 }] });

export default function Welcome() {
  const { width, height } = useWindowDimensions();
  const insets = useSafeAreaInsets();
  const shortSide = Math.min(width, height);
  const logoWidth = Math.round(Math.min(Math.max(shortSide * 0.34, 120), 220));
  const headline = Math.round(Math.min(Math.max(shortSide * 0.075, 26), 40));

  return (
    <View style={[styles.canvas, { paddingTop: insets.top, paddingBottom: Math.max(insets.bottom, 16) }]}>
      <StatusBar style="dark" />
      <ScrollView
        contentContainerStyle={styles.scroll}
        bounces={false}
        showsVerticalScrollIndicator={false}
      >
        <View style={styles.column}>
          <View style={styles.hero}>
            <Animated.View entering={FadeIn.duration(420).easing(settle)}>
              <Image
                source={require('@/assets/images/tara-logo-mauve.png')}
                style={{ width: logoWidth, height: Math.round(logoWidth / LOGO_ASPECT) }}
                resizeMode="contain"
                accessibilityLabel="tara"
              />
            </Animated.View>
            <Animated.Text
              entering={rise(1)}
              accessibilityRole="header"
              style={[styles.headline, { fontSize: headline, lineHeight: Math.round(headline * 1.2) }]}
            >
              Care for every mother, in her own language.
            </Animated.Text>
            <Animated.Text entering={rise(2)} style={styles.body}>
              {TAGLINE}
            </Animated.Text>
          </View>

          <Animated.View entering={rise(3)} style={styles.actions}>
            <Pressable
              accessibilityRole="button"
              accessibilityLabel="Get started"
              android_ripple={{ color: 'rgba(255,255,255,0.18)' }}
              style={({ pressed }) => [styles.primary, pressed && styles.primaryPressed]}
            >
              <Text style={styles.primaryLabel}>Get started</Text>
            </Pressable>
            <Text style={styles.languages}>ಕನ್ನಡ · हिंदी · English</Text>
          </Animated.View>
        </View>
      </ScrollView>
    </View>
  );
}

const styles = StyleSheet.create({
  canvas: {
    flex: 1,
    backgroundColor: Brand.alabaster,
  },
  scroll: {
    flexGrow: 1,
    paddingHorizontal: 24,
    alignItems: 'center',
  },
  column: {
    flex: 1,
    width: '100%',
    maxWidth: MAX_CONTENT_WIDTH,
    justifyContent: 'space-between',
    paddingTop: 32,
    paddingBottom: 8,
    gap: 40,
  },
  hero: {
    flexGrow: 1,
    justifyContent: 'center',
    gap: 20,
  },
  headline: {
    color: Brand.botanicalMidnight,
    fontWeight: '700',
    letterSpacing: -0.4,
  },
  body: {
    color: Brand.ink70,
    fontSize: 17,
    lineHeight: 25,
  },
  actions: {
    gap: 14,
  },
  primary: {
    minHeight: 56, // comfortably above the 48 dp touch-target minimum
    borderRadius: 16,
    backgroundColor: Brand.brandFlame,
    alignItems: 'center',
    justifyContent: 'center',
    paddingHorizontal: 24,
    overflow: 'hidden',
  },
  primaryPressed: {
    opacity: 0.88,
  },
  primaryLabel: {
    color: '#FFFFFF',
    fontSize: 18,
    fontWeight: '600',
    letterSpacing: 0.2,
  },
  languages: {
    color: Brand.cadetSlate,
    fontSize: 15,
    textAlign: 'center',
  },
});
