import { createContext, useContext, type ReactNode } from 'react';
import { Pressable, StyleSheet, View, type StyleProp, type ViewStyle } from 'react-native';
import Animated, { useAnimatedStyle, useSharedValue, withSpring } from 'react-native-reanimated';

import { AppIcon, type AppIconName } from '@/components/system/app-icon';
import { Emblem, TONES, type EmblemTone } from '@/components/system/emblem';
import { Icon, type IconName } from '@/components/system/icon';
import { TrendChart } from '@/components/report/trend-chart';
import { AppText } from '@/components/ui/app-text';
import { Brand } from '@/constants/brand';
import { Haptic } from '@/lib/haptics';
import { Physics } from '@/constants/motion';

// Bento: the app's grid language. Rows of cells on a 12 pt gap; each cell is
// ONE flat tinted surface (no card-in-card, no glass), one hue per cell, an
// emblem or icon, a label, and one number or line that matters. Mix sizes by
// giving cells different `flex` values or stacking two small cells beside a
// tall one (BentoStack).

// Cells inside a BentoStack size from their content (column), not from an
// equal share of the row width.
const InStack = createContext(false);

export function BentoRow({ children, style }: { children: ReactNode; style?: StyleProp<ViewStyle> }) {
  return <View style={[styles.row, style]}>{children}</View>;
}

export function BentoStack({ children, flex = 1 }: { children: ReactNode; flex?: number }) {
  return (
    <View style={[styles.stack, { flex }]}>
      <InStack.Provider value>{children}</InStack.Provider>
    </View>
  );
}

type CellProps = {
  tone?: EmblemTone | 'neutral' | 'alert';
  icon?: IconName;
  /** Emblem tile instead of the plain icon (feature cells). */
  emblem?: boolean;
  /** Clay icon in a white well, instead of `icon` (the brand's richer look). */
  art?: AppIconName;
  label?: string;
  value?: string;
  unit?: string;
  note?: string;
  flex?: number;
  /** Defaults to 104 (76 when compact). An explicit value, 0 included, always wins. */
  minHeight?: number;
  onPress?: () => void;
  /** Dense cell for lists and phones: 76 pt tall, 10 pt padding, heading-size value, one-line note. */
  compact?: boolean;
  accessibilityLabel?: string;
  children?: ReactNode;
  style?: StyleProp<ViewStyle>;
};

const BG = { neutral: Brand.shell, alert: Brand.cherryMist } as const;

