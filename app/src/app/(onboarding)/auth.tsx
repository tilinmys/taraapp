import { router, useLocalSearchParams } from 'expo-router';
import { useCallback, useEffect, useRef, useState } from 'react';
import { Keyboard, Pressable, ScrollView, StyleSheet, useWindowDimensions, View, type TextInputInstance } from 'react-native';
import Animated, { useAnimatedStyle, useReducedMotion, useSharedValue, withSpring, withTiming } from 'react-native-reanimated';

import { AppText } from '@/components/ui/app-text';
import { OtpInput } from '@/components/ui/otp-input';
import { Segmented } from '@/components/ui/segmented';
import { TextField } from '@/components/ui/text-field';
import { AUTH_TEST_MODE } from '@/constants/config';
import { Colors, Haptic, Layout, Onboarding, Physics, useContentWidth } from '@/constants/theme';
import { useOnboarding, useOnboardingCta, useT, type Role } from '@/features/onboarding/context';

// Auth (Step 3 of 3 · Final Step, 75 %). Role-aware: the route carries
// ?role= and ?mode=, so the right fields show immediately.
//   ASHA     Village / PHC name + ASHA Worker ID
//   Mother   Mother's full name + months pregnant (3 / 5 / 7 / 9 Mo, one tap)
//   Doctor   Hospital / PHC name + Medical Registration No.
// Then +91 mobile -> 4-digit code. No SMS service exists yet: the demo code
// is 4210, offered as a one-tap chip, and nothing is sent anywhere. The
// single CTA lands the user in their own workspace: /asha, /mother, /doctor.

type Mode = 'create' | 'signin';
type Phase = 'phone' | 'otp';
const MONTH_PRESETS = [3, 5, 7, 9] as const;
const DEMO_CODE = '4210';

