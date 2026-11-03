import { router, useLocalSearchParams } from 'expo-router';
import { useState } from 'react';

import { MotherDetailsForm, toDetails, type DetailsDraft } from '@/components/asha/mother-details-form';
import { Screen } from '@/components/system/screen';
import { Haptic } from '@/constants/theme';
import { ME_ASHA, zone, zonesOf } from '@/data/care-network';
import { store } from '@/data/store';
import { useOnboarding, useT } from '@/features/onboarding/context';
import { goBack } from '@/lib/nav';

// ASHA: add a pregnant woman. ONE screen, built for a first-time user in a
// village (Bidar district: mothers speak Kannada or Hindi at home). The
// fields are the shared details form (components/asha/mother-details-form):
// name, months, the language she speaks, her village (skipped when opened
// from a village page), and an optional phone folded under "More details".
// One primary action, "Save and start talking", goes straight to Sakhi; a
// quiet link saves only and returns to her page (NEW). Nothing else is
// asked: age, last period and the rest come from Sakhi. Her details can be
// corrected later from her page (asha/edit/[id]).

export default function AddMother() {
  const t = useT();
  const { language: appLanguage } = useOnboarding();
  const { zone: zoneParam } = useLocalSearchParams<{ zone?: string }>();
  // Only the villages this ASHA looks after (location rule).
  const myZones = zonesOf(ME_ASHA);
  const preset = zone(zoneParam)?.ashaId === ME_ASHA ? zoneParam : myZones.length === 1 ? myZones[0].id : undefined;

  const [draft, setDraft] = useState<DetailsDraft>({ name: '', months: null, lang: appLanguage, zoneId: preset, phone: '' });
  const [tried, setTried] = useState(false);
  const details = toDetails(draft);

  const save = (start: boolean) => {
    setTried(true);
    if (!details) {
      Haptic.warning();
      return;
    }
    Haptic.success();
    const id = store.addMother(details);
    // Either way she lands where her care continues: Sakhi now, or her page
    // (with a "Next: talk with Sakhi" banner). Finishing a check-in also
    // returns to her page.
    if (start) router.replace({ pathname: '/asha/session/[id]', params: { id } });
    else router.replace({ pathname: '/asha/mother/[id]', params: { id, added: '1' } });
  };

  return (
    <Screen
      eyebrow="ASHA · Sunita"
      title={t('addTitle')}
      subtitle={t('addSubtitle')}
      compactTitle={t('addTitle')}
      back={{ label: t('back'), onPress: () => goBack('/asha') }}
      cta={{
        label: t('addSave'),
        enabled: true,
        icon: 'mic',
        haptic: 'medium',
        context: details ? `${details.name.trim()} · ${details.months} ${t('monthsUnit')}` : undefined,
        onPress: () => save(true),
        secondary: { label: t('addSaveOnly'), onPress: () => save(false) },
      }}
    >
      <MotherDetailsForm value={draft} onChange={setDraft} tried={tried} hideVillage={!!preset} foldPhone />
    </Screen>
  );
}
