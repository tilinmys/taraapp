import { router } from 'expo-router';
import { StatusBar } from 'expo-status-bar';
import { useCallback, useRef, useState } from 'react';
import {
  KeyboardAvoidingView,
  Platform,
  ScrollView,
  StyleSheet,
  useWindowDimensions,
  View,
  type TextInputInstance,
} from 'react-native';
import Animated, {
  FadeInDown,
  FadeOutUp,
  LayoutAnimationConfig,
  LinearTransition,
  useAnimatedStyle,
  useReducedMotion,
  useSharedValue,
  withDelay,
  withSpring,
  withTiming,
} from 'react-native-reanimated';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { BrandIntro } from '@/components/brand-intro';
import { AnimatedAppText, AppText } from '@/components/ui/app-text';
import { AppleMark, GoogleMark } from '@/components/ui/brand-icons';
import { PressButton } from '@/components/ui/press-button';
import { TextField } from '@/components/ui/text-field';
import { WelcomeHero } from '@/components/welcome-hero';
import { Brand, TAGLINE } from '@/constants/brand';
import { Curves, Intro, MoreSprings, Welcome } from '@/constants/motion';

// Welcome screen, optionally preceded by the brand intro (`intro`).
//
// Hierarchy: hero (floating logo over a gradient orb) -> headline + subtitle
// -> "Create Account" (filled cherry, dominant) -> "I have an account"
// (outlined) -> Apple / Google / guest, quiet at the bottom.
//
// Entrance (when the intro's bloom covers the screen, t = 0):
//   hero      0 ms    fade + scale 0.95 -> 1 (soft spring, ~600 ms)
//   text    150 ms    rise 20 pt + fade, 400 ms
//   primary 300 ms    rise 20 pt on a bouncy spring
//   second  400 ms    clean fade
//   alt     500 ms    clean fade
//
// Exits: "Create Account" pushes the registration screen (native stack:
// this screen slides out left while the form slides in from the right).
// "I have an account" morphs the buttons into the login fields in place.

const MAX_CONTENT_WIDTH = 480;
type Mode = 'welcome' | 'signin';

