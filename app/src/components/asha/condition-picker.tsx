import { useState } from 'react';
import { Pressable, StyleSheet, View } from 'react-native';
import Animated, { FadeIn, FadeOut, useReducedMotion } from 'react-native-reanimated';

import { Icon } from '@/components/system/icon';
import { AppText } from '@/components/ui/app-text';
import { Colors, Haptic } from '@/constants/theme';
import { careStatus, store, type MotherRecord } from '@/data/store';
import { useT } from '@/features/onboarding/context';

// "How is she?" dropdown: the ASHA's own call on a woman, in one tap.
// Closed it is a pill showing the current state (Well green / Needs care red
// / Not checked yet plum) with a ▾. Open it shows two big options below.
// Picking "Needs care" also calls onNeedsCare, where the screen asks (in a
// popup) whether to tell the doctor; picking "Well" asks nothing.

export function ConditionPicker({ mother, onNeedsCare }: { mother: MotherRecord; onNeedsCare: (m: MotherRecord) => void }) {
  const t = useT();
  const reduceMotion = useReducedMotion();
  const [open, setOpen] = useState(false);
  const c = careStatus(mother);
  const tone = c === 'alert' ? { fg: Colors.cherry, bg: Colors.cherryMist } : c === 'ok' ? { fg: Colors.sage, bg: Colors.sageTint } : { fg: Colors.plum, bg: Colors.lilacTint };
  const label = c === 'alert' ? t('statusNeedsCare') : c === 'ok' ? t('statusWell') : t('notCheckedYet');

  const pick = (flag: 'well' | 'needs_care') => {
    setOpen(false);
    if ((flag === 'needs_care') === (c === 'alert') && mother.ashaFlag) return;
    store.setFlag(mother.id, flag);
    if (flag === 'needs_care') {
      Haptic.warning();
      onNeedsCare(mother);
    } else Haptic.success();
  };

  return (
    <View style={styles.wrap}>
      <View style={styles.head}>
        <AppText variant="caption" color={Colors.inkMuted} style={styles.flex}>
          {t('condition')}
        </AppText>
        <Pressable
          accessibilityRole="button"
          accessibilityLabel={`${t('condition')} ${label}`}
          accessibilityState={{ expanded: open }}
          onPress={() => {
            Haptic.light();
            setOpen((o) => !o);
          }}
          style={({ pressed }) => [styles.pill, { backgroundColor: tone.bg, borderColor: tone.fg }, pressed && styles.pressed]}
        >
          <View style={[styles.dot, { backgroundColor: tone.fg }]} />
          <AppText variant="label" color={tone.fg} numberOfLines={1}>
            {label}
          </AppText>
          <Icon name="down" size={16} color={tone.fg} weight="bold" />
        </Pressable>
      </View>
      {open && (
        <Animated.View entering={reduceMotion ? undefined : FadeIn.duration(140)} exiting={reduceMotion ? undefined : FadeOut.duration(100)} style={styles.menu} accessibilityRole="radiogroup">
          {(
            [
              { flag: 'well', label: t('statusWell'), fg: Colors.sage, bg: Colors.sageTint, on: c === 'ok' },
              { flag: 'needs_care', label: t('statusNeedsCare'), fg: Colors.cherry, bg: Colors.cherryMist, on: c === 'alert' },
            ] as const
          ).map((o) => (
            <Pressable
              key={o.flag}
              accessibilityRole="radio"
              accessibilityState={{ checked: o.on }}
              accessibilityLabel={o.label}
              onPress={() => pick(o.flag)}
              style={({ pressed }) => [styles.option, { backgroundColor: o.bg }, o.on && { borderColor: o.fg }, pressed && styles.pressed]}
            >
              <View style={[styles.bigDot, { backgroundColor: o.fg }]} />
              <AppText variant="bodyStrong" color={o.fg} style={styles.flex}>
                {o.label}
              </AppText>
              {o.on && <Icon name="check" size={18} color={o.fg} weight="bold" />}
            </Pressable>
          ))}
        </Animated.View>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  flex: { flex: 1, minWidth: 0 },
  pressed: { opacity: 0.75 },
  wrap: { gap: 8 },
  head: { flexDirection: 'row', alignItems: 'center', gap: 8 },
  pill: { minHeight: 40, flexDirection: 'row', alignItems: 'center', gap: 6, paddingHorizontal: 12, borderRadius: 20, borderWidth: 1.5 },
  dot: { width: 8, height: 8, borderRadius: 4 },
  menu: { gap: 8 },
  option: { minHeight: 52, flexDirection: 'row', alignItems: 'center', gap: 12, paddingHorizontal: 14, borderRadius: 14, borderWidth: 2, borderColor: 'transparent' },
  bigDot: { width: 14, height: 14, borderRadius: 7 },
});
