import { router, useLocalSearchParams } from 'expo-router';
import { useState } from 'react';
import { Pressable, StyleSheet, View } from 'react-native';
import { useSharedValue } from 'react-native-reanimated';

import { KickCounter } from '@/components/mother/kick-counter';
import { MissingMother } from '@/components/mother/missing-mother';
import { PressRow } from '@/components/mother/today-cards';
import { AppIcon } from '@/components/system/app-icon';
import { TONES } from '@/components/system/emblem';
import { Icon } from '@/components/system/icon';
import { Rise } from '@/components/system/rise';
import { Screen } from '@/components/system/screen';
import { AppText } from '@/components/ui/app-text';
import { BottomSheet } from '@/components/ui/bottom-sheet';
import { Colors } from '@/constants/theme';
import { TOPICS, trimesterOf, useTracker, weekOf, type TopicId } from '@/data/pregnancy';
import { tx } from '@/i18n/content';
import { useMother } from '@/data/store';
import { useOnboarding, useT } from '@/features/onboarding/context';
import { LANGUAGES } from '@/i18n/strings';

// Pregnancy tab: her pregnancy and what to learn. (Today's checklist and
// medicines live on Home, where she starts her day.)
//   Header       week badge, trimester + days to go, gestational progress,
//                language pill
//   My health    her checkup readings, one tap to a calm page (no risk words)
//   Explore care maternity, fertility, reproductive health
//   Dock         one primary button: "Count kicks" (opens the kick counter;
//                Home's "Kicks" tile opens it too, via ?open=kicks)

