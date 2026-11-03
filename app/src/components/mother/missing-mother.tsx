import { StyleSheet, View } from 'react-native';

import { StateView } from '@/components/system/state-view';
import { Colors } from '@/constants/theme';
import { store } from '@/data/store';

// Shown by a mother tab when her record is not in the store (e.g. the sample
// mothers were cleared). Never a blank screen: say so and offer the fix.
export function MissingMother() {
  return (
    <View style={styles.wrap}>
      <StateView
        title="We couldn’t load your details"
        body="Your records are safe. Tap below to load them again."
        icon="mother"
        action={{ label: 'Load my details', onPress: () => store.resetDemo() }}
      />
    </View>
  );
}

const styles = StyleSheet.create({
  wrap: { flex: 1, justifyContent: 'center', paddingHorizontal: 24, backgroundColor: Colors.porcelain },
});