export function BentoCell({
  tone = 'neutral',
  icon,
  emblem,
  art,
  label,
  value,
  unit,
  note,
  flex = 1,
  minHeight: minHeightProp,
  onPress,
  compact,
  accessibilityLabel,
  children,
  style,
}: CellProps) {
  const stacked = useContext(InStack);
  const minHeight = minHeightProp ?? (compact ? 76 : 104);
  const press = useSharedValue(1);
  const pressStyle = useAnimatedStyle(() => ({ transform: [{ scale: press.get() }] }));
  const t = tone === 'neutral' || tone === 'alert' ? null : TONES[tone];
  const bg = t ? t.bg : BG[tone as 'neutral' | 'alert'];
  const fg = t ? t.fg : Brand.cherry;
  const fill = t ? t.fill : Brand.pumpkin;

  const body = (
    <>
      {(icon || art || label) && (
        <View style={styles.head}>
          {art ? (
            <View style={[styles.well, compact && styles.wellCompact]}>
              <AppIcon name={art} size={compact ? 22 : 30} />
            </View>
          ) : null}
          {!art && icon &&
            (emblem && t ? (
              <Emblem icon={icon} tone={tone as EmblemTone} size={compact ? 32 : 40} onTint />
            ) : (
              <Icon name={icon} size={compact ? 18 : 22} color={fg} tone={fill} />
            ))}
          {!!label && (
            <AppText variant="overline" color={tone === 'alert' ? Brand.cherry : Brand.inkMuted} numberOfLines={2} style={styles.label}>
              {label}
            </AppText>
          )}
        </View>
      )}
      {!!value && (
        <View style={styles.valueRow}>
          {/* Numbers use the metric size on one line; words wrap as a heading. */}
          {value.length <= 9 && !compact ? (
            <AppText variant="metric" color={tone === 'alert' ? Brand.cherry : Brand.ink} numberOfLines={1} adjustsFontSizeToFit minimumFontScale={0.7}>
              {value}
            </AppText>
          ) : (
            <AppText variant="heading" color={tone === 'alert' ? Brand.cherry : Brand.ink} style={styles.flex}>
              {value}
            </AppText>
          )}
          {!!unit && (
            <AppText variant="caption" color={Brand.inkMuted}>
              {unit}
            </AppText>
          )}
        </View>
      )}
      {!!note && (
        <AppText variant="caption" color={tone === 'alert' ? Brand.ink : Brand.inkMuted} numberOfLines={compact ? 1 : 3}>
          {note}
        </AppText>
      )}
      {children}
    </>
  );

  // A hairline in the cell's own hue keeps tints crisp on the header band.
  const edge = tone === 'neutral' ? Brand.hairline : `${fg}24`;
  const size = stacked ? { flexGrow: 1, flexBasis: 'auto' as const } : { flex };
  const cellStyle = [styles.cell, compact && styles.cellCompact, size, { minHeight, backgroundColor: bg, borderColor: edge }, tone === 'alert' && styles.alert, style];
  if (!onPress) {
    return (
      <View style={cellStyle} accessibilityLabel={accessibilityLabel} accessible={!!accessibilityLabel}>
        {body}
      </View>
    );
  }
  return (
    <Animated.View style={[size, pressStyle]}>
      <Pressable
        accessibilityRole="button"
        accessibilityLabel={accessibilityLabel ?? [label, value, unit, note].filter(Boolean).join(', ')}
        onPressIn={() => press.set(withSpring(Physics.pressScale, Physics.press))}
        onPressOut={() => press.set(withSpring(1, Physics.press))}
        onPress={() => {
          Haptic.light();
          onPress();
        }}
        style={[cellStyle, { flex: undefined, flexGrow: 1 }]}
      >
        {body}
      </Pressable>
    </Animated.View>
  );
}

/**
 * The trend for a vitals cell (react-native-gifted-charts, see trend-chart):
 * it fills the cell's width unless a width is given; the latest point turns
 * cherry when it crosses its limit.
 */
type SparkProps = { data: number[]; color: string; width?: number; height?: number; compact?: boolean; warnAbove?: number; warnBelow?: number };

export function Spark({ data, color, compact, width, height = compact ? 40 : 52, warnAbove, warnBelow }: SparkProps) {
  return <TrendChart data={data} color={color} width={width} height={height} warnAbove={warnAbove} warnBelow={warnBelow} />;
}

const styles = StyleSheet.create({
  row: { flexDirection: 'row', gap: 12 },
  stack: { gap: 12 },
  cell: { borderRadius: 20, padding: 14, gap: 8, overflow: 'hidden', borderWidth: 1 },
  cellCompact: { padding: 10, gap: 4, borderRadius: 16 },
  alert: { borderWidth: 2, borderColor: Brand.cherry },
  // Wraps: on narrow cells the label drops under the icon as a whole word.
  head: { flexDirection: 'row', flexWrap: 'wrap', alignItems: 'center', columnGap: 8, rowGap: 4 },
  label: { flexGrow: 1, flexShrink: 1 },
  flex: { flex: 1, minWidth: 0 },
  valueRow: { flexDirection: 'row', alignItems: 'baseline', gap: 4 },
  well: { width: 42, height: 42, borderRadius: 14, alignItems: 'center', justifyContent: 'center', backgroundColor: Brand.surface },
  wellCompact: { width: 32, height: 32, borderRadius: 10 },
});
