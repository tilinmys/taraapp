import { useEffect, type ReactNode } from 'react';
import { BackHandler, Pressable, StyleSheet, View } from 'react-native';
import Animated, { FadeIn, FadeOut, ZoomIn, useReducedMotion } from 'react-native-reanimated';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { Icon, type IconName } from '@/components/system/icon';
import { AppText } from '@/components/ui/app-text';
import { Colors, Haptic, Layout } from '@/constants/theme';

// Long-press focus overlay (the mobile "right-click"). The held card is
// shown lifted above a dim scrim (no blur: blur costs contrast and
// performance) with its quick actions right under the thumb. Tap the scrim,
// press back, or pick an action to close.

export type FocusAction = { label: string; icon: IconName; onPress: () => void; disabled?: boolean };

type Props = { visible: boolean; onClose: () => void; card: ReactNode; actions: FocusAction[] };

export function FocusMenu({ visible, onClose, card, actions }: Props) {
  const reduceMotion = useReducedMotion();
  const insets = useSafeAreaInsets();

  useEffect(() => {
    if (!visible) return;
    const sub = BackHandler.addEventListener('hardwareBackPress', () => {
      onClose();
      return true;
    });
    return () => sub.remove();
  }, [onClose, visible]);

  if (!visible) return null;
  return (
    <View style={StyleSheet.absoluteFill} accessibilityViewIsModal>
      <Animated.View entering={FadeIn.duration(160)} exiting={FadeOut.duration(140)} style={[StyleSheet.absoluteFill, styles.scrim]}>
        <Pressable style={StyleSheet.absoluteFill} accessibilityRole="button" accessibilityLabel="Close" onPress={onClose} />
      </Animated.View>
      <View style={[styles.center, { paddingTop: insets.top + 24, paddingBottom: insets.bottom + 24 }]} pointerEvents="box-none">
        <Animated.View entering={reduceMotion ? FadeIn.duration(120) : ZoomIn.springify().damping(16).stiffness(240)} style={styles.lifted}>
          {card}
        </Animated.View>
        <Animated.View entering={FadeIn.delay(80).duration(180)} style={styles.menu}>
          {actions.map((a, i) => (
            <Pressable
              key={a.label}
              accessibilityRole="menuitem"
              accessibilityState={{ disabled: !!a.disabled }}
              disabled={a.disabled}
              onPress={() => {
                Haptic.light();
                onClose();
                a.onPress();
              }}
              style={({ pressed }) => [styles.item, i > 0 && styles.itemDivider, pressed && styles.pressed, a.disabled && styles.disabled]}
            >
              <AppText variant="buttonSmall" style={styles.flex}>
                {a.label}
              </AppText>
              <Icon name={a.icon} size={22} color={Colors.cherry} />
            </Pressable>
          ))}
        </Animated.View>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  scrim: { backgroundColor: Colors.ink, opacity: 0.45 },
  center: { flex: 1, justifyContent: 'center', paddingHorizontal: Layout.gutter, gap: 12 },
  lifted: {
    transform: [{ scale: 1.02 }],
    shadowColor: Colors.ink,
    shadowOpacity: 0.25,
    shadowRadius: 24,
    shadowOffset: { width: 0, height: 12 },
  },
  menu: { borderRadius: Layout.radius, backgroundColor: Colors.surface, overflow: 'hidden' },
  item: { minHeight: 52, flexDirection: 'row', alignItems: 'center', gap: 12, paddingHorizontal: 18 },
  itemDivider: { borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: Colors.hairline },
  pressed: { backgroundColor: Colors.cherryMist },
  disabled: { opacity: 0.4 },
  flex: { flex: 1 },
});
