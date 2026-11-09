import { router, useLocalSearchParams } from 'expo-router';
import { useEffect, useState } from 'react';
import { BackHandler } from 'react-native';
import { useSharedValue } from 'react-native-reanimated';

import { MotherDetailsForm, toDetails, type DetailsDraft } from '@/components/asha/mother-details-form';
import { ConfirmSheet } from '@/components/system/confirm-sheet';
import { Screen } from '@/components/system/screen';
import { StateView } from '@/components/system/state-view';
import { Haptic } from '@/constants/theme';
import { store, useMother, type MotherRecord } from '@/data/store';
import { useT } from '@/features/onboarding/context';
import { goBack } from '@/lib/nav';

// ASHA: correct a woman's details (reached from "Her details" on her page).
// The shared details form, with ONE sticky action: "Save details". It is
// greyed until something changed, says why it cannot save (the field shows
// the error), and on success returns to her page, which confirms "Details
// saved" (they are kept on the phone: store + lib/kv). Leaving with unsaved
// changes asks first, so a half-done correction is never lost silently.

export default function EditMother() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const mother = useMother(id);
  const t = useT();
  if (!mother) {
    return (
      <Screen title="" back={{ label: t('back'), onPress: () => goBack('/asha') }}>
        <StateView title={t('notFound')} body={t('notInVillages')} action={{ label: t('back'), onPress: () => goBack('/asha') }} />
      </Screen>
    );
  }
  return <Edit key={mother.id} mother={mother} />;
}

const draftOf = (m: MotherRecord): DetailsDraft => ({ name: m.name, months: m.months, lang: m.language ?? 'kn', zoneId: m.zoneId, phone: m.phone ?? '' });

function Edit({ mother }: { mother: MotherRecord }) {
  const t = useT();
  const sheet = useSharedValue(0);
  // The form starts from her record ONCE: the record changing underneath
  // (another screen) must not wipe what the ASHA is typing.
  const [initial] = useState(() => draftOf(mother));
  const [draft, setDraft] = useState<DetailsDraft>(initial);
  const [tried, setTried] = useState(false);
  const [leaving, setLeaving] = useState(false);
  const changed = JSON.stringify(draft) !== JSON.stringify(initial);
  const details = toDetails(draft);
  const herPage = { pathname: '/asha/mother/[id]', params: { id: mother.id } } as const;

  const back = () => (changed ? setLeaving(true) : goBack(herPage));
  useEffect(() => {
    const sub = BackHandler.addEventListener('hardwareBackPress', () => {
      back();
      return true;
    });
    return () => sub.remove();
  });

  const save = () => {
    setTried(true);
    if (!details || !store.updateDetails(mother.id, details)) {
      Haptic.warning();
      return;
    }
    Haptic.success();
    router.dismissTo({ pathname: '/asha/mother/[id]', params: { id: mother.id, saved: String(Date.now()) } });
  };

  return (
    <Screen
      eyebrow={mother.name}
      title={t('detailsTitle')}
      subtitle={t('detailsSubtitle')}
      compactTitle={t('detailsTitle')}
      back={{ label: t('back'), onPress: back }}
      sheetProgress={sheet}
      cta={{
        label: t('detailsSave'),
        enabled: changed,
        icon: 'checkCircle',
        haptic: 'success',
        context: changed ? undefined : t('detailsUnchanged'),
        onPress: save,
      }}
      overlay={
        <ConfirmSheet
          open={leaving}
          progress={sheet}
          title={t('detailsLeaveQ')}
          body={t('detailsLeaveBody')}
          confirmLabel={t('detailsLeaveYes')}
          cancelLabel={t('detailsKeepEditing')}
          onConfirm={() => {
            setLeaving(false);
            goBack(herPage);
          }}
          onCancel={() => setLeaving(false)}
        />
      }
    >
      <MotherDetailsForm value={draft} onChange={setDraft} tried={tried} />
    </Screen>
  );
}
