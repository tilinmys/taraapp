import { useEffect, useState } from 'react';
import { Pressable, StyleSheet, View } from 'react-native';
import Animated, {
  FadeIn,
  FadeOut,
  useAnimatedStyle,
  useReducedMotion,
  useSharedValue,
  withSpring,
} from 'react-native-reanimated';

import { Emblem, type EmblemTone } from '@/components/system/emblem';
import { Icon, type IconName } from '@/components/system/icon';
import { AppText } from '@/components/ui/app-text';
import { Brand } from '@/constants/brand';
import { Haptic } from '@/lib/haptics';

// Filter dropdown: a pill that says what is showing ("History ▾"). Tapping
// it drops a small menu (spring, chevron flips) with each option's emblem,
// one-line description and count; the current one is ticked. Tapping
// anywhere else closes it.

export type FilterOption<T extends string> = {
  id: T;
  label: string;
  hint: string;
  icon: IconName;
  tone: EmblemTone;
  count: number;
};

const SPRING = { stiffness: 250, damping: 25 };

export function FilterDropdown<T extends string>({
  label,
  options,
  value,
  onChange,
}: {
  label: string;
  options: FilterOption<T>[];
  value: T;
  onChange: (v: T) => void;
}) {
  const [open, setOpen] = useState(false);
  const reduceMotion = useReducedMotion();
  const flip = useSharedValue(0);
  const current = options.find((o) => o.id === value) ?? options[0];
  useEffect(() => {
    flip.set(reduceMotion ? (open ? 1 : 0) : withSpring(open ? 1 : 0, SPRING));
  }, [flip, open, reduceMotion]);
  const chevron = useAnimatedStyle(() => ({ transform: [{ rotate: `${flip.get() * 180}deg` }] }));

  return (
    <View style={styles.wrap}>
      <Pressable
        accessibilityRole="button"
        accessibilityLabel={`${label}: ${current.label}`}
        accessibilityHint="Opens the filter"
        accessibilityState={{ expanded: open }}
        onPress={() => {
          Haptic.light();
          setOpen((o) => !o);
        }}
        style={({ pressed }) => [styles.pill, open && styles.pillOpen, pressed && styles.pressed]}
      >
        <Icon name="settings" size={16} color={Brand.inkMuted} />
        <AppText variant="caption" color={Brand.inkMuted}>
          {label}
        </AppText>
        <AppText variant="buttonSmall" color={Brand.ink}>
          {current.label}
        </AppText>
        <Animated.View style={chevron}>
          <Icon name="down" size={16} color={Brand.cherry} weight="bold" />
        </Animated.View>
      </Pressable>

      {open && (
        <>
          {/* Tap outside to close. */}
          <Pressable accessibilityLabel="Close filter" style={styles.scrim} onPress={() => setOpen(false)} />
          <Animated.View
            entering={reduceMotion ? undefined : FadeIn.duration(160)}
            exiting={reduceMotion ? undefined : FadeOut.duration(120)}
            style={styles.menu}
            accessibilityRole="menu"
          >
            {options.map((o, i) => {
              const on = o.id === value;
              return (
                <Pressable
                  key={o.id}
                  accessibilityRole="menuitem"
                  accessibilityState={{ selected: on }}
                  accessibilityLabel={`${o.label}, ${o.count}. ${o.hint}`}
                  onPress={() => {
                    if (!on) Haptic.medium();
                    onChange(o.id);
                    setOpen(false);
                  }}
                  style={({ pressed }) => [styles.option, i > 0 && styles.divider, (on || pressed) && styles.optionOn]}
                >
                  <Emblem icon={o.icon} tone={o.tone} size={40} />
                  <View style={styles.flex}>
                    <AppText variant="bodyStrong">{o.label}</AppText>
                    <AppText variant="caption" color={Brand.inkMuted}>
                      {o.hint}
                    </AppText>
                  </View>
                  <View style={[styles.count, on && styles.countOn]}>
                    <AppText variant="label" color={on ? Brand.onCherry : Brand.ink}>
                      {String(o.count)}
                    </AppText>
                  </View>
                </Pressable>
              );
            })}
          </Animated.View>
        </>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  wrap: { zIndex: 20, elevation: 20, alignSelf: 'flex-start' },
  flex: { flex: 1, minWidth: 0 },
  pressed: { opacity: 0.7 },
  pill: {
    minHeight: 44,
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
    paddingHorizontal: 14,
    borderRadius: 22,
    borderWidth: 1.5,
    borderColor: Brand.blush,
    backgroundColor: Brand.surface,
  },
  pillOpen: { borderColor: Brand.cherry, backgroundColor: Brand.roseTint },
  scrim: { position: 'absolute', top: -2000, left: -2000, width: 5000, height: 5000 },
  menu: {
    position: 'absolute',
    top: 52,
    left: 0,
    width: 300,
    maxWidth: 340,
    borderRadius: 20,
    backgroundColor: Brand.surface,
    borderWidth: 1,
    borderColor: Brand.hairline,
    overflow: 'hidden',
    shadowColor: Brand.ink,
    shadowOpacity: 0.14,
    shadowRadius: 18,
    shadowOffset: { width: 0, height: 8 },
    elevation: 12,
  },
  option: { flexDirection: 'row', alignItems: 'center', gap: 12, paddingHorizontal: 14, paddingVertical: 12 },
  optionOn: { backgroundColor: Brand.roseTint },
  divider: { borderTopWidth: 1, borderTopColor: Brand.hairline },
  count: {
    minWidth: 30,
    height: 30,
    borderRadius: 15,
    paddingHorizontal: 8,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: Brand.shell,
  },
  countOn: { backgroundColor: Brand.cherry },
});
