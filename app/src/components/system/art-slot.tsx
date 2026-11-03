import { Image, StyleSheet, View, type ImageSourcePropType } from 'react-native';

import { ART_SLOTS, type ArtSlotName } from '@/constants/art-slots';
import { Colors } from '@/constants/theme';
import { Emblem, type EmblemTone } from '@/components/system/emblem';
import type { IconName } from '@/components/system/icon';

// Renders the image registered for `slot` (constants/art-slots.ts) or, until one
// is provided, a branded icon tile of the exact same size. Swap in artwork
// later without touching any screen. Square slots fall back to an Emblem.

type Props = {
  slot: ArtSlotName;
  fallback: IconName;
  size?: number; // square tile size
  width?: number; // for wide slots
  height?: number;
  active?: boolean;
  label?: string;
  tone?: EmblemTone;
};

export function ArtSlot({ slot, fallback, size = 52, width, height, active, label, tone = 'cherry' }: Props) {
  const source = ART_SLOTS[slot] as ImageSourcePropType | null;
  const w = width ?? size;
  const h = height ?? size;
  if (source) {
    return (
      <Image
        fadeDuration={0}
        source={source}
        style={{ width: w, height: h, borderRadius: Math.min(16, h / 3) }}
        resizeMode="contain"
        accessibilityLabel={label}
        accessibilityIgnoresInvertColors
      />
    );
  }
  if (w === h) return <Emblem icon={fallback} tone={tone} size={w} onTint={active} />;
  // Wide slots: a tinted banner with the emblem centred.
  return (
    <View style={[styles.wide, { width: w, height: h, borderRadius: Math.min(20, h / 3) }]} aria-hidden>
      <Emblem icon={fallback} tone={tone} size={Math.round(h * 0.6)} onTint />
    </View>
  );
}

const styles = StyleSheet.create({
  wide: { alignItems: 'center', justifyContent: 'center', backgroundColor: Colors.roseTint },
});
