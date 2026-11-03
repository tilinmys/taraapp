import { useState } from 'react';
import { Pressable, StyleSheet, View } from 'react-native';
import Animated, { useAnimatedStyle, useSharedValue, withSpring } from 'react-native-reanimated';

import { SpringCheck } from '@/components/mother/spring-check';
import { AppIcon } from '@/components/system/app-icon';
import { Burst } from '@/components/system/burst';
import { Icon } from '@/components/system/icon';
import { AppText } from '@/components/ui/app-text';
import { PressButton } from '@/components/ui/press-button';
import { Colors, Haptic, Physics } from '@/constants/theme';
import type { PlanItem } from '@/data/care-plan';
import { MEDICATIONS, REFILL_DAYS, trackerStore, useTracker } from '@/data/pregnancy';
import { useT } from '@/features/onboarding/context';
import { planLabel } from '@/lib/plan-label';

// The mother's "today" cards, shown on her Home: the daily checklist (a
// summary card that opens the checklist sheet) and today's medicines with a
// big Take button each. Plain and calm: no risk words, no red.


export function PressRow({ onPress, label, checked, children, style, outer, role = 'checkbox' }: { onPress: () => void; label: string; checked: boolean; children: React.ReactNode; style?: object; outer?: object; role?: 'checkbox' | 'button' }) {
  const s = useSharedValue(1);
  const a = useAnimatedStyle(() => ({ transform: [{ scale: s.get() }] }));
  return (
    <Animated.View style={[outer, a]}>
      <Pressable
        accessibilityRole={role}
        accessibilityState={role === 'checkbox' ? { checked } : undefined}
        aria-checked={role === 'checkbox' ? checked : undefined}
        accessibilityLabel={label}
        onPressIn={() => s.set(withSpring(Physics.pressScale, Physics.press))}
        onPressOut={() => s.set(withSpring(1, Physics.press))}
        onPress={() => {
          Haptic.light();
          onPress();
        }}
        style={style}
      >
        {children}
      </Pressable>
    </Animated.View>
  );
}

/** Today's checklist as a popup: tick freely, nothing is saved until Save. */
export function ChecklistSheet({ plan, asha, onSaved }: { plan: PlanItem[]; asha: string; onSaved: (allDone: boolean) => void }) {
  const { done } = useTracker();
  const t = useT();
  const [draft, setDraft] = useState<Record<string, boolean>>(() => Object.fromEntries(plan.map((d) => [d.id, !!done[d.id]])));
  const changed = plan.some((d) => draft[d.id] !== !!done[d.id]);
  const save = () => {
    plan.forEach((d) => {
      if (draft[d.id] !== !!done[d.id]) trackerStore.toggle(d.id);
    });
    Haptic.success();
    onSaved(plan.length > 0 && plan.every((d) => draft[d.id]));
  };
  return (
    <View style={styles.sheetBody}>
      <AppText variant="caption" color={Colors.inkMuted}>
        {t('setByAsha', { asha })}
      </AppText>
      {plan.map((d) => {
        const on = !!draft[d.id];
        return (
          <PressRow
            key={d.id}
            label={planLabel(d, t)}
            checked={on}
            onPress={() => setDraft((x) => ({ ...x, [d.id]: !x[d.id] }))}
            style={[styles.sheetRow, on && styles.checkCellOn]}
          >
            <AppIcon name={d.icon} size={34} />
            <AppText variant="bodyStrong" color={on ? Colors.inkMuted : Colors.ink} style={[styles.flex, on && styles.struck]}>
              {planLabel(d, t)}
            </AppText>
            <SpringCheck checked={on} size={26} />
          </PressRow>
        );
      })}
      <PressButton label={changed ? t('save') : t('saved')} disabled={!changed} onPress={save} />
    </View>
  );
}

/** The checklist summary: ticks so far, streak, one dot per task. Opens the sheet. */
export function ChecklistCard({ plan, onOpen, cheers }: { plan: PlanItem[]; onOpen: () => void; cheers: number }) {
  const { done, streak } = useTracker();
  const t = useT();
  const ticked = plan.filter((d) => done[d.id]).length;
  const all = ticked === plan.length && plan.length > 0;
  return (
    <PressRow
      label={`${t('todayChecklist')}. ${t('doneStreak', { a: ticked, b: plan.length, n: streak })}`}
      checked={all}
      role="button"
      onPress={onOpen}
      style={[styles.grid, styles.checklist, styles.checkCard]}
    >
      <View style={styles.iconWell}>
        <AppIcon name="checklist" size={40} />
      </View>
      <View style={styles.flex}>
        <AppText variant="heading">{t('todayChecklist')}</AppText>
        <AppText variant="caption" color={all ? Colors.sage : Colors.inkMuted}>
          {all ? t('allDoneStreak', { n: streak }) : t('doneStreak', { a: ticked, b: plan.length, n: streak })}
        </AppText>
        <View style={styles.dots}>
          {plan.map((d) => (
            <View key={d.id} style={[styles.dot, done[d.id] && styles.dotOn]} />
          ))}
        </View>
      </View>
      <Icon name="forward" size={20} color={Colors.cherry} weight="bold" />
      {cheers > 0 && <Burst key={cheers} />}
    </PressRow>
  );
}

