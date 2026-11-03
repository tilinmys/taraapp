import { StyleSheet, View } from 'react-native';
import Svg, { Circle, Path } from 'react-native-svg';

import { Icon, type IconName } from '@/components/system/icon';
import { Brand } from '@/constants/brand';

// Emblem: the app's own illustrated icon tile, drawn in code so it stays crisp
// and on-palette without stock art. A tinted squircle, a soft petal arc and a
// small orbit dot behind a duotone Phosphor glyph (outline in the accent,
// inner fill in a second warm colour at 30 %). One tone per topic.

export type EmblemTone = 'pumpkin' | 'cherry' | 'plum' | 'sage';

export const TONES: Record<EmblemTone, { fg: string; fill: string; bg: string }> = {
  pumpkin: { fg: Brand.pumpkin, fill: Brand.cherry, bg: Brand.orangeTint },
  cherry: { fg: Brand.cherry, fill: Brand.pumpkin, bg: Brand.roseTint },
  plum: { fg: Brand.plum, fill: Brand.blush, bg: Brand.lilacTint },
  sage: { fg: Brand.sage, fill: Brand.pumpkin, bg: Brand.sageTint },
};

type Props = {
  icon: IconName;
  tone?: EmblemTone;
  size?: number;
  /** Solid surface behind the tile, e.g. when the card itself is tinted. */
  onTint?: boolean;
};

export function Emblem({ icon, tone = 'cherry', size = 52, onTint }: Props) {
  const c = TONES[tone];
  const r = Math.round(size * 0.3);
  return (
    <View
      style={[styles.tile, { width: size, height: size, borderRadius: r, backgroundColor: onTint ? Brand.surface : c.bg }]}
      aria-hidden
    >
      <Svg width={size} height={size} viewBox="0 0 100 100" style={StyleSheet.absoluteFill}>
        <Path d="M100 58 C 78 60, 60 78, 58 100 L100 100 Z" fill={c.fg} opacity={0.1} />
        <Circle cx={22} cy={22} r={5} fill={c.fill} opacity={0.35} />
        <Circle cx={22} cy={22} r={10} stroke={c.fg} strokeWidth={1.5} fill="none" opacity={0.18} />
      </Svg>
      <Icon name={icon} size={Math.round(size * 0.52)} color={c.fg} tone={c.fill} />
    </View>
  );
}

const styles = StyleSheet.create({
  tile: { alignItems: 'center', justifyContent: 'center', overflow: 'hidden' },
});
