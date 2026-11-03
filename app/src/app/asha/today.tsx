import { router } from 'expo-router';
import { useState } from 'react';
import { Pressable, StyleSheet, View } from 'react-native';
import { useSharedValue } from 'react-native-reanimated';

import { goBack } from '@/lib/nav';
import { VisitProgress } from '@/components/asha/visit-progress';
import { AppIcon } from '@/components/system/app-icon';
import { Icon } from '@/components/system/icon';
import { Screen } from '@/components/system/screen';
import { StateView } from '@/components/system/state-view';
import { AppText } from '@/components/ui/app-text';
import { BottomSheet } from '@/components/ui/bottom-sheet';
import { Colors, Haptic } from '@/constants/theme';
import { ME_ASHA, network, upcomingBooking, useNetwork, zone, zoneName } from '@/data/care-network';
import { dayPlanStore, reasonFor, useDayPlan, type Reason } from '@/data/day-plan';
import { useAshaMothers, type MotherRecord } from '@/data/store';
import { useOnboarding, useT } from '@/features/onboarding/context';
import type { StringKey } from '@/i18n/strings';
import { relLabel } from '@/lib/day-label';

// ASHA: today's visits. Her own small "admin panel" for the day, kept to
// what she actually decides: WHO she visits today, in WHICH ORDER, and
// WHETHER each visit happened.
//   Overview        scheduled / visited / left + a progress bar, recomputed
//                   on every tick (VisitProgress)
//   Numbered list   1, 2, 3 ... in visiting order. Each card: the number,
//                   her name + village (› opens her page), ↑ ↓ to change
//                   the order, why she is on the list + her last check-in,
//                   "Start check-in" (Sakhi) and a Visited / Not visited
//                   CHECKBOX that says its state in words. A ticked card
//                   stays where it is (green, its number becomes a check):
//                   nothing jumps under her thumb, and unticking is the
//                   same tap. A finished Sakhi check-in ticks her by itself.
//   Add             the sticky button opens her women, village by village;
//                   one tap adds a woman to the end of today's list.
// The list starts as a suggestion (needs care first); her first change
// saves it for the day.

const REASON: Record<Reason, { key: StringKey; fg: string; bg: string }> = {
  needs_care: { key: 'statusNeedsCare', fg: Colors.cherry, bg: Colors.cherryMist },
  not_checked: { key: 'notCheckedYet', fg: Colors.plum, bg: Colors.lilacTint },
  doctor_soon: { key: 'reasonDoctorSoon', fg: Colors.sage, bg: Colors.sageTint },
  overdue: { key: 'reasonOverdue', fg: Colors.pumpkinInk, bg: Colors.orangeTint },
};

