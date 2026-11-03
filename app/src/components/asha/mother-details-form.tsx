import { useState } from 'react';
import { Pressable, StyleSheet, View } from 'react-native';

import { AppIcon } from '@/components/system/app-icon';
import { Icon } from '@/components/system/icon';
import { AppText } from '@/components/ui/app-text';
import { TextField } from '@/components/ui/text-field';
import { Colors, Haptic } from '@/constants/theme';
import { ME_ASHA, zoneName, zonesOf } from '@/data/care-network';
import { useOnboarding, useT } from '@/features/onboarding/context';
import { LANGUAGES } from '@/i18n/strings';
import { detailsProblems, type DetailsDraft } from '@/lib/mother-details';

// A pregnant woman's details, the SAME form for adding her (asha/add) and
// for correcting them later (asha/edit/[id]), built for a first-time user:
//   1 Her name               the only typing
//   2 Months pregnant        a 3 x 3 grid of big number tiles, 1 to 9
//   3 Language she speaks    Sakhi talks to HER in it
//   4 Village                big tiles of HER ASHA's villages only
//   Phone                    optional (folded on Add, open on Edit)
// The screen owns the value and the Save button; this owns the fields. The
// rules (what is required, phone format) are lib/mother-details.

export { toDetails, type DetailsDraft } from '@/lib/mother-details';

const MONTHS = [1, 2, 3, 4, 5, 6, 7, 8, 9] as const;

type Props = {
  value: DetailsDraft;
  onChange: (next: DetailsDraft) => void;
  /** Show the errors (after a Save was tried). */
  tried: boolean;
  /** Hide the village step (already known, e.g. added from a village page). */
  hideVillage?: boolean;
  /** Fold the phone under "More details" (Add); open it (Edit). */
  foldPhone?: boolean;
};