export default function AuthStep() {
  const t = useT();
  const { width } = useWindowDimensions();
  const { maxWidth } = useContentWidth();
  const reduceMotion = useReducedMotion();
  const { role: sessionRole, setRole, setProfileName } = useOnboarding();
  const params = useLocalSearchParams<{ mode?: string; role?: string }>();
  const role: Role =
    params.role === 'asha' || params.role === 'mother' || params.role === 'doctor' ? params.role : (sessionRole ?? 'asha');

  const [mode, setMode] = useState<Mode>(params.mode === 'signin' ? 'signin' : 'create');
  const [phase, setPhase] = useState<Phase>('phone');
  const [phone, setPhone] = useState('');
  const [place, setPlace] = useState(''); // village / PHC, or hospital / PHC
  const [ashaId, setAshaId] = useState('');
  const [motherName, setMotherName] = useState('');
  const [months, setMonths] = useState<number | null>(null);
  const [reg, setReg] = useState('');
  const [code, setCode] = useState('');
  const [submitted, setSubmitted] = useState(false);
  const [codeError, setCodeError] = useState<string | undefined>();
  const firstFieldRef = useRef<TextInputInstance>(null);
  const otpRef = useRef<TextInputInstance>(null);

  const create = mode === 'create';
  const digits = phone.replace(/\D/g, '');
  const errors = {
    phone: digits.length !== 10 ? t('phoneError') : undefined,
    place:
      create && role !== 'mother' && !place.trim() ? (role === 'doctor' ? t('hospitalError') : t('phcError')) : undefined,
    ashaId: create && role === 'asha' && !ashaId.trim() ? t('ashaIdError') : undefined,
    name: create && role === 'mother' && motherName.trim().length < 2 ? t('nameError') : undefined,
    months: create && role === 'mother' && !months ? t('weeksError') : undefined,
    reg: create && role === 'doctor' && !reg.trim() ? t('regError') : undefined,
  };
  // Test mode: nothing is required (see constants/config.ts).
  const valid = AUTH_TEST_MODE || Object.values(errors).every((e) => !e);
  const shown = (e?: string) => (submitted ? e : undefined);

  // Phone <-> code panels slide on the page spring.
  const p = useSharedValue(0);
  useEffect(() => {
    const to = phase === 'otp' ? 1 : 0;
    p.set(reduceMotion ? withTiming(to, { duration: 0 }) : withSpring(to, Physics.page));
    if (phase === 'otp') setTimeout(() => otpRef.current?.focus(), 350);
  }, [p, phase, reduceMotion]);
  const phoneStyle = useAnimatedStyle(() => ({
    opacity: 1 - (1 - Onboarding.recedeOpacity) * p.get(),
    transform: [
      { translateX: -width * Onboarding.parallax * p.get() },
      { scale: 1 - (1 - Onboarding.recedeScale) * p.get() },
    ],
  }));
  const otpStyle = useAnimatedStyle(() => ({ transform: [{ translateX: width * (1 - p.get()) }] }));

  const enterWorkspace = useCallback(() => {
    router.replace(role === 'asha' ? '/asha' : role === 'mother' ? '/mother' : '/doctor');
  }, [role]);

  const sendOtp = useCallback(() => {
    setSubmitted(true);
    if (!valid) {
      Haptic.warning();
      return;
    }
    setRole(role);
    if (role === 'mother' && motherName.trim()) setProfileName(motherName.trim());
    Keyboard.dismiss();
    if (AUTH_TEST_MODE) {
      Haptic.success();
      enterWorkspace();
      return;
    }
    setPhase('otp');
  }, [enterWorkspace, motherName, role, setProfileName, setRole, valid]);

  const verify = useCallback(() => {
    if (code !== DEMO_CODE) {
      Haptic.warning();
      setCodeError(t('codeMismatch', { code: DEMO_CODE }));
      return;
    }
    Haptic.success();
    enterWorkspace();
  }, [code, enterWorkspace, t]);

  const backToPhone = useCallback(() => {
    setCode('');
    setCodeError(undefined);
    setPhase('phone');
  }, []);

  useOnboardingCta(
    phase === 'phone' ? (AUTH_TEST_MODE ? t('continue') : t('sendOtp')) : t('verifyEnter'),
    phase === 'phone' ? true : code.length === 4,
    phase === 'phone' ? sendOtp : verify,
    phase === 'otp' ? { label: t('changeNumber'), onPress: backToPhone } : undefined,
  );

  const onPhone = (text: string) => {
    // Keyboard / SIM autofill often inserts "+91 98765 43210" or "098765...":
    // keep the last 10 digits (the +91 prefix is shown separately).
    const all = text.replace(/\D/g, '');
    const next = all.length > 10 ? all.slice(-10) : all;
    setPhone(next);
    if (next.length === 10 && digits.length !== 10) {
      Haptic.light();
      if (create) firstFieldRef.current?.focus();
      else Keyboard.dismiss();
    }
  };

  const column = [styles.column, { maxWidth }];

  return (
    <View style={styles.stage}>
      {/* Phase 1: details + phone */}
      <Animated.View
        style={[StyleSheet.absoluteFill, phoneStyle]}
        pointerEvents={phase === 'phone' ? 'auto' : 'none'}
        aria-hidden={phase !== 'phone'}
      >
        <ScrollView contentContainerStyle={styles.scroll} keyboardShouldPersistTaps="handled">
          <View style={column}>
            <Segmented
              options={[
                { id: 'create', label: t('modeCreate') },
                { id: 'signin', label: t('modeSignin') },
              ]}
              value={mode}
              onChange={(m) => {
                setMode(m);
                setSubmitted(false);
              }}
            />
            <View style={styles.header}>
              <AppText variant="title" accessibilityRole="header">
                {create ? t('authCreateTitle') : t('authSigninTitle')}
              </AppText>
              <AppText variant="body" color={Colors.inkMuted}>
                {t('authSubtitle')}
              </AppText>
            </View>

            {AUTH_TEST_MODE && (
              <View style={styles.testNote}>
                <AppText variant="caption" color={Colors.ink}>
                  {t('testModeNote')}
                </AppText>
              </View>
            )}

            <TextField
              label={t('mobileNumber')}
              prefix="+91"
              value={phone}
              onChangeText={onPhone}
              keyboardType="phone-pad"
              autoComplete="tel"
              textContentType="telephoneNumber"
              importantForAutofill="yes"
              placeholder="98765 43210"
              error={shown(errors.phone)}
            />

            {create && role === 'asha' && (
              <>
                <TextField ref={firstFieldRef} label={t('phcBlock')} value={place} onChangeText={setPlace} error={shown(errors.place)} />
                <TextField label={t('ashaId')} value={ashaId} onChangeText={setAshaId} autoCapitalize="characters" error={shown(errors.ashaId)} />
              </>
            )}

            {create && role === 'mother' && (
              <>
                <TextField
                  ref={firstFieldRef}
                  label={t('fullName')}
                  value={motherName}
                  onChangeText={setMotherName}
                  autoComplete="name"
                  textContentType="name"
                  autoCapitalize="words"
                  error={shown(errors.name)}
                />
                <View style={styles.field}>
                  <AppText variant="label">{t('gestWeeks')}</AppText>
                  <View style={styles.pills} accessibilityRole="radiogroup">
                    {MONTH_PRESETS.map((m) => {
                      const on = months === m;
                      return (
                        <Pressable
                          key={m}
                          accessibilityRole="radio"
                          accessibilityState={{ checked: on }}
                          aria-checked={on}
                          accessibilityLabel={t('weeksPill', { n: m })}
                          onPress={() => {
                            Haptic.light();
                            setMonths(m);
                          }}
                          style={({ pressed }) => [styles.pill, on && styles.pillOn, pressed && styles.pressed]}
                        >
                          <AppText
                            variant="buttonSmall"
                            color={on ? Colors.cherryPressed : Colors.ink}
                            numberOfLines={1}
                            adjustsFontSizeToFit
                            minimumFontScale={0.8}
                          >
                            {t('weeksPill', { n: m })}
                          </AppText>
                        </Pressable>
                      );
                    })}
                  </View>
                  {submitted && errors.months && (
                    <AppText variant="label" color={Colors.cherry}>
                      {errors.months}
                    </AppText>
                  )}
                </View>
              </>
            )}

            {create && role === 'doctor' && (
              <>
                <TextField ref={firstFieldRef} label={t('hospitalPhc')} value={place} onChangeText={setPlace} error={shown(errors.place)} />
                <TextField
                  label={t('regNumber')}
                  hint={t('regHint')}
                  value={reg}
                  onChangeText={setReg}
                  autoCapitalize="characters"
                  error={shown(errors.reg)}
                />
              </>
            )}
          </View>
        </ScrollView>
      </Animated.View>

      {/* Phase 2: 4-digit code */}
      <Animated.View
        style={[StyleSheet.absoluteFill, styles.otpPanel, otpStyle]}
        pointerEvents={phase === 'otp' ? 'auto' : 'none'}
        aria-hidden={phase !== 'otp'}
      >
        <ScrollView contentContainerStyle={styles.scroll} keyboardShouldPersistTaps="handled">
          <View style={column}>
            <View style={styles.header}>
              <AppText variant="title" accessibilityRole="header">
                {t('otpTitle')}
              </AppText>
              <AppText variant="body" color={Colors.inkMuted}>
                {t('otpSentTo', { phone: `${digits.slice(0, 5)} ${digits.slice(5)}` })}
              </AppText>
            </View>
            <OtpInput
              ref={otpRef}
              length={4}
              value={code}
              onChange={(c) => {
                setCode(c);
                setCodeError(undefined);
              }}
              label={t('otpCode')}
            />
            {!!codeError && (
              <AppText variant="label" color={Colors.cherry} accessibilityLiveRegion="polite">
                {codeError}
              </AppText>
            )}
            <View style={styles.demo}>
              <AppText variant="caption" color={Colors.ink}>
                {t('otpDemo')}
              </AppText>
              <Pressable
                accessibilityRole="button"
                accessibilityLabel={t('useDemoCode', { code: DEMO_CODE })}
                onPress={() => {
                  Haptic.light();
                  setCode(DEMO_CODE);
                  setCodeError(undefined);
                }}
                style={({ pressed }) => [styles.chip, pressed && styles.pressed]}
              >
                <AppText variant="buttonSmall" color={Colors.cherryPressed}>
                  {t('useDemoCode', { code: DEMO_CODE })}
                </AppText>
              </Pressable>
            </View>
          </View>
        </ScrollView>
      </Animated.View>
    </View>
  );
}

