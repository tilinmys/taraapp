import { router, useLocalSearchParams } from 'expo-router';
import { StyleSheet, View } from 'react-native';

import { CaseRow, Empty, SectionHead } from '@/components/doctor/ui';
import { Screen } from '@/components/system/screen';
import { AppText } from '@/components/ui/app-text';
import { Colors } from '@/constants/theme';
import { triageOf, useDoctorReview, type Triage } from '@/data/doctor-review';
import { useMothers } from '@/data/store';
import { goBack } from '@/lib/nav';

// All cases, in sections instead of filters: the status IS the grouping, so
// every case is one scroll away and nothing hides behind a toggle.
//   Urgent -> New reports -> Referred (follow up) -> Reviewed
// Opened from the Urgent tile, it shows only the urgent section, with a
// link to everything.

const SECTIONS: { id: Triage; title: string; empty: string }[] = [
  { id: 'urgent', title: 'Urgent', empty: 'No urgent case. Every high-risk mother is referred.' },
  { id: 'new', title: 'New reports', empty: 'Every ASHA report is reviewed.' },
  { id: 'referred', title: 'Referred · follow up', empty: 'No referral to follow up.' },
  { id: 'done', title: 'Reviewed', empty: 'Nothing reviewed yet today.' },
];

export default function AllCases() {
  const { show } = useLocalSearchParams<{ show?: string }>();
  const mothers = useMothers();
  const review = useDoctorReview();
  const only = SECTIONS.find((s) => s.id === show);
  const sections = only ? [only] : SECTIONS;
  const open = (id: string) => router.push({ pathname: '/doctor/case/[id]', params: { id } });
  const sorted = [...mothers].sort((a, b) => a.name.localeCompare(b.name));

  return (
    <Screen
      title={only ? `${only.title} cases` : 'All cases'}
      compactTitle={only ? only.title : 'All cases'}
      back={{ label: 'Home', onPress: () => goBack('/doctor') }}
    >
      <AppText variant="caption" color={Colors.inkMuted}>
        {only ? 'Only the cases that need you now.' : `${mothers.length} mothers from your ASHAs, grouped by what they need.`}
      </AppText>
      {sections.map((s) => {
        const list = sorted.filter((m) => triageOf(m, review) === s.id);
        return (
          <View key={s.id} style={styles.section}>
            <SectionHead title={s.title} count={list.length} />
            {list.length === 0 ? (
              <Empty text={s.empty} />
            ) : (
              <View style={styles.list}>
                {list.map((m) => (
                  <CaseRow key={m.id} m={m} onPress={open} />
                ))}
              </View>
            )}
          </View>
        );
      })}
      {only && (
        <SectionHead title="" link={{ label: 'See all cases', onPress: () => router.setParams({ show: undefined }) }} />
      )}
    </Screen>
  );
}

const styles = StyleSheet.create({
  section: { gap: 10 },
  list: { gap: 10 },
});
