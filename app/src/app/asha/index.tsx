import { router } from 'expo-router';
import { Pressable, StyleSheet, View } from 'react-native';

import { VisitProgress } from '@/components/asha/visit-progress';
import { AppIcon } from '@/components/system/app-icon';
import { Icon } from '@/components/system/icon';
import { NavCard } from '@/components/system/nav-card';
import { Screen } from '@/components/system/screen';
import { StateView } from '@/components/system/state-view';
import { AppText } from '@/components/ui/app-text';
import { Colors, Haptic } from '@/constants/theme';
import { ME_ASHA, inbox, upcomingBooking, useNetwork, useNow, zoneName, zonesOf, type Zone } from '@/data/care-network';
import { useDayPlan } from '@/data/day-plan';
import { careStatus, sortByRisk, store, useAshaMothers, type MotherRecord } from '@/data/store';
import { useOnboarding, useT } from '@/features/onboarding/context';

// ASHA home. ONE path, no repeats:
//   Your day     today's visits as an OVERVIEW: scheduled / visited / left
//                and a progress bar (no single woman singled out); tap opens
//                Today's visits, where she ticks each visit and sets the order
//   Villages     one tile per village -> its women -> one woman's page,
//                where everything for her happens (Sakhi check-in, how she
//                is, doctor visit, daily checklist)
//   Add woman    the only "new" action
//   Work lists   Sakhi Direct (the WebSocket voice check-in) · Doctor visits
//                · Remind visits (same card each).
//                Notifications live ONLY on the bell in the header (with
//                its unread badge): a second card for them was a duplicate.
//   How it works three numbered steps, for the first days
// No mothers list, Sakhi card or sticky talk button here: a check-in always
// starts from the woman it is about.

/** A village tile: the place, how many women, ONE status, and ›. */
function ZoneTile({ z, women }: { z: Zone; women: MotherRecord[] }) {
  const t = useT();
  const { language } = useOnboarding();
  const care = women.filter((m) => careStatus(m) === 'alert').length;
  const name = zoneName(z, language);
  const status = care ? t('needCare', { n: care }) : t('allWell');
  return (
    <Pressable
      accessibilityRole="button"
      accessibilityLabel={`${name}. ${t('womenCount', { n: women.length })}. ${status}`}
      onPress={() => {
        Haptic.light();
        router.push({ pathname: '/asha/zone/[id]', params: { id: z.id } });
      }}
      style={({ pressed }) => [styles.zone, care ? styles.zoneAlert : null, pressed && styles.pressed]}
    >
      <View style={styles.zoneTop}>
        <View style={styles.zoneWell}>
          <AppIcon name="home" size={26} />
        </View>
        <Icon name="forward" size={22} color={Colors.cherry} weight="bold" />
      </View>
      <AppText variant="heading" numberOfLines={1}>
        {name}
      </AppText>
      <AppText variant="caption" color={Colors.inkMuted}>
        {t('womenCount', { n: women.length })}
      </AppText>
      <View style={[styles.zonePill, care ? styles.zonePillAlert : styles.zonePillOk]}>
        <View style={[styles.dot, { backgroundColor: care ? Colors.cherry : Colors.sage }]} />
        <AppText variant="label" color={care ? Colors.cherry : Colors.sage} numberOfLines={2} style={styles.flex}>
          {status}
        </AppText>
      </View>
    </Pressable>
  );
}

