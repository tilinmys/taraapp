import { useCallback } from 'react';
import { ScrollView, StyleSheet, View } from 'react-native';
import Animated, { FadeInDown, useReducedMotion } from 'react-native-reanimated';

import { RoleCard } from '@/components/onboarding/role-card';
import type { EmblemTone } from '@/components/system/emblem';
import type { IconName } from '@/components/system/icon';
import type { ArtSlotName } from '@/constants/art-slots';
import { AppText } from '@/components/ui/app-text';
import { Colors, Curves, Layout } from '@/constants/theme';
import { useOnboarding, useOnboardingCta, useOnboardingNav, useT, type Role } from '@/features/onboarding/context';
import type { StringKey } from '@/i18n/strings';

// Screen: role (Step 2 of 3 · Workspace Setup, 33 % filled).
// Smart default: ASHA Worker is pre-selected, so the screen never opens
// blank and the button is live from the first frame ("Continue as ASHA
// Worker"). The label re-animates whenever the role changes. Below the
// button, a quiet text link goes straight to sign-in.

// `slot` is an image slot (constants/art-slots.ts): the icon shows until artwork is added.
const ROLES: { id: Role; title: StringKey; short: StringKey; body: StringKey; icon: IconName; slot: ArtSlotName; tone: EmblemTone }[] = [
  { id: 'asha', title: 'roleAshaTitle', short: 'roleAshaShort', body: 'roleAshaBody', icon: 'asha', slot: 'roleAsha', tone: 'pumpkin' },
  { id: 'mother', title: 'roleMotherTitle', short: 'roleMotherShort', body: 'roleMotherBody', icon: 'mother', slot: 'roleMother', tone: 'cherry' },
  { id: 'doctor', title: 'roleDoctorTitle', short: 'roleDoctorShort', body: 'roleDoctorBody', icon: 'doctor', slot: 'roleDoctor', tone: 'plum' },
];

export default function RoleStep() {
  const t = useT();
  const reduceMotion = useReducedMotion();
  const { role, setRole } = useOnboarding();
  const { forward } = useOnboardingNav();

  const chosen = ROLES.find((r) => r.id === role);
  const next = useCallback(() => forward(`/auth?mode=create&role=${role ?? 'asha'}`), [forward, role]);
  const signIn = useCallback(() => forward(`/auth?mode=signin&role=${role ?? 'asha'}`), [forward, role]);
  useOnboardingCta(
    chosen ? t('continueAs', { role: t(chosen.short) }) : t('chooseOption'),
    !!chosen,
    next,
    { label: t('haveAccount'), onPress: signIn },
  );

  const rise = (i: number) =>
    reduceMotion ? undefined : FadeInDown.duration(420).delay(160 + i * 70).easing(Curves.settle);

  return (
    <ScrollView contentContainerStyle={styles.scroll} showsVerticalScrollIndicator={false}>
      <View style={styles.column}>
        <View style={styles.header}>
          <AppText variant="title" accessibilityRole="header">
            {t('roleTitle')}
          </AppText>
          <AppText variant="body" color={Colors.inkMuted}>
            {t('roleSubtitle')}
          </AppText>
        </View>

        <View style={styles.cards} accessibilityRole="radiogroup">
          {ROLES.map((r, i) => (
            <Animated.View key={r.id} entering={rise(i)}>
              <RoleCard
                title={t(r.title)}
                body={t(r.body)}
                icon={r.icon}
                slot={r.slot}
                tone={r.tone}
                selected={role === r.id}
                onSelect={() => setRole(r.id)}
              />
            </Animated.View>
          ))}
        </View>
      </View>
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  scroll: {
    flexGrow: 1,
    paddingHorizontal: Layout.gutter,
    paddingTop: 20,
    paddingBottom: 24,
    alignItems: 'center',
  },
  column: {
    width: '100%',
    maxWidth: Layout.maxWidth,
    gap: 24,
  },
  header: {
    gap: 8,
  },
  cards: {
    gap: 12,
  },
});