export default function TodayVisits() {
  const t = useT();
  const { language } = useOnboarding();
  const women = useAshaMothers(ME_ASHA);
  const net = useNetwork();
  const plan = useDayPlan(ME_ASHA, women);
  const sheet = useSharedValue(0);
  const [adding, setAdding] = useState(false);
  const byId = (id: string) => women.find((m) => m.id === id);
  const done = plan.order.filter((id) => plan.visited[id]);
  const notIn = women.filter((m) => !plan.order.includes(m.id));
  const villages = [...new Set(notIn.map((m) => m.zoneId))];
  const place = (m: MotherRecord) => {
    const z = zone(m.zoneId);
    return z ? zoneName(z, language) : '';
  };

  return (
    <Screen
      band
      eyebrow={t('today')}
      title={t('todayTitle')}
      compactTitle={t('todayTitle')}
      back={{ label: t('back'), onPress: () => goBack('/asha') }}
      sheetProgress={sheet}
      accessory={plan.order.length ? <VisitProgress total={plan.order.length} done={done.length} /> : undefined}
      cta={{
        label: t('todayAdd'),
        enabled: notIn.length > 0,
        icon: 'plus',
        haptic: 'medium',
        onPress: () => setAdding(true),
      }}
      overlay={
        <BottomSheet open={adding} onClose={() => setAdding(false)} progress={sheet} title={t('todayAdd')} closeLabel={t('back')}>
          <View style={styles.sheetBody}>
            {villages.map((zid) => {
              const z = zone(zid);
              return (
                <View key={zid} style={styles.group}>
                  <AppText variant="label" color={Colors.inkMuted}>
                    {z ? zoneName(z, language) : ''}
                  </AppText>
                  {notIn
                    .filter((m) => m.zoneId === zid)
                    .map((m) => (
                      <Pressable
                        key={m.id}
                        accessibilityRole="button"
                        accessibilityLabel={`${t('todayAdd')}: ${m.name}`}
                        onPress={() => {
                          Haptic.success();
                          dayPlanStore.add(ME_ASHA, women, m.id);
                          // She hears it live: "Sunita will visit you today".
                          network.tellMother(m.id, 'visit_today', ME_ASHA);
                          setAdding(false);
                        }}
                        style={({ pressed }) => [styles.pick, pressed && styles.pressed]}
                      >
                        <View style={styles.avatar}>
                          <AppText variant="heading">{m.name.slice(0, 1)}</AppText>
                        </View>
                        <AppText variant="bodyStrong" style={styles.flex} numberOfLines={1}>
                          {m.name}
                        </AppText>
                        <View style={styles.plus}>
                          <Icon name="plus" size={20} color={Colors.onCherry} weight="bold" />
                        </View>
                      </Pressable>
                    ))}
                </View>
              );
            })}
          </View>
        </BottomSheet>
      }
    >
      {plan.order.length === 0 ? (
        <StateView title={t('todayEmpty')} body={t('todayEmptyHint')} icon="calendar" />
      ) : (
        <>
          <AppText variant="caption" color={Colors.inkMuted}>
            {t('todayHint')}
          </AppText>
          <View style={styles.list}>
            {plan.order.map((id, i) => {
              const m = byId(id);
              if (!m) return null;
              const r = reasonFor(m, upcomingBooking(m.id, net));
              const tone = r ? REASON[r] : null;
              const visited = !!plan.visited[id];
              return (
                <View key={id} style={[styles.card, r === 'needs_care' && !visited && styles.cardAlert, visited && styles.cardDone]}>
                  <View style={styles.row}>
                    <View style={[styles.num, visited && styles.numDone]} accessibilityLabel={visited ? t('visitedLabel') : `${i + 1}`}>
                      {visited ? (
                        <Icon name="check" size={20} color={Colors.onCherry} weight="bold" />
                      ) : (
                        <AppText variant="heading" color={Colors.onCherry}>
                          {String(i + 1)}
                        </AppText>
                      )}
                    </View>
                    {/* Name + village, with a visible ›: it opens her page. */}
                    <Pressable
                      accessibilityRole="button"
                      accessibilityLabel={`${i + 1}. ${m.name}, ${place(m)}${tone ? `, ${t(tone.key)}` : ''}. ${t('openHerPage')}`}
                      onPress={() => router.push({ pathname: '/asha/mother/[id]', params: { id } })}
                      style={({ pressed }) => [styles.who, pressed && styles.pressed]}
                    >
                      <View style={styles.flex}>
                        <AppText variant="heading" numberOfLines={1}>
                          {m.name}
                        </AppText>
                        <AppText variant="caption" color={Colors.inkMuted} numberOfLines={1}>
                          {place(m)}
                        </AppText>
                      </View>
                      <Icon name="forward" size={20} color={Colors.cherry} weight="bold" />
                    </Pressable>
                    <View style={styles.arrows}>
                      <Arrow label={t('moveUp')} icon="up" disabled={i === 0} onPress={() => dayPlanStore.move(ME_ASHA, women, id, -1)} />
                      <Arrow label={t('moveDown')} icon="down" disabled={i === plan.order.length - 1} onPress={() => dayPlanStore.move(ME_ASHA, women, id, 1)} />
                    </View>
                  </View>
                  {/* Why she is on today's list, and when she was last checked. */}
                  <View style={styles.why}>
                    {tone && (
                      <View style={[styles.reason, { backgroundColor: tone.bg }]}>
                        <AppText variant="label" color={tone.fg} numberOfLines={1}>
                          {t(tone.key)}
                        </AppText>
                      </View>
                    )}
                    <AppText variant="caption" color={Colors.inkMuted} numberOfLines={1} style={styles.shrink}>
                      {t('lastSeenOn', { when: relLabel(m.lastCheck, language) })}
                    </AppText>
                  </View>
                  {/* What she can do for her: talk to Sakhi now, or tick the
                      visit when she saw her without a check-in. Both read
                      as actions (a verb, an empty box), never as a status. */}
                  <View style={styles.actions}>
                    <Pressable
                      accessibilityRole="button"
                      accessibilityLabel={`${t('startCheckIn')}: ${m.name}`}
                      onPress={() => {
                        Haptic.medium();
                        router.push({ pathname: '/asha/session/[id]', params: { id } });
                      }}
                      style={({ pressed }) => [styles.action, styles.actionMain, pressed && styles.pressed]}
                    >
                      <Icon name="mic" size={18} color={Colors.onCherry} weight="bold" />
                      <AppText variant="buttonSmall" color={Colors.onCherry} numberOfLines={1} style={styles.shrink}>
                        {t('startCheckIn')}
                      </AppText>
                    </Pressable>
                    <Pressable
                      accessibilityRole="checkbox"
                      accessibilityState={{ checked: visited }}
                      aria-checked={visited}
                      accessibilityLabel={`${m.name}: ${visited ? t('visitedLabel') : t('notVisitedLabel')}`}
                      onPress={() => {
                        if (visited) Haptic.light();
                        else Haptic.success();
                        dayPlanStore.toggleVisited(ME_ASHA, women, id);
                      }}
                      style={({ pressed }) => [styles.action, styles.actionTick, visited && styles.actionTicked, pressed && styles.pressed]}
                    >
                      <View style={[styles.box, visited && styles.boxOn]}>{visited && <Icon name="check" size={14} color={Colors.onCherry} weight="bold" />}</View>
                      <AppText variant="buttonSmall" color={visited ? Colors.onCherry : Colors.sage} numberOfLines={1} style={styles.shrink}>
                        {visited ? t('visitedLabel') : t('notVisitedLabel')}
                      </AppText>
                    </Pressable>
                  </View>
                </View>
              );
            })}
          </View>

        </>
      )}
      <View style={styles.tip}>
        <AppIcon name="tip" size={32} />
        <AppText variant="caption" color={Colors.ink} style={styles.flex}>
          {t('todayTip')}
        </AppText>
      </View>
    </Screen>
  );
}