export default function Pregnancy() {
  const { demoMother, language } = useOnboarding();
  const mother = useMother(demoMother);
  const { kicks } = useTracker();
  const t = useT();
  const { open } = useLocalSearchParams<{ open?: string }>();
  const [kicking, setKicking] = useState(open === 'kicks');
  const sheet = useSharedValue(0);
  // Home's "Kicks" tile (and the baby's "Check on me") land here with
  // ?open=kicks: open the counter. Closing clears the param, so the next
  // visit with it opens the counter again.
  const [seenOpen, setSeenOpen] = useState(open);
  if (open !== seenOpen) {
    setSeenOpen(open);
    if (open === 'kicks') setKicking(true);
  }
  const closeKicks = () => {
    setKicking(false);
    if (open) router.setParams({ open: undefined });
  };
  if (!mother) return <MissingMother />;

  const week = weekOf(mother.weeksLabel);
  const daysToGo = Math.max(0, 40 - week) * 7;
  const native = LANGUAGES.find((l) => l.id === language)?.native ?? 'English';

  return (
    <Screen
      inTabs
      band
      title={t('yourPregnancy')}
      titleVariant="greeting"
      compactTitle={t('weekOf40', { n: week })}
      right={{ label: t('profile'), icon: 'profile', art: 'profile', onPress: () => router.push('/profile') }}
      sheetProgress={sheet}
      accessory={
        <View style={styles.header}>
          <View style={styles.headerRow}>
            <View style={styles.weekBadge}>
              <AppText variant="label" color={Colors.onCherry}>{t('weekN', { n: week })}</AppText>
            </View>
            <AppText variant="caption" color={Colors.inkMuted} style={styles.flex} numberOfLines={1}>
              {t('trimesterLeft', { t: trimesterOf(week), d: daysToGo })}
            </AppText>
            <Pressable
              accessibilityRole="button"
              accessibilityLabel={t('languageChange', { lang: native })}
              onPress={() => router.push('/profile')}
              style={({ pressed }) => [styles.lang, pressed && styles.pressed]}
            >
              <AppIcon name="language" size={20} />
              <AppText variant="caption" color={Colors.ink}>
                {native}
              </AppText>
            </Pressable>
          </View>
          <View
            style={styles.progress}
            accessibilityRole="progressbar"
            accessibilityLabel={t('weekOf40', { n: week })}
            aria-valuenow={Math.round((week / 40) * 100)}
          >
            <View style={[styles.progressFill, { width: `${Math.min(100, (week / 40) * 100)}%` }]} />
            {[13, 27].map((t) => (
              <View key={t} style={[styles.tick, { left: `${(t / 40) * 100}%` }]} />
            ))}
          </View>
        </View>
      }
      cta={{
        label: t('countKicks'),
        context: kicks ? t('kicksToday', { n: kicks }) : t('kicksBest'),
        enabled: true,
        icon: 'footprints',
        haptic: 'medium',
        onPress: () => setKicking(true),
      }}
      overlay={
        <BottomSheet open={kicking} onClose={closeKicks} progress={sheet} title={t('kickCounter')} closeLabel={t('close')}>
          {kicking && <KickCounter />}
        </BottomSheet>
      }
      gap={16}
    >
      {/* 1 My health: her readings, calm and plain (opens a page). */}
      <Rise index={0}>
        <PressRow
          label={`${t('myHealth')}. ${t('healthLine')}`}
          checked={false}
          role="button"
          onPress={() => router.push('/mother/health')}
          style={styles.health}
        >
          <View style={styles.healthWell}>
            <AppIcon name="scan" size={44} />
          </View>
          <View style={styles.flex}>
            <AppText variant="heading">{t('myHealth')}</AppText>
            <AppText variant="caption" color={Colors.inkMuted} numberOfLines={2}>
              {t('healthLine')}
            </AppText>
          </View>
          <Icon name="forward" size={20} color={Colors.cherry} weight="bold" />
        </PressRow>
      </Rise>

      {/* 2 Explore care: one uniform row per topic. */}
      <Rise index={1} style={styles.section}>
        <AppText variant="heading" accessibilityRole="header">
          {t('exploreCare')}
        </AppText>
        {(['maternity', 'fertility', 'reproductive'] as TopicId[]).map((id) => {
          const t = TOPICS[id];
          const tone = TONES[t.tone];
          return (
            <PressRow
              key={id}
              label={`${tx(t.title, language)}. ${tx(t.blurb, language)}`}
              checked={false}
              role="button"
              onPress={() => router.push({ pathname: '/mother/topic/[id]', params: { id } })}
              style={[styles.topic, { backgroundColor: tone.bg }]}
            >
              <View style={styles.topicWell}>
                <AppIcon name={id} size={38} />
              </View>
              <View style={styles.flex}>
                <AppText variant="bodyStrong" numberOfLines={1}>
                  {tx(t.title, language)}
                </AppText>
                <AppText variant="caption" color={Colors.inkMuted} numberOfLines={1}>
                  {tx(t.blurb, language)}
                </AppText>
              </View>
              <Icon name="forward" size={20} color={tone.fg} weight="bold" />
            </PressRow>
          );
        })}
      </Rise>
    </Screen>
  );
}
const styles = StyleSheet.create({
  flex: { flex: 1, minWidth: 0 },
  pressed: { opacity: 0.7 },
  section: { gap: 10 },
  header: { gap: 10 },
  headerRow: { flexDirection: 'row', alignItems: 'center', gap: 10 },
  weekBadge: { flexDirection: 'row', alignItems: 'center', gap: 6, paddingHorizontal: 12, paddingVertical: 6, borderRadius: 16, backgroundColor: Colors.cherry },
  lang: {
    minHeight: 36,
    flexDirection: 'row',
    alignItems: 'center',
    gap: 4,
    paddingHorizontal: 10,
    borderRadius: 18,
    borderWidth: 1,
    borderColor: Colors.bandLine,
    backgroundColor: Colors.surface,
  },
  progress: { height: 8, borderRadius: 4, backgroundColor: Colors.surface, overflow: 'hidden' },
  progressFill: { height: '100%', borderRadius: 4, backgroundColor: Colors.cherry },
  tick: { position: 'absolute', top: 0, bottom: 0, width: 2, backgroundColor: Colors.band },
  health: { flexDirection: 'row', alignItems: 'center', gap: 14, padding: 16, borderRadius: 20, borderWidth: 1, borderColor: Colors.bandLine, backgroundColor: Colors.surface },
  healthWell: { width: 60, height: 60, borderRadius: 18, alignItems: 'center', justifyContent: 'center', backgroundColor: Colors.sageTint },
  topic: { height: 76, flexDirection: 'row', alignItems: 'center', gap: 14, paddingHorizontal: 14, borderRadius: 18 },
  topicWell: { width: 52, height: 52, borderRadius: 16, alignItems: 'center', justifyContent: 'center', backgroundColor: Colors.surface },
});