export function WelcomeScreen({ intro = false }: { intro?: boolean }) {
  const { width, height } = useWindowDimensions();
  const insets = useSafeAreaInsets();
  const reduceMotion = useReducedMotion();
  const shortSide = Math.min(width, height);
  const logoWidth = Math.round(Math.min(Math.max(shortSide * 0.44, 150), 220));

  const [showIntro, setShowIntro] = useState(intro);
  const [mode, setMode] = useState<Mode>('welcome');
  const [notice, setNotice] = useState<string | null>(null);
  const [phone, setPhone] = useState('');
  const [password, setPassword] = useState('');
  const [signinError, setSigninError] = useState<string | undefined>();
  const passwordRef = useRef<TextInputInstance>(null);

  // Entrance progress (0 -> 1), one per block.
  const heroIn = useSharedValue(intro ? 0 : 1);
  const heroScale = useSharedValue(intro ? Welcome.heroFrom : 1);
  const textIn = useSharedValue(intro ? 0 : 1);
  const primaryRise = useSharedValue(intro ? 1 : 0); // 1 = 20 pt below
  const primaryFade = useSharedValue(intro ? 0 : 1);
  const secondaryIn = useSharedValue(intro ? 0 : 1);
  const altIn = useSharedValue(intro ? 0 : 1);

  const enter = useCallback(() => {
    if (reduceMotion) {
      const fade = { duration: Intro.reducedFadeMs };
      [heroIn, textIn, primaryFade, secondaryIn, altIn].forEach((v) => v.set(withTiming(1, fade)));
      heroScale.set(1);
      primaryRise.set(0);
      return;
    }
    heroIn.set(withDelay(Welcome.heroDelay, withTiming(1, { duration: Welcome.heroFadeMs, easing: Curves.settle })));
    heroScale.set(withDelay(Welcome.heroDelay, withSpring(1, MoreSprings.hero)));
    textIn.set(withDelay(Welcome.textDelay, withTiming(1, { duration: Welcome.textMs, easing: Curves.settle })));
    primaryRise.set(withDelay(Welcome.primaryDelay, withSpring(0, MoreSprings.cta)));
    primaryFade.set(withDelay(Welcome.primaryDelay, withTiming(1, { duration: Welcome.primaryFadeMs })));
    secondaryIn.set(withDelay(Welcome.secondaryDelay, withTiming(1, { duration: Welcome.secondaryMs })));
    altIn.set(withDelay(Welcome.altDelay, withTiming(1, { duration: Welcome.altMs })));
  }, [altIn, heroIn, heroScale, primaryFade, primaryRise, reduceMotion, secondaryIn, textIn]);

  const introDone = useCallback(() => setShowIntro(false), []);

  const heroStyle = useAnimatedStyle(() => ({ opacity: heroIn.get(), transform: [{ scale: heroScale.get() }] }));
  const textStyle = useAnimatedStyle(() => ({
    opacity: textIn.get(),
    transform: [{ translateY: reduceMotion ? 0 : (1 - textIn.get()) * Welcome.textRise }],
  }));
  const primaryStyle = useAnimatedStyle(() => ({
    opacity: primaryFade.get(),
    transform: [{ translateY: primaryRise.get() * Welcome.primaryRise }],
  }));
  const secondaryStyle = useAnimatedStyle(() => ({ opacity: secondaryIn.get() }));
  const altStyle = useAnimatedStyle(() => ({ opacity: altIn.get() }));

  const morphIn = reduceMotion ? undefined : FadeInDown.duration(Welcome.morphInMs).delay(Welcome.morphOutMs / 2);
  const morphOut = reduceMotion ? undefined : FadeOutUp.duration(Welcome.morphOutMs);
  const morphLayout = reduceMotion ? undefined : LinearTransition.duration(Welcome.morphLayoutMs);

  const toSignin = () => {
    setNotice(null);
    setMode('signin');
  };
  const toWelcome = () => {
    setSigninError(undefined);
    setNotice(null);
    setMode('welcome');
  };
  const submitSignin = () => {
    const digits = phone.replace(/\D/g, '');
    if (digits.length !== 10) {
      setSigninError('Enter your 10-digit mobile number.');
      return;
    }
    if (!password) {
      setSigninError(undefined);
      passwordRef.current?.focus();
      return;
    }
    setSigninError(undefined);
    setNotice('Sign-in is not connected to accounts yet. This screen is ready for it.');
  };

  return (
    <View style={styles.canvas}>
      <StatusBar style="dark" />
      <KeyboardAvoidingView style={styles.flex} behavior={Platform.OS === 'ios' ? 'padding' : undefined}>
        <ScrollView
          contentContainerStyle={[
            styles.scroll,
            { paddingTop: insets.top + 16, paddingBottom: Math.max(insets.bottom, 16) + 12 },
          ]}
          keyboardShouldPersistTaps="handled"
          bounces={false}
          showsVerticalScrollIndicator={false}
        >
          <View style={styles.column}>
            <View style={styles.top}>
              <Animated.View style={heroStyle}>
                <WelcomeHero logoWidth={logoWidth} />
              </Animated.View>

              <Animated.View style={[styles.textBlock, textStyle]}>
                <LayoutAnimationConfig skipEntering>
                  <Animated.View key={mode} entering={morphIn} exiting={morphOut} style={styles.textInner}>
                    <AppText variant={mode === 'welcome' ? 'greeting' : 'display'} align="center" accessibilityRole="header">
                      {mode === 'welcome' ? 'Welcome to tara' : 'Welcome back'}
                    </AppText>
                    {mode === 'welcome' ? (
                      // Readable text: Baloo, never a script face or rose (theme.ts rules).
                      // Set as two balanced lines, one sentence each, never a lone word.
                      <AppText variant="body" color={Brand.inkMuted} align="center" accessibilityLabel={TAGLINE}>
                        {TAGLINE.replace('. ', '.\n')}
                      </AppText>
                    ) : (
                      <AppText variant="subtitle" color={Brand.inkMuted} align="center" style={styles.subtitle}>
                        Sign in with the mobile number you registered with.
                      </AppText>
                    )}
                  </Animated.View>
                </LayoutAnimationConfig>
              </Animated.View>
            </View>

            <Animated.View layout={morphLayout} style={styles.actions}>
              <LayoutAnimationConfig skipEntering>
                {mode === 'welcome' ? (
                  <Animated.View key="welcome" entering={morphIn} exiting={morphOut} style={styles.group}>
                    <Animated.View style={primaryStyle}>
                      <PressButton
                        label="Create Account"
                        accessibilityHint="Starts account setup"
                        onPress={() => router.push('/language')}
                      />
                    </Animated.View>
                    <Animated.View style={secondaryStyle}>
                      <PressButton label="I have an account" variant="secondary" onPress={toSignin} />
                    </Animated.View>

                    <Animated.View style={[styles.alt, altStyle]}>
                      <View style={styles.divider} aria-hidden>
                        <View style={styles.rule} />
                        <AppText variant="caption" color={Brand.inkMuted}>
                          or continue with
                        </AppText>
                        <View style={styles.rule} />
                      </View>
                      <View style={styles.socialRow}>
                        <PressButton
                          label="Apple"
                          variant="social"
                          icon={<AppleMark size={19} color={Brand.ink} />}
                          accessibilityHint="Sign in with Apple"
                          onPress={() => setNotice('Apple and Google sign-in arrive once accounts are connected.')}
                        />
                        <PressButton
                          label="Google"
                          variant="social"
                          icon={<GoogleMark size={19} />}
                          accessibilityHint="Sign in with Google"
                          onPress={() => setNotice('Apple and Google sign-in arrive once accounts are connected.')}
                        />
                      </View>
                      <PressButton
                        label="Continue as guest"
                        variant="link"
                        onPress={() => setNotice('Guest mode arrives with the next screens.')}
                      />
                    </Animated.View>
                  </Animated.View>
                ) : (
                  <Animated.View key="signin" entering={morphIn} exiting={morphOut} style={styles.group}>
                    <TextField
                      label="Mobile number"
                      value={phone}
                      onChangeText={setPhone}
                      keyboardType="phone-pad"
                      autoComplete="tel"
                      textContentType="telephoneNumber"
                      importantForAutofill="yes"
                      placeholder="98765 43210"
                      returnKeyType="next"
                      onSubmitEditing={() => passwordRef.current?.focus()}
                      error={signinError}
                      autoFocus
                    />
                    <TextField
                      ref={passwordRef}
                      label="Password"
                      value={password}
                      onChangeText={setPassword}
                      secureTextEntry
                      autoComplete="current-password"
                      textContentType="password"
                      returnKeyType="go"
                      onSubmitEditing={submitSignin}
                    />
                    <PressButton label="Sign In" onPress={submitSignin} />
                    <PressButton label="Back" variant="link" onPress={toWelcome} />
                  </Animated.View>
                )}
              </LayoutAnimationConfig>

              {!!notice && (
                <AnimatedAppText
                  variant="caption"
                  color={Brand.inkMuted}
                  align="center"
                  entering={reduceMotion ? undefined : FadeInDown.duration(220)}
                  accessibilityLiveRegion="polite"
                  style={styles.notice}
                >
                  {notice}
                </AnimatedAppText>
              )}
            </Animated.View>
          </View>
        </ScrollView>
      </KeyboardAvoidingView>

      {showIntro && <BrandIntro onCovered={enter} onDone={introDone} />}
    </View>
  );
}

const styles = StyleSheet.create({
  canvas: {
    flex: 1,
    backgroundColor: Brand.porcelain,
  },
  flex: {
    flex: 1,
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
    gap: 28,
  },
  top: {
    flexGrow: 1,
    alignItems: 'center',
    justifyContent: 'center',
    gap: 8,
  },
  textBlock: {
    width: '100%',
  },
  textInner: {
    alignItems: 'center',
    gap: 10,
  },
  subtitle: {
    maxWidth: 340,
  },
  actions: {
    gap: 14,
  },
  group: {
    gap: 14,
  },
  alt: {
    gap: 14,
    marginTop: 6,
  },
  divider: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 12,
  },
  rule: {
    flex: 1,
    height: StyleSheet.hairlineWidth,
    backgroundColor: Brand.outline,
  },
  socialRow: {
    flexDirection: 'row',
    gap: 12,
  },
  notice: {
    paddingHorizontal: 8,
  },
});