export function MotherDetailsForm({ value, onChange, tried, hideVillage, foldPhone }: Props) {
  const t = useT();
  const { language: appLanguage } = useOnboarding();
  const [more, setMore] = useState(!foldPhone || !!value.phone);
  const myZones = zonesOf(ME_ASHA);
  const problems = detailsProblems(value);
  const set = (patch: Partial<DetailsDraft>) => onChange({ ...value, ...patch });

  const phone = (
    <TextField
      label={t('addPhone')}
      prefix="+91"
      value={value.phone}
      onChangeText={(phone) => set({ phone })}
      keyboardType="phone-pad"
      autoComplete="off"
      textContentType="none"
      importantForAutofill="no"
      placeholder="98765 43210"
      maxLength={14}
      error={problems.phone && (tried || value.phone.replace(/\D/g, '').length > 10) ? t('detailsPhoneError') : undefined}
    />
  );

  return (
    <>
      {/* 1 Name */}
      <TextField
        label={t('addName')}
        value={value.name}
        onChangeText={(name) => set({ name })}
        autoCapitalize="words"
        autoComplete="name"
        textContentType="name"
        returnKeyType="done"
        maxLength={60}
        error={tried && problems.name ? t('addNameError') : undefined}
      />

      {/* 2 Months: big tiles, no typing */}
      <View style={styles.block}>
        <AppText variant="label">{t('addMonths')}</AppText>
        <View style={styles.grid} accessibilityRole="radiogroup">
          {MONTHS.map((m) => {
            const on = value.months === m;
            return (
              <Pressable
                key={m}
                accessibilityRole="radio"
                accessibilityState={{ checked: on }}
                aria-checked={on}
                accessibilityLabel={`${m} ${t('monthsUnit')}`}
                onPress={() => {
                  Haptic.light();
                  set({ months: m });
                }}
                style={({ pressed }) => [styles.tile, on && styles.tileOn, pressed && styles.pressed]}
              >
                <AppText variant="metric" color={on ? Colors.onCherry : Colors.ink}>
                  {`${m}`}
                </AppText>
                <AppText variant="caption" color={on ? Colors.onCherry : Colors.inkMuted}>
                  {t('monthsUnit')}
                </AppText>
              </Pressable>
            );
          })}
        </View>
        {tried && problems.months && (
          <AppText variant="label" color={Colors.cherry}>
            {t('addMonthsError')}
          </AppText>
        )}
      </View>

      {/* 3 The language she speaks (Sakhi's language for her) */}
      <View style={styles.block}>
        <AppText variant="label">{t('addLanguage')}</AppText>
        <View style={styles.row} accessibilityRole="radiogroup">
          {LANGUAGES.map((l) => {
            const on = value.lang === l.id;
            return (
              <Pressable
                key={l.id}
                accessibilityRole="radio"
                accessibilityState={{ checked: on }}
                aria-checked={on}
                accessibilityLabel={l.english === l.native ? l.native : `${l.native}, ${l.english}`}
                onPress={() => {
                  Haptic.light();
                  set({ lang: l.id });
                }}
                style={({ pressed }) => [styles.option, on && styles.optionOn, pressed && styles.pressed]}
              >
                <AppText variant="heading" color={on ? Colors.cherryPressed : Colors.ink} numberOfLines={1}>
                  {l.native}
                </AppText>
                {l.english !== l.native && (
                  <AppText variant="caption" color={Colors.inkMuted}>
                    {l.english}
                  </AppText>
                )}
              </Pressable>
            );
          })}
        </View>
        <View style={styles.hint}>
          <AppIcon name="voice" size={22} />
          <AppText variant="caption" color={Colors.inkMuted} style={styles.flex}>
            {t('addLanguageHint')}
          </AppText>
        </View>
      </View>

      {/* 4 Her village */}
      {!hideVillage && (
        <View style={styles.block}>
          <AppText variant="label">{t('addVillageStep')}</AppText>
          <View style={styles.row} accessibilityRole="radiogroup">
            {myZones.map((z) => {
              const on = value.zoneId === z.id;
              return (
                <Pressable
                  key={z.id}
                  accessibilityRole="radio"
                  accessibilityState={{ checked: on }}
                  aria-checked={on}
                  accessibilityLabel={zoneName(z, appLanguage)}
                  onPress={() => {
                    Haptic.light();
                    set({ zoneId: z.id });
                  }}
                  style={({ pressed }) => [styles.option, on && styles.optionOn, pressed && styles.pressed]}
                >
                  {/* Two lines, never cut: "ಅಲಿಯಾಬಾದ್" does not fit one third of a phone. */}
                  <AppText variant="bodyStrong" color={on ? Colors.cherryPressed : Colors.ink} numberOfLines={2} align="center">
                    {zoneName(z, appLanguage)}
                  </AppText>
                </Pressable>
              );
            })}
          </View>
          {tried && problems.zone && (
            <AppText variant="label" color={Colors.cherry}>
              {t('addVillageError')}
            </AppText>
          )}
        </View>
      )}

      {/* Phone: optional */}
      {foldPhone ? (
        <View style={styles.more}>
          <Pressable
            accessibilityRole="button"
            accessibilityState={{ expanded: more }}
            onPress={() => setMore((o) => !o)}
            style={({ pressed }) => [styles.moreHead, pressed && styles.pressed]}
          >
            <AppText variant="bodyStrong" style={styles.flex}>
              {t('addMore')}
            </AppText>
            <View style={more && styles.flip}>
              <Icon name="down" size={18} color={Colors.inkMuted} weight="bold" />
            </View>
          </Pressable>
          {more && <View style={styles.moreBody}>{phone}</View>}
        </View>
      ) : (
        phone
      )}
    </>
  );
}

const styles = StyleSheet.create({
  flex: { flex: 1, minWidth: 0 },
  pressed: { transform: [{ scale: 0.97 }] },
  block: { gap: 10 },
  grid: { flexDirection: 'row', flexWrap: 'wrap', gap: 10 },
  tile: {
    width: '31%',
    flexGrow: 1,
    minHeight: 72,
    alignItems: 'center',
    justifyContent: 'center',
    borderRadius: 18,
    borderWidth: 2,
    borderColor: Colors.bandLine,
    backgroundColor: Colors.surface,
  },
  tileOn: { backgroundColor: Colors.cherry, borderColor: Colors.cherry },
  row: { flexDirection: 'row', gap: 10 },
  option: {
    flex: 1,
    minHeight: 72,
    alignItems: 'center',
    justifyContent: 'center',
    paddingHorizontal: 6,
    borderRadius: 18,
    borderWidth: 2,
    borderColor: Colors.bandLine,
    backgroundColor: Colors.surface,
  },
  optionOn: { backgroundColor: Colors.cherryMist, borderColor: Colors.cherry },
  hint: { flexDirection: 'row', alignItems: 'center', gap: 8 },
  more: { borderRadius: 18, borderWidth: 1, borderColor: Colors.bandLine, overflow: 'hidden' },
  moreHead: { minHeight: 56, flexDirection: 'row', alignItems: 'center', gap: 10, paddingHorizontal: 16 },
  moreBody: { gap: 12, paddingHorizontal: 16, paddingBottom: 16 },
  flip: { transform: [{ rotate: '180deg' }] },
});
