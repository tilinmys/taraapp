import { BlurTargetView } from 'expo-blur';
import { useGlobalSearchParams } from 'expo-router';
import { Tabs } from 'expo-router/js-tabs';
import { useCallback, useRef, useState, type ComponentRef } from 'react';
import { StyleSheet, View } from 'react-native';

import { BabyHello } from '@/components/mother/baby-hello';
import { MotherTabBar } from '@/components/mother/tab-bar';
import { claimHello } from '@/components/mother/weekly-story-hero';
import { Colors } from '@/constants/theme';
import { useMother } from '@/data/store';
import { useOnboarding } from '@/features/onboarding/context';

// Mother workspace: four tabs with a custom bar. No navigator transition:
// animated tab scenes could detach mid-switch and leave a blank screen on
// device. The slide-in lives in <Screen inTabs> (Reanimated, always visible).
//
// On the first visit of the app session the baby says hello ON TOP of the
// tabs (components/mother/baby-hello.tsx), over a blur of her real home.
// The tabs sit in a BlurTargetView: Android's blur samples that view.
export default function MotherTabs() {
  const { story } = useGlobalSearchParams<{ story?: string }>();
  const { demoMother } = useOnboarding();
  const mother = useMother(demoMother);
  const blurTarget = useRef<ComponentRef<typeof View>>(null);
  // Decided once per app session, before the tabs render.
  const [hello, setHello] = useState(() => claimHello(story === 'skip'));
  const done = useCallback(() => setHello(false), []);

  return (
    <View style={styles.fill}>
      <BlurTargetView ref={blurTarget} style={styles.fill}>
        <Tabs
          tabBar={(props) => <MotherTabBar {...props} />}
          screenOptions={{ headerShown: false, animation: 'none', sceneStyle: { backgroundColor: Colors.porcelain } }}
        >
          <Tabs.Screen name="index" options={{ title: 'Home' }} />
          <Tabs.Screen name="pregnancy" options={{ title: 'Pregnancy' }} />
          <Tabs.Screen name="talk" options={{ title: 'Talk' }} />
          <Tabs.Screen name="history" options={{ title: 'History' }} />
        </Tabs>
      </BlurTargetView>
      {hello && <BabyHello mother={mother} blurTarget={blurTarget} onDone={done} />}
    </View>
  );
}

const styles = StyleSheet.create({
  fill: { flex: 1, backgroundColor: Colors.porcelain },
});
