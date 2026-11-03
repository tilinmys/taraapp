import { router } from 'expo-router';
import { useState } from 'react';
import { Pressable, StyleSheet, View } from 'react-native';
import { useSharedValue } from 'react-native-reanimated';

import { ArtSlot } from '@/components/system/art-slot';
import { ConfirmSheet } from '@/components/system/confirm-sheet';
import { Icon, type IconName } from '@/components/system/icon';
import { Screen } from '@/components/system/screen';
import { AppText } from '@/components/ui/app-text';
import { Colors, Haptic, Layout } from '@/constants/theme';
import { carePlanStore } from '@/data/care-plan';
import { dayPlanStore } from '@/data/day-plan';
import { doctorReview } from '@/data/doctor-review';
import { store } from '@/data/store';
import { useOnboarding, type Role } from '@/features/onboarding/context';
import { LANGUAGES } from '@/i18n/strings';

// Profile: the same screen for every role, reached from the top-right icon
// of each workspace. Who you are, your language, demo controls (until the
// backend is connected) and sign-out, which is confirmed first.

const ROLE_INFO: Record<Role, { label: string; icon: IconName; slot: 'roleAsha' | 'roleMother' | 'roleDoctor'; who: string }> = {
  asha: { label: 'ASHA Worker', icon: 'asha', slot: 'roleAsha', who: 'Sunita' },
  mother: { label: 'Pregnant Mother', icon: 'mother', slot: 'roleMother', who: 'Lakshmi D.' },
  doctor: { label: 'Doctor', icon: 'doctor', slot: 'roleDoctor', who: 'Dr. Anand' },
};

function Row({ label, hint, icon, selected, onPress, tone }: {
  label: string;
  hint?: string;
  icon?: IconName;
  selected?: boolean;
  onPress: () => void;
  tone?: 'danger';
}) {
  return (
    <Pressable
      accessibilityRole={selected === undefined ? 'button' : 'radio'}
      accessibilityLabel={hint ? `${label}. ${hint}` : label}
      accessibilityState={selected === undefined ? undefined : { checked: selected }}
      aria-checked={selected}
      onPress={onPress}
      style={({ pressed }) => [styles.row, pressed && styles.rowPressed]}
    >
      <View style={styles.rowText}>
        <AppText variant="buttonSmall" color={tone === 'danger' ? Colors.cherry : Colors.ink}>
          {label}
        </AppText>
        {!!hint && (
          <AppText variant="caption" color={Colors.inkMuted}>
            {hint}
          </AppText>
        )}
      </View>
      {selected !== undefined ? (
        <View style={[styles.radio, selected && styles.radioOn]} aria-hidden>
          {selected && <Icon name="check" size={14} color={Colors.onCherry} weight="bold" />}
        </View>
      ) : (
        icon && <Icon name={icon} size={22} color={tone === 'danger' ? Colors.cherry : Colors.inkMuted} />
      )}
    </Pressable>
  );
}

export default function Profile() {
  const { role, language, setLanguage, profileName, demoMother, setDemoMother } = useOnboarding();
  const info = ROLE_INFO[role ?? 'asha'];
  const [confirm, setConfirm] = useState(false);
  const sheet = useSharedValue(0);
  const name = profileName || info.who;

  return (
    <Screen
      title="Profile"
      back={{ label: 'Back', onPress: () => (router.canGoBack() ? router.back() : router.replace('/welcome')) }}
      sheetProgress={sheet}
      overlay={
        <ConfirmSheet
          open={confirm}
          progress={sheet}
          title="Sign out?"
          body="You will return to the welcome screen. Demo data on this phone stays as it is."
          confirmLabel="Sign out"
          onConfirm={() => {
            setConfirm(false);
            if (router.canDismiss()) router.dismissAll();
            router.replace('/welcome');
          }}
          onCancel={() => setConfirm(false)}
        />
      }
    >
      <View style={styles.identity}>
        <ArtSlot slot={info.slot} fallback={info.icon} size={56} label={info.label} />
        <View style={styles.rowText}>
          <AppText variant="heading">{name}</AppText>
          <AppText variant="caption" color={Colors.inkMuted}>
            {`${info.label} · Hosahalli PHC`}
          </AppText>
        </View>
      </View>

      <View style={styles.section}>
        <AppText variant="overline" color={Colors.inkMuted}>
          LANGUAGE
        </AppText>
        <View style={styles.group} accessibilityRole="radiogroup">
          {LANGUAGES.map((l, i) => (
            <View key={l.id} style={i > 0 && styles.divider}>
              <Row
                label={l.native}
                hint={l.english !== l.native ? l.english : undefined}
                selected={language === l.id}
                onPress={() => {
                  Haptic.light();
                  setLanguage(l.id);
                }}
              />
            </View>
          ))}
        </View>
      </View>

      <View style={styles.section}>
        <AppText variant="overline" color={Colors.inkMuted}>
          DEMO CONTROLS
        </AppText>
        <View style={styles.group}>
          {role === 'mother' && (
            <>
              <Row
                label="Show high-risk mother"
                hint="Lakshmi D. · clinic visit needed"
                selected={demoMother === 'lakshmi'}
                onPress={() => setDemoMother('lakshmi')}
              />
              <View style={styles.divider}>
                <Row
                  label="Show normal mother"
                  hint="Kavitha R. · all good"
                  selected={demoMother === 'kavitha'}
                  onPress={() => setDemoMother('kavitha')}
                />
              </View>
            </>
          )}
          <View style={role === 'mother' ? styles.divider : undefined}>
            <Row
              label="Reset demo data"
              hint="Restores the 4 sample mothers"
              icon="reset"
              onPress={() => {
                Haptic.medium();
                store.resetDemo();
                doctorReview.reset();
                carePlanStore.reset();
                dayPlanStore.reset();
              }}
            />
          </View>
        </View>
      </View>

      <View style={styles.group}>
        <Row label="Sign out" icon="signOut" tone="danger" onPress={() => setConfirm(true)} />
      </View>
    </Screen>
  );
}

const styles = StyleSheet.create({
  identity: { flexDirection: 'row', alignItems: 'center', gap: 14, padding: 16, borderRadius: Layout.radius, backgroundColor: Colors.shell },
  section: { gap: 8 },
  group: { borderRadius: Layout.radius, backgroundColor: Colors.shell, overflow: 'hidden' },
  divider: { borderTopWidth: 1, borderTopColor: Colors.porcelain },
  row: { minHeight: 56, flexDirection: 'row', alignItems: 'center', gap: 12, paddingHorizontal: 16, paddingVertical: 10 },
  rowPressed: { backgroundColor: Colors.cherryMist },
  rowText: { flex: 1, minWidth: 0, gap: 2 },
  radio: {
    width: 24,
    height: 24,
    borderRadius: 12,
    borderWidth: 2,
    borderColor: Colors.outline,
    backgroundColor: Colors.surface,
    alignItems: 'center',
    justifyContent: 'center',
  },
  radioOn: { borderColor: Colors.cherry, backgroundColor: Colors.cherry },
});