function Arrow({ label, icon, disabled, onPress }: { label: string; icon: 'up' | 'down'; disabled: boolean; onPress: () => void }) {
  return (
    <Pressable
      accessibilityRole="button"
      accessibilityLabel={label}
      accessibilityState={{ disabled }}
      disabled={disabled}
      hitSlop={4}
      onPress={() => {
        Haptic.light();
        onPress();
      }}
      style={({ pressed }) => [styles.arrow, disabled && styles.off, pressed && styles.pressed]}
    >
      <View style={icon === 'up' ? styles.flip : undefined}>
        <Icon name="down" size={22} color={Colors.cherry} weight="bold" />
      </View>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  flex: { flex: 1, minWidth: 0 },
  shrink: { flexShrink: 1 },
  pressed: { opacity: 0.7 },
  off: { opacity: 0.25 },
  flip: { transform: [{ rotate: '180deg' }] },
  list: { gap: 12 },
  card: { gap: 12, padding: 14, borderRadius: 16, borderWidth: 1, borderColor: Colors.bandLine, backgroundColor: Colors.surface },
  cardAlert: { borderLeftWidth: 5, borderLeftColor: Colors.cherry },
  cardDone: { backgroundColor: Colors.sageTint, borderColor: Colors.sageTint },
  numDone: { backgroundColor: Colors.sage },
  row: { flexDirection: 'row', alignItems: 'center', gap: 10 },
  num: { width: 36, height: 36, borderRadius: 18, alignItems: 'center', justifyContent: 'center', backgroundColor: Colors.cherry },
  who: { flex: 1, minWidth: 0, minHeight: 48, flexDirection: 'row', alignItems: 'center', gap: 6 },
  arrows: { flexDirection: 'row', gap: 6 },
  arrow: { width: 44, height: 44, borderRadius: 12, alignItems: 'center', justifyContent: 'center', borderWidth: 1.5, borderColor: Colors.bandLine, backgroundColor: Colors.surface },
  why: { flexDirection: 'row', alignItems: 'center', gap: 8, flexWrap: 'wrap' },
  reason: { paddingHorizontal: 10, paddingVertical: 4, borderRadius: 8 },
  actions: { flexDirection: 'row', gap: 10 },
  action: { flex: 1, minHeight: 48, flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 8, paddingHorizontal: 10, borderRadius: 12 },
  actionMain: { backgroundColor: Colors.cherry },
  actionTick: { borderWidth: 1.5, borderColor: Colors.sage, backgroundColor: Colors.surface },
  actionTicked: { backgroundColor: Colors.sage },
  box: { width: 20, height: 20, borderRadius: 5, borderWidth: 2, borderColor: Colors.sage, alignItems: 'center', justifyContent: 'center' },
  boxOn: { borderColor: Colors.onCherry },
  tip: { flexDirection: 'row', alignItems: 'center', gap: 10, padding: 12, borderRadius: 16, backgroundColor: Colors.shell },
  sheetBody: { gap: 16, paddingBottom: 8 },
  group: { gap: 8 },
  pick: { minHeight: 60, flexDirection: 'row', alignItems: 'center', gap: 12, paddingHorizontal: 12, borderRadius: 16, backgroundColor: Colors.shell },
  avatar: { width: 40, height: 40, borderRadius: 20, alignItems: 'center', justifyContent: 'center', backgroundColor: Colors.surface },
  plus: { width: 40, height: 40, borderRadius: 20, alignItems: 'center', justifyContent: 'center', backgroundColor: Colors.cherry },
});