export default function AshaHome() {
  const t = useT();
  // Location rule: only the women of Sunita's own villages.
  const all = useAshaMothers(ME_ASHA);
  const mothers = sortByRisk(all);
  const net = useNetwork();
  const now = useNow();
  const myZones = zonesOf(ME_ASHA);
  const openThisWeek = net.slots.filter((x) => x.status === 'open' && x.start > now && x.start < now + 7 * 86_400_000).length;
  const visitsSoon = all.filter((m) => {
    const b = upcomingBooking(m.id, net);
    const sl = b && net.slots.find((x) => x.id === b.slotId);
    return !!sl && sl.start < now + 2 * 86_400_000;
  }).length;
  const unread = inbox(net, { kind: 'staff', id: ME_ASHA }).filter((n) => !n.read).length;
  const plan = useDayPlan(ME_ASHA, all);
  const done = plan.order.filter((id) => plan.visited[id]).length;
  const total = plan.order.length;

  return (
    <Screen
      band
      eyebrow={t('ashaWorkspace')}
      title={t('hello', { name: 'Sunita' })}
      compactTitle={t('hello', { name: 'Sunita' })}
      right={{ label: t('profile'), icon: 'profile', art: 'ashaWorker', onPress: () => router.push('/profile') }}
      rightExtra={{ label: t('notifications'), icon: 'bell', badge: unread, onPress: () => router.push('/asha/messages') }}
      accessory={
        // Your day: the counts at a glance. Tap opens the list to tick visits.
        <Pressable
          accessibilityRole="button"
          accessibilityLabel={`${t('todayTitle')}. ${total ? t('todayProgress', { done, total }) : t('todayNone')}`}
          onPress={() => router.push('/asha/today')}
          style={({ pressed }) => [styles.today, pressed && styles.pressed]}
        >
          <View style={styles.todayHead}>
            <AppIcon name="ashaWorker" size={40} />
            <AppText variant="heading" style={styles.flex}>
              {t('todayTitle')}
            </AppText>
            <Icon name="forward" size={22} color={Colors.cherry} weight="bold" />
          </View>
          {total ? (
            <VisitProgress total={total} done={done} />
          ) : (
            <AppText variant="body" color={Colors.inkMuted}>
              {t('todayNone')}
            </AppText>
          )}
        </Pressable>
      }
    >
      {mothers.length === 0 && (
        <StateView
          title={t('noWomen')}
          body={t('noWomenHint')}
          icon="mother"
          action={{ label: t('restoreSample'), onPress: () => store.resetDemo() }}
        />
      )}

      {/* Villages: the way in to every woman. */}
      <View style={styles.sectionHead}>
        <AppText variant="heading" accessibilityRole="header">
          {t('zonesTitle')}
        </AppText>
        <AppText variant="caption" color={Colors.inkMuted}>
          {t('zonesHint')}
        </AppText>
      </View>
      <View style={styles.zones}>
        {myZones.map((z) => (
          <ZoneTile key={z.id} z={z} women={all.filter((m) => m.zoneId === z.id)} />
        ))}
      </View>

      {/* Add a pregnant woman: big, impossible to miss. */}
      <Pressable
        accessibilityRole="button"
        accessibilityLabel={`${t('addMother')}. ${t('addMotherHint')}`}
        onPress={() => {
          Haptic.medium();
          router.push('/asha/add');
        }}
        style={({ pressed }) => [styles.addCard, pressed && styles.pressed]}
      >
        <View style={styles.addWell}>
          <AppIcon name="mother" size={40} />
        </View>
        <View style={styles.flex}>
          <AppText variant="heading" color={Colors.cherry}>
            {t('addMother')}
          </AppText>
          <AppText variant="caption" color={Colors.inkMuted}>
            {t('addMotherHint')}
          </AppText>
        </View>
        <View style={styles.addPlus}>
          <Icon name="plus" size={22} color={Colors.onCherry} weight="bold" />
        </View>
      </Pressable>

      {/* Work lists: same card, same arrow. */}
      <View style={styles.list}>
        <NavCard icon="voice" title={t('directTitle')} line={t('directHint')} onPress={() => router.push('/asha/direct')} />
        <NavCard icon="calendar" title={t('visitsTitle')} line={t('visitsHint', { n: openThisWeek })} onPress={() => router.push('/asha/visits')} />
        <NavCard
          icon="voice"
          title={t('remindersTitle')}
          line={t('remindersHint', { n: visitsSoon })}
          status={visitsSoon ? { label: `${visitsSoon}`, tone: 'info' } : undefined}
          onPress={() => router.push('/asha/reminders')}
        />
      </View>

      {/* How it works: the whole job in three steps. */}
      <View style={styles.steps}>
        <AppText variant="label" color={Colors.inkMuted}>
          {t('stepsTitle')}
        </AppText>
        {[t('step1'), t('step2'), t('step3')].map((s, i) => (
          <View key={s} style={styles.step}>
            <View style={styles.stepNum}>
              <AppText variant="label" color={Colors.onCherry}>
                {String(i + 1)}
              </AppText>
            </View>
            <AppText variant="body" style={styles.flex}>
              {s}
            </AppText>
          </View>
        ))}
      </View>
    </Screen>
  );
}

const styles = StyleSheet.create({
  flex: { flex: 1, minWidth: 0 },
  pressed: { opacity: 0.7 },
  today: { gap: 12, padding: 14, borderRadius: 20, backgroundColor: Colors.surface, borderWidth: 1, borderColor: Colors.bandLine },
  todayHead: { flexDirection: 'row', alignItems: 'center', gap: 10 },
  // Title above its hint: side by side, long Kannada hints squeezed the title.
  sectionHead: { gap: 2 },
  list: { gap: 10 },
  zones: { flexDirection: 'row', flexWrap: 'wrap', gap: 10 },
  zone: {
    width: '47%',
    flexGrow: 1,
    gap: 4,
    padding: 11,
    borderRadius: 20,
    borderWidth: 1,
    borderColor: Colors.bandLine,
    backgroundColor: Colors.surface,
  },
  zoneAlert: { borderWidth: 2, borderColor: Colors.cherry },
  zoneTop: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', marginBottom: 2 },
  zoneWell: { width: 38, height: 38, borderRadius: 12, alignItems: 'center', justifyContent: 'center', backgroundColor: Colors.shell },
  zonePill: { flexDirection: 'row', alignItems: 'center', gap: 6, alignSelf: 'stretch', paddingHorizontal: 10, paddingVertical: 6, borderRadius: 12 },
  zonePillAlert: { backgroundColor: Colors.cherryMist },
  zonePillOk: { backgroundColor: Colors.sageTint },
  dot: { width: 8, height: 8, borderRadius: 4 },
  addCard: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 14,
    padding: 14,
    borderRadius: 20,
    borderWidth: 2,
    borderStyle: 'dashed',
    borderColor: Colors.cherry,
    backgroundColor: Colors.cherryMist,
  },
  addWell: { width: 56, height: 56, borderRadius: 18, alignItems: 'center', justifyContent: 'center', backgroundColor: Colors.surface },
  addPlus: { width: 44, height: 44, borderRadius: 22, alignItems: 'center', justifyContent: 'center', backgroundColor: Colors.cherry },
  steps: { gap: 10, padding: 14, borderRadius: 20, backgroundColor: Colors.shell },
  step: { flexDirection: 'row', alignItems: 'center', gap: 12 },
  stepNum: { width: 28, height: 28, borderRadius: 14, alignItems: 'center', justifyContent: 'center', backgroundColor: Colors.cherry },
});
