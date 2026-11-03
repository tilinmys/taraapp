import { useEffect } from 'react';
import { Pressable, StyleSheet, View } from 'react-native';
import Animated, {
  interpolateColor,
  useAnimatedStyle,
  useReducedMotion,
  useSharedValue,
  withSpring,
  withTiming,
} from 'react-native-reanimated';

import { ArtSlot } from '@/components/system/art-slot';
import type { EmblemTone } from '@/components/system/emblem';
import { Icon, type IconName } from '@/components/system/icon';
import type { ArtSlotName } from '@/constants/art-slots';
import { AppText } from '@/components/ui/app-text';
import { Colors, Haptic, Layout, Physics } from '@/constants/theme';

// Selectable role card (radio semantics).
//   unselected  Shell fill, outline border, 1.5 px icon, hollow radio
//   selected    Cherry Mist fill + 2 pt Cherry border (200 ms cross-fade),
//               2.25 px icon, Cherry radio whose white check pops in
//               (scale 0.4 -> 1, rotate -15deg -> 0, bouncy spring)
//   press       springs to 0.97 and back; medium haptic on select
// Each role has its own emblem tone (ASHA pumpkin, mother cherry, doctor plum).

type Props = {
  title: string;
  body: string;
  icon: IconName;
  /** Image slot: shows artwork once provided, the icon until then. */
  slot: ArtSlotName;
  tone: EmblemTone;
  selected: boolean;
  onSelect: () => void;
};

export function RoleCard({ title, body, icon, slot, tone, selected, onSelect }: Props) {
  const reduceMotion = useReducedMotion();
  const press = useSharedValue(1);
  const sel = useSharedValue(selected ? 1 : 0);
  const check = useSharedValue(selected ? 1 : 0);

  useEffect(() => {
    sel.set(withTiming(selected ? 1 : 0, { duration: reduceMotion ? 0 : Physics.selectMs }));
    if (selected && !reduceMotion) {
      check.set(0);
      check.set(withSpring(1, Physics.check));
    } else {
      check.set(withTiming(selected ? 1 : 0, { duration: reduceMotion ? 0 : 120 }));
    }
  }, [check, reduceMotion, sel, selected]);

  const cardStyle = useAnimatedStyle(() => ({
    transform: [{ scale: press.get() }],
    backgroundColor: interpolateColor(sel.get(), [0, 1], [Colors.shell, Colors.cherryMist]),
    borderColor: interpolateColor(sel.get(), [0, 1], [Colors.outline, Colors.cherry]),
  }));
  const radioStyle = useAnimatedStyle(() => ({
    backgroundColor: interpolateColor(sel.get(), [0, 1], [Colors.surface, Colors.cherry]),
    borderColor: interpolateColor(sel.get(), [0, 1], [Colors.outline, Colors.cherry]),
  }));
  const checkStyle = useAnimatedStyle(() => {
    const c = check.get();
    return {
      opacity: Math.min(1, c * 1.6),
      transform: [
        { scale: Physics.checkFrom + (1 - Physics.checkFrom) * c },
        { rotate: `${Physics.checkRotateFrom * (1 - c)}deg` },
      ],
    };
  });

  return (
    <Animated.View style={[styles.card, cardStyle]}>
      <Pressable
        accessibilityRole="radio"
        accessibilityState={{ checked: selected }}
        aria-checked={selected}
        accessibilityLabel={`${title}. ${body}`}
        onPressIn={() => {
          if (!reduceMotion) press.set(withSpring(Physics.pressScale, Physics.press));
        }}
        onPressOut={() => press.set(withSpring(1, Physics.press))}
        onPress={() => {
          if (!selected) Haptic.medium();
          onSelect();
        }}
        style={styles.select}
      >
        <ArtSlot slot={slot} fallback={icon} tone={tone} size={56} active={selected} label={title} />
        <View style={styles.text}>
          <AppText variant="heading" numberOfLines={1} adjustsFontSizeToFit minimumFontScale={0.85}>
            {title}
          </AppText>
          <AppText variant="caption" color={Colors.inkMuted}>
            {body}
          </AppText>
        </View>
        <Animated.View style={[styles.radio, radioStyle]} aria-hidden>
          <Animated.View style={checkStyle}>
            <Icon name="check" size={14} color={Colors.onCherry} weight="bold" />
          </Animated.View>
        </Animated.View>
      </Pressable>
    </Animated.View>
  );
}

const styles = StyleSheet.create({
  card: {
    minHeight: Layout.cardMinHeight,
    flexDirection: 'row',
    alignItems: 'center',
    paddingHorizontal: 16,
    paddingVertical: 14,
    borderRadius: Layout.radius,
    borderWidth: 2, // constant width; only the colour changes, so nothing shifts
  },
  select: {
    flex: 1,
    minHeight: Layout.cardMinHeight - 28,
    flexDirection: 'row',
    alignItems: 'center',
    gap: 14,
  },
  // Text column: takes the free width, never squeezed into mid-word breaks.
  text: {
    flex: 1,
    minWidth: 0,
    paddingRight: 12,
    gap: 2,
  },
  radio: {
    width: 26,
    height: 26,
    borderRadius: 13,
    borderWidth: 2,
    alignItems: 'center',
    justifyContent: 'center',
  },
});
