import type { BottomTabBarProps } from 'expo-router/js-tabs';
import { useEffect } from 'react';
import { Pressable, StyleSheet, View } from 'react-native';
import Animated, { useAnimatedStyle, useReducedMotion, useSharedValue, withSpring } from 'react-native-reanimated';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { AppIcon, type AppIconName } from '@/components/system/app-icon';
import { AppText } from '@/components/ui/app-text';
import { Brand } from '@/constants/brand';
import { useContentWidth } from '@/hooks/use-content-width';
import { useT } from '@/features/onboarding/context';
import type { StringKey } from '@/i18n/strings';
import { Haptic } from '@/lib/haptics';

// The mother's bottom bar: four tabs (Home, Pregnancy, Talk, History). Flat white
// with one hairline, no glass. The active tab sits in a rose-tint capsule
// that springs (stiffness 250, damping 25) between tabs. Icons are the Canva
// clay set: soft (55%) when idle, full colour and lifted when active.

const TABS: Record<string, { label: StringKey; icon: AppIconName }> = {
  index: { label: 'tabHome', icon: 'home' },
  pregnancy: { label: 'tabPregnancy', icon: 'pregnancy' },
  talk: { label: 'tabTalk', icon: 'talk' },
  history: { label: 'tabHistory', icon: 'history' },
};

const SPRING = { stiffness: 250, damping: 25 };

function TabItem({ focused, label, icon, onPress }: { focused: boolean; label: string; icon: AppIconName; onPress: () => void }) {
  const reduceMotion = useReducedMotion();
  const on = useSharedValue(focused ? 1 : 0);
  useEffect(() => {
    on.set(reduceMotion ? (focused ? 1 : 0) : withSpring(focused ? 1 : 0, SPRING));
  }, [focused, on, reduceMotion]);
  const capsule = useAnimatedStyle(() => ({ opacity: on.get(), transform: [{ scaleX: 0.6 + 0.4 * on.get() }] }));
  const lift = useAnimatedStyle(() => ({ transform: [{ translateY: -2 * on.get() }] }));

  return (
    <Pressable
      accessibilityRole="tab"
      accessibilityState={{ selected: focused }}
      aria-selected={focused}
      accessibilityLabel={label}
      onPress={onPress}
      style={styles.item}
    >
      <View style={styles.iconWrap}>
        <Animated.View style={[styles.capsule, capsule]} />
        <Animated.View style={lift}>
          <AppIcon name={icon} size={28} dim={!focused} />
        </Animated.View>
      </View>
      <AppText variant="caption" color={focused ? Brand.ink : Brand.inkMuted} numberOfLines={1}>
        {label}
      </AppText>
    </Pressable>
  );
}

export function MotherTabBar({ state, emitter, navigateToTab }: BottomTabBarProps) {
  const insets = useSafeAreaInsets();
  const { maxWidth } = useContentWidth();
  const t = useT();
  return (
    <View style={[styles.bar, { paddingBottom: Math.max(insets.bottom, 10), paddingLeft: insets.left, paddingRight: insets.right }]}>
      <View style={[styles.row, { maxWidth }]} accessibilityRole="tablist">
        {state.routes.map((route, index) => {
          const tab = TABS[route.name];
          if (!tab) return null;
          const focused = state.index === index;
          return (
            <TabItem
              key={route.key}
              focused={focused}
              label={t(tab.label)}
              icon={tab.icon}
              onPress={() => {
                const event = emitter.emit({ type: 'tabPress', target: route.key, canPreventDefault: true });
                if (!focused && !event.defaultPrevented) {
                  Haptic.light();
                  navigateToTab(route.key);
                }
              }}
            />
          );
        })}
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  bar: {
    backgroundColor: Brand.surface,
    borderTopWidth: StyleSheet.hairlineWidth,
    borderTopColor: Brand.hairline,
    paddingTop: 8,
    alignItems: 'center',
  },
  row: { width: '100%', flexDirection: 'row' },
  item: { flex: 1, minHeight: 52, alignItems: 'center', justifyContent: 'center', gap: 2 },
  iconWrap: { width: 60, height: 34, alignItems: 'center', justifyContent: 'center' },
  capsule: { position: 'absolute', top: 0, right: 0, bottom: 0, left: 0, borderRadius: 16, backgroundColor: Brand.roseTint },
});
