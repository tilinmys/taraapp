import { Image, StyleSheet, View } from 'react-native';
import Animated, { FadeInDown, useReducedMotion } from 'react-native-reanimated';
import Svg, { Circle } from 'react-native-svg';

import { Emblem, TONES, type EmblemTone } from '@/components/system/emblem';
import type { IconName } from '@/components/system/icon';
import { AppText } from '@/components/ui/app-text';
import { PressButton } from '@/components/ui/press-button';
import { Brand } from '@/constants/brand';
import { Curves } from '@/constants/motion';

// Full empty / error / not-found state: the tara leaf "mascot" in orbit rings
// (or a topic emblem), a plain title, one sentence, and the single next step.
// Never a dead end: there is always an action.

const LEAF = require('@/assets/images/logo-layers/tara-leaf.webp');

type Props = {
  title: string;
  body: string;
  /** Topic emblem; omit to show the tara leaf mascot. */
  icon?: IconName;
  tone?: EmblemTone;
  action?: { label: string; onPress: () => void };
  secondary?: { label: string; onPress: () => void };
  compact?: boolean;
};

export function StateView({ title, body, icon, tone = 'cherry', action, secondary, compact }: Props) {
  const reduceMotion = useReducedMotion();
  const size = compact ? 96 : 136;
  const c = TONES[tone];
  const enter = reduceMotion ? undefined : FadeInDown.duration(420).easing(Curves.settle);
  return (
    <Animated.View entering={enter} style={[styles.wrap, compact && styles.compact]}>
      <View style={{ width: size, height: size, alignItems: 'center', justifyContent: 'center' }} aria-hidden>
        <Svg width={size} height={size} viewBox="0 0 100 100" style={StyleSheet.absoluteFill}>
          <Circle cx={50} cy={50} r={48} fill={c.bg} />
          <Circle cx={50} cy={50} r={36} stroke={c.fg} strokeWidth={1} fill="none" opacity={0.18} strokeDasharray="2 4" />
          <Circle cx={84} cy={30} r={4} fill={Brand.pumpkin} opacity={0.5} />
          <Circle cx={18} cy={70} r={3} fill={c.fg} opacity={0.35} />
        </Svg>
        {icon ? (
          <Emblem icon={icon} tone={tone} size={Math.round(size * 0.46)} onTint />
        ) : (
          <Image source={LEAF} style={{ width: size * 0.2, height: size * 0.2 * (165 / 63) }} resizeMode="contain" fadeDuration={0} />
        )}
      </View>
      <View style={styles.text}>
        <AppText variant="heading" align="center" accessibilityRole="header">
          {title}
        </AppText>
        <AppText variant="body" color={Brand.inkMuted} align="center">
          {body}
        </AppText>
      </View>
      {action && <PressButton label={action.label} onPress={action.onPress} style={styles.button} />}
      {secondary && <PressButton label={secondary.label} variant="link" onPress={secondary.onPress} />}
    </Animated.View>
  );
}

const styles = StyleSheet.create({
  wrap: { alignItems: 'center', gap: 20, paddingVertical: 32, paddingHorizontal: 8 },
  compact: { paddingVertical: 16, gap: 14 },
  text: { gap: 6, maxWidth: 360 },
  button: { alignSelf: 'stretch' },
});
