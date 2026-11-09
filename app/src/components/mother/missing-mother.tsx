import { StyleSheet, View } from 'react-native';

import { StateView } from '@/components/system/state-view';
import { Colors } from '@/constants/theme';
import { store } from '@/data/store';
import { useT } from '@/features/onboarding/context';

// Shown by a mother tab when her record is not in the store (e.g. the sample
// mothers were cleared). Never a blank screen: say so and offer the fix.
export function MissingMother() {
  const t = useT();
  return (
    <View style={styles.wrap}>
      <StateView
        title={t('missTitle')}
        body={t('missBody')}
        icon="mother"
        action={{ label: t('missAction'), onPress: () => store.resetDemo() }}
      />
    </View>
  );
}

const styles = StyleSheet.create({
  wrap: { flex: 1, justifyContent: 'center', paddingHorizontal: 24, backgroundColor: Colors.porcelain },
});
