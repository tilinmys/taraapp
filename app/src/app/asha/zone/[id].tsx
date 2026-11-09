import { router, useLocalSearchParams } from 'expo-router';
import { useState } from 'react';
import { Pressable, StyleSheet, View } from 'react-native';
import { useSharedValue } from 'react-native-reanimated';

import { goBack } from '@/lib/nav';
import { ConditionPicker } from '@/components/asha/condition-picker';
import { TellDoctorSheet } from '@/components/asha/tell-doctor';
import { Icon } from '@/components/system/icon';
import { Screen } from '@/components/system/screen';
import { StateView } from '@/components/system/state-view';
import { AppText } from '@/components/ui/app-text';
import { Colors, Haptic } from '@/constants/theme';
import { ME_ASHA, slotOf, upcomingBooking, useNetwork, zone, zoneName } from '@/data/care-network';
import { careStatus, sortByRisk, useAshaMothers, type MotherRecord } from '@/data/store';
import { useOnboarding, useT } from '@/features/onboarding/context';
import { whenLabel } from '@/lib/day-label';
import { BpStrip } from '@/components/asha/bp-recheck';

// One village (zone): its women, the ones who need care first. Each card:
//   top     initial, name, months (+ booked visit), ›  -> opens HER page
//           (Sakhi check-in, checklist, doctor visit all live there)
//   bottom  "How is she?" dropdown: Well / Needs care. Choosing Needs care
//           pops up "Tell the doctor?" (and only then).
// One sticky button: add a woman to THIS village.

export default function ZonePage() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const t = useT();
  const { language } = useOnboarding();
  const z = zone(id);
  const net = useNetwork();
  const women = sortByRisk(useAshaMothers(ME_ASHA).filter((m) => m.zoneId === id));
  const sheet = useSharedValue(0);
  const [asking, setAsking] = useState<MotherRecord | null>(null);
  const [told, setTold] = useState<string | null>(null);
  const back = { label: t('back'), onPress: () => goBack('/asha') };

  // Location rule: only her own villages open.
  if (!z || z.ashaId !== ME_ASHA) {
    return (
      <Screen title={t('zonesTitle')} back={back}>
        <StateView title="Not your village" body="You can open only the villages you look after." action={{ label: t('back'), onPress: back.onPress }} />
      </Screen>
    );
  }

  const line = (m: MotherRecord) => {
    const b = upcomingBooking(m.id, net);
    const s = b && slotOf(b);
    const months = t('monthsShort', { n: m.months });
    return s ? `${months} · ${whenLabel(s.start, language)}` : months;
  };
  const care = women.filter((m) => careStatus(m) === 'alert').length;

  return (
    <Screen
      band
      eyebrow={t('zonesTitle')}
      title={zoneName(z, language)}
      compactTitle={zoneName(z, language)}
      back={back}
      sheetProgress={sheet}
      accessory={
        <AppText variant="bodyStrong" color={care ? Colors.cherry : Colors.sage}>
          {`${t('womenCount', { n: women.length })} · ${care ? t('needCare', { n: care }) : t('allWell')}`}
        </AppText>
      }
      cta={{
        label: t('addMother'),
        enabled: true,
        icon: 'plus',
        haptic: 'medium',
        onPress: () => router.push({ pathname: '/asha/add', params: { zone: z.id } }),
      }}
      overlay={
        <TellDoctorSheet
          mother={asking}
          progress={sheet}
          onClose={(names) => {
            if (names && asking) setTold(`${asking.name}: ${t('doctorTold', { names: names.join(', ') })}`);
            setAsking(null);
          }}
        />
      }
    >
      {!!told && (
        <View style={styles.told} accessibilityLiveRegion="polite">
          <Icon name="checkCircle" size={20} color={Colors.sage} weight="fill" />
          <AppText variant="bodyStrong" color={Colors.sage} style={styles.flex}>
            {told}
          </AppText>
        </View>
      )}
      {women.length === 0 ? (
        <StateView title={t('zoneEmpty')} body={t('addMotherHint')} icon="mother" />
      ) : (
        <>
          <AppText variant="caption" color={Colors.inkMuted}>
            {t('tapWoman')}
          </AppText>
          <View style={styles.list}>
            {women.map((m) => {
              const alert = careStatus(m) === 'alert';
              return (
                <View key={m.id} style={[styles.card, alert && styles.cardAlert]}>
                  {/* High or low BP only: the reading, and the 6-hour recheck top-right. */}
                  <BpStrip mother={m} inset={14} radius={alert ? 18 : 19} />
                  <Pressable
                    accessibilityRole="button"
                    accessibilityLabel={`${m.name}. ${line(m)}`}
                    onPress={() => {
                      Haptic.light();
                      router.push({ pathname: '/asha/mother/[id]', params: { id: m.id } });
                    }}
                    style={({ pressed }) => [styles.top, pressed && styles.pressed]}
                  >
                    <View style={[styles.avatar, alert && styles.avatarAlert]}>
                      <AppText variant="heading" color={alert ? Colors.cherry : Colors.ink}>
                        {m.name.slice(0, 1)}
                      </AppText>
                    </View>
                    <View style={styles.flex}>
                      <AppText variant="heading" numberOfLines={1}>
                        {m.name}
                      </AppText>
                      <AppText variant="caption" color={Colors.inkMuted} numberOfLines={1}>
                        {line(m)}
                      </AppText>
                    </View>
                    <Icon name="forward" size={22} color={Colors.cherry} weight="bold" />
                  </Pressable>
                  <View style={styles.divider} />
                  <ConditionPicker mother={m} onNeedsCare={(x) => setAsking(x)} />
                </View>
              );
            })}
          </View>
        </>
      )}
    </Screen>
  );
}

const styles = StyleSheet.create({
  flex: { flex: 1, minWidth: 0 },
  pressed: { opacity: 0.7 },
  list: { gap: 12 },
  told: { flexDirection: 'row', alignItems: 'center', gap: 8, padding: 12, borderRadius: 14, backgroundColor: Colors.sageTint },
  card: { gap: 10, padding: 14, borderRadius: 20, borderWidth: 1, borderColor: Colors.bandLine, backgroundColor: Colors.surface },
  cardAlert: { borderWidth: 2, borderColor: Colors.cherry },
  top: { flexDirection: 'row', alignItems: 'center', gap: 12 },
  avatar: { width: 48, height: 48, borderRadius: 24, alignItems: 'center', justifyContent: 'center', backgroundColor: Colors.shell },
  avatarAlert: { backgroundColor: Colors.cherryMist },
  divider: { height: 1, backgroundColor: Colors.hairline },
});
