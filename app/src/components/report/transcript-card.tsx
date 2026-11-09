import { useState } from 'react';
import { Pressable, StyleSheet, View } from 'react-native';
import Animated, { FadeIn, LinearTransition, useReducedMotion } from 'react-native-reanimated';

import { Icon } from '@/components/system/icon';
import { AppText } from '@/components/ui/app-text';
import { Brand } from '@/constants/brand';
import type { TranscriptLine } from '@/data/store';
import { useT } from '@/features/onboarding/context';

// The live talk, as it happened. Collapsed, it shows the opening lines plus
// every danger answer and the question before it (what a reviewer needs);
// "Show full conversation" expands it in place.

type Props = { lines: TranscriptLine[]; preview?: number; title?: string };

export function TranscriptCard({ lines, preview = 6, title: titleProp }: Props) {
  const t = useT();
  const title = titleProp ?? t('trTitle');
  const [open, setOpen] = useState(false);
  const reduceMotion = useReducedMotion();
  const keep = new Set(lines.flatMap((l, i) => (l.danger ? [i - 1, i] : [])));
  const shown = open ? lines : lines.filter((_, i) => i < preview || keep.has(i));
  const hidden = lines.length - shown.length;

  return (
    <Animated.View layout={reduceMotion ? undefined : LinearTransition.springify().damping(22).stiffness(220)} style={styles.card}>
      <View style={styles.head}>
        <Icon name="chat" size={20} color={Brand.pumpkin} tone={Brand.cherry} />
        <AppText variant="overline" color={Brand.inkMuted} style={styles.flex}>
          {title.toUpperCase()}
        </AppText>
        <AppText variant="caption" color={Brand.inkMuted}>
          {t('trLines', { n: lines.length })}
        </AppText>
      </View>
      {shown.map((l, i) => (
        <Animated.View
          key={`${i}-${l.from}-${l.text.slice(0, 16)}`}
          entering={open && !reduceMotion ? FadeIn.duration(200) : undefined}
          style={styles.line}
        >
          <AppText variant="label" color={l.from === 'sakhi' ? Brand.pumpkin : Brand.cherry} style={styles.who}>
            {l.from === 'sakhi' ? 'Sakhi' : 'ASHA'}
          </AppText>
          <AppText variant={l.danger ? 'bodyStrong' : 'body'} color={l.danger ? Brand.cherry : Brand.ink} style={styles.flex}>
            {`${l.danger ? '⚠️ ' : ''}${l.text}`}
          </AppText>
        </Animated.View>
      ))}
      {lines.length === 0 && (
        <AppText variant="caption" color={Brand.inkMuted}>
          {t('trNone')}
        </AppText>
      )}
      {lines.length > preview && (hidden > 0 || open) && (
        <Pressable
          accessibilityRole="button"
          accessibilityState={{ expanded: open }}
          onPress={() => setOpen((o) => !o)}
          style={({ pressed }) => [styles.more, pressed && styles.pressed]}
        >
          <AppText variant="buttonSmall" color={Brand.cherry}>
            {open ? t('trLess') : t('trMore', { n: hidden })}
          </AppText>
          <View style={open && styles.flip}>
            <Icon name="down" size={16} color={Brand.cherry} weight="bold" />
          </View>
        </Pressable>
      )}
    </Animated.View>
  );
}

const styles = StyleSheet.create({
  card: { gap: 10, padding: 16, borderRadius: 20, backgroundColor: Brand.shell },
  head: { flexDirection: 'row', alignItems: 'center', gap: 8 },
  flex: { flex: 1, minWidth: 0 },
  line: { flexDirection: 'row', gap: 10, alignItems: 'flex-start' },
  who: { width: 48, paddingTop: 2 },
  more: { minHeight: 44, flexDirection: 'row', alignItems: 'center', gap: 6 },
  pressed: { opacity: 0.6 },
  flip: { transform: [{ rotate: '180deg' }] },
});