const styles = StyleSheet.create({
  stage: {
    flex: 1,
  },
  otpPanel: {
    backgroundColor: Colors.porcelain,
  },
  scroll: {
    flexGrow: 1,
    paddingHorizontal: Layout.gutter,
    paddingTop: 20,
    paddingBottom: 24,
    alignItems: 'center',
  },
  column: {
    width: '100%',
    gap: 20,
  },
  header: {
    gap: 8,
  },
  field: {
    gap: 12,
  },
  pills: {
    flexDirection: 'row',
    gap: 10,
  },
  pill: {
    flex: 1,
    minHeight: 52,
    paddingHorizontal: 8,
    borderRadius: 26,
    borderWidth: 2,
    borderColor: Colors.outline,
    backgroundColor: Colors.shell,
    alignItems: 'center',
    justifyContent: 'center',
  },
  pillOn: {
    backgroundColor: Colors.cherryMist,
    borderColor: Colors.cherry,
  },
  pressed: {
    transform: [{ scale: 0.97 }],
  },
  testNote: {
    paddingHorizontal: 14,
    paddingVertical: 10,
    borderRadius: 12,
    backgroundColor: Colors.cherryMist,
  },
  demo: {
    gap: 12,
    padding: 16,
    borderRadius: Layout.radius,
    backgroundColor: Colors.shell,
  },
  chip: {
    alignSelf: 'flex-start',
    minHeight: 44,
    paddingHorizontal: 16,
    borderRadius: 22,
    borderWidth: 2,
    borderColor: Colors.cherry,
    backgroundColor: Colors.cherryMist,
    justifyContent: 'center',
  },
});