/** Today's medicines: dose time, a big Take button (strike-through once taken), refill reminder. */
export function MedicinesCard() {
  const { taken } = useTracker();
  const t = useT();
  return (
    <View style={[styles.grid, styles.meds]}>
      <View style={styles.gridTitle}>
        <AppIcon name="medicine" size={30} />
        <AppText variant="heading" style={styles.flex}>
          {t('medicines')}
        </AppText>
        <AppText variant="caption" color={Colors.inkMuted}>
          {t('today')}
        </AppText>
      </View>
      {MEDICATIONS.map((m, i) => {
        const on = !!taken[m.id];
        const low = m.daysLeft <= REFILL_DAYS;
        return (
          <View key={m.id} style={[styles.medRow, i > 0 && styles.medDivider]}>
            <View style={styles.flex}>
              <AppText variant="bodyStrong" color={on ? Colors.inkMuted : Colors.ink} style={on && styles.struck}>
                {`${m.name} · ${m.dose}`}
              </AppText>
              <View style={styles.medMeta}>
                <Icon name="timer" size={14} color={Colors.inkMuted} />
                <AppText variant="caption" color={Colors.inkMuted}>
                  {m.time}
                </AppText>
              </View>
              {low && (
                <View style={styles.refill}>
                  <AppText variant="overline" color={Colors.pumpkinInk}>
                    {t('refillSoon', { n: m.daysLeft })}
                  </AppText>
                </View>
              )}
            </View>
            <PressRow
              label={on ? `${m.name} taken. Tap to undo.` : `Mark ${m.name} as taken`}
              checked={on}
              onPress={() => trackerStore.toggleDose(m.id)}
              style={[styles.doseBtn, on && styles.doseBtnOn]}
            >
              {on ? <Icon name="checkCircle" size={22} color={Colors.onCherry} weight="fill" /> : <AppIcon name="medicine" size={28} />}
              <AppText variant="caption" color={on ? Colors.onCherry : Colors.pumpkinInk}>
                {on ? t('taken') : t('take')}
              </AppText>
            </PressRow>
          </View>
        );
      })}
    </View>
  );
}

const styles = StyleSheet.create({
  flex: { flex: 1, minWidth: 0 },
  struck: { textDecorationLine: 'line-through' },
  grid: { gap: 12, padding: 16, borderRadius: 20 },
  gridTitle: { flexDirection: 'row', alignItems: 'center', gap: 8 },
  checklist: {
    backgroundColor: Colors.surface,
    borderWidth: 1,
    borderColor: Colors.band,
    shadowColor: Colors.ink,
    shadowOpacity: 0.04,
    shadowRadius: 12,
    shadowOffset: { width: 0, height: 4 },
    elevation: 1,
  },
  checkCellOn: { backgroundColor: Colors.orangeTint },
  meds: { backgroundColor: Colors.orangeTint, borderWidth: 1, borderColor: `${Colors.pumpkin}22` },
  medRow: { flexDirection: 'row', alignItems: 'center', gap: 12 },
  medDivider: { paddingTop: 12, borderTopWidth: 1, borderTopColor: `${Colors.pumpkin}1F` },
  medMeta: { flexDirection: 'row', alignItems: 'center', gap: 4, marginTop: 2 },
  refill: { alignSelf: 'flex-start', marginTop: 6, paddingHorizontal: 8, paddingVertical: 3, borderRadius: 8, backgroundColor: Colors.surface },
  doseBtn: {
    width: 64,
    height: 64,
    borderRadius: 18,
    alignItems: 'center',
    justifyContent: 'center',
    gap: 2,
    backgroundColor: Colors.surface,
    borderWidth: 1.5,
    borderColor: `${Colors.pumpkin}55`,
  },
  doseBtnOn: { backgroundColor: Colors.pumpkin, borderColor: Colors.pumpkin },
  checkCard: { flexDirection: 'row', alignItems: 'center', gap: 14 },
  iconWell: { width: 56, height: 56, borderRadius: 16, alignItems: 'center', justifyContent: 'center', backgroundColor: Colors.orangeTint },
  dots: { flexDirection: 'row', gap: 6, marginTop: 8 },
  dot: { width: 22, height: 6, borderRadius: 3, backgroundColor: Colors.band },
  dotOn: { backgroundColor: Colors.pumpkin },
  sheetBody: { gap: 10, paddingBottom: 8 },
  sheetRow: { minHeight: 64, flexDirection: 'row', alignItems: 'center', gap: 12, paddingHorizontal: 14, borderRadius: 16, backgroundColor: Colors.shell },
});
