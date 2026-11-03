import { router } from 'expo-router';
import { StyleSheet, View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { StateView } from '@/components/system/state-view';
import { Brand } from '@/constants/brand';

// Any unknown link (old notification, typo'd deep link) lands here, never on a blank screen.
export default function NotFound() {
  const insets = useSafeAreaInsets();
  return (
    <View style={[styles.screen, { paddingTop: insets.top, paddingBottom: insets.bottom }]}>
      <StateView
        title="This page has moved"
        body="The link may be old. Your data is safe; let's take you home."
        action={{ label: 'Go to home', onPress: () => (router.canGoBack() ? router.back() : router.replace('/welcome')) }}
      />
    </View>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: Brand.porcelain, justifyContent: 'center', paddingHorizontal: 24 },
});
