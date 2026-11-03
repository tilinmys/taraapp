import { useState } from 'react';
import { Pressable, ScrollView, StyleSheet, View } from 'react-native';
import Animated, { useAnimatedStyle, useSharedValue, withSpring } from 'react-native-reanimated';

import { Icon, type IconName } from '@/components/system/icon';
import { AppText } from '@/components/ui/app-text';
import { Brand } from '@/constants/brand';
import { Haptic } from '@/lib/haptics';

// Section jump bar for long records: icon + label chips; the white pill
// glides (spring) to the chosen chip and the page smooth-scrolls to that
// section. Scrolls sideways on narrow phones instead of squeezing labels,
// or, with \`fit\`, shares the width equally (labels only, no icons) so every
// section is always visible on a phone: the doctor never hunts for a tab.

export type JumpItem = { id: string; label: string; icon: IconName };

const SPRING = { damping: 25, stiffness: 250 };

export function JumpBar({ items, onJump, fit, initial }: { items: JumpItem[]; onJump: (id: string) => void; fit?: boolean; initial?: string }) {
  const [active, setActive] = useState(initial ?? items[0]?.id);
  const [frames, setFrames] = useState<Record<string, { x: number; w: number }>>({});
  const x = useSharedValue(0);
  const w = useSharedValue(0);
  const pill = useAnimatedStyle(() => ({ transform: [{ translateX: x.get() }], width: w.get(), opacity: w.get() ? 1 : 0 }));

  const select = (id: string) => {
    const f = frames[id];
    if (f) {
      x.set(withSpring(f.x, SPRING));
      w.set(withSpring(f.w, SPRING));
    }
    if (id !== active) Haptic.light();
    setActive(id);
    onJump(id);
  };

  const track = (
      <View style={styles.track} accessibilityRole="tablist">
        <Animated.View style={[styles.pill, pill]} />
        {items.map((it) => {
          const on = it.id === active;
          return (
            <Pressable
              key={it.id}
              accessibilityRole="tab"
              accessibilityState={{ selected: on }}
              aria-selected={on}
              accessibilityLabel={`Jump to ${it.label}`}
              onLayout={(e) => {
                const { x: fx, width } = e.nativeEvent.layout;
                setFrames((f) => ({ ...f, [it.id]: { x: fx, w: width } }));
                if (it.id === active && !w.get()) {
                  x.set(fx);
                  w.set(width);
                }
              }}
              onPress={() => select(it.id)}
              style={[styles.chip, fit && styles.chipFit]}
            >
              {!fit && <Icon name={it.icon} size={18} color={on ? Brand.cherry : Brand.inkMuted} tone={on ? Brand.pumpkin : undefined} />}
              <AppText variant={fit ? (items.length > 4 ? 'caption' : 'label') : 'buttonSmall'} color={on ? Brand.ink : Brand.inkMuted} numberOfLines={1} adjustsFontSizeToFit={fit} minimumFontScale={0.8}>
                {it.label}
              </AppText>
            </Pressable>
          );
        })}
      </View>
  );
  if (fit) return track;
  return (
    <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.scroll}>
      {track}
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  scroll: { flexGrow: 1 },
  track: { flexDirection: 'row', flexGrow: 1, padding: 4, borderRadius: 16, backgroundColor: 'rgba(43,27,33,0.05)' },
  pill: {
    position: 'absolute',
    top: 4,
    bottom: 4,
    left: 0,
    borderRadius: 12,
    backgroundColor: Brand.surface,
    shadowColor: Brand.ink,
    shadowOpacity: 0.08,
    shadowRadius: 6,
    shadowOffset: { width: 0, height: 2 },
    elevation: 1,
  },
  chip: { flexGrow: 1, minHeight: 40, flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 6, paddingHorizontal: 12 },
  chipFit: { flex: 1, flexBasis: 0, minWidth: 0, minHeight: 44, paddingHorizontal: 4 },
});
