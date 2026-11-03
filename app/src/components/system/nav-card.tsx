import type { ReactNode } from 'react';
import { Pressable, StyleSheet, View } from 'react-native';
import Animated, { useAnimatedStyle, useSharedValue, withSpring } from 'react-native-reanimated';

import { AppIcon, type AppIconName } from '@/components/system/app-icon';
import { Icon } from '@/components/system/icon';
import { AppText } from '@/components/ui/app-text';
import { Colors, Haptic, Physics } from '@/constants/theme';

// THE card of the ASHA app. One pattern for every list (villages, women,
// visits, reminders), so a first-time user learns it once:
//   [clay icon]  Big title              [status]  ›
//                one plain line
// The › always means "opens another page". Status is one word in one of
// three colours: red = needs care, green = all well, plum = new / waiting.

export type Status = { label: string; tone: 'alert' | 'ok' | 'info' };

type Props = {
  icon?: AppIconName;
  /** A letter avatar instead of an icon (people). */
  initial?: string;
  title: string;
  line?: string;
  status?: Status;
  onPress?: () => void;
  /** Right-hand control instead of the arrow (e.g. a Remind button). */
  action?: ReactNode;
  accessibilityLabel?: string;
  emphasis?: boolean;
  /** Lines for the title (1; notifications use 2: Kannada and Hindi run long). */
  titleLines?: number;
};

const TONE = {
  alert: { bg: Colors.cherryMist, fg: Colors.cherry },
  ok: { bg: Colors.sageTint, fg: Colors.sage },
  info: { bg: Colors.lilacTint, fg: Colors.plum },
} as const;

export function NavCard({ icon, initial, title, line, status, onPress, action, accessibilityLabel, emphasis, titleLines = 1 }: Props) {
  const press = useSharedValue(1);
  const a = useAnimatedStyle(() => ({ transform: [{ scale: press.get() }] }));
  const body = (
    <>
      {icon ? (
        <View style={styles.well}>
          <AppIcon name={icon} size={34} />
        </View>
      ) : initial ? (
        <View style={[styles.avatar, status?.tone === 'alert' && styles.avatarAlert]}>
          <AppText variant="heading" color={status?.tone === 'alert' ? Colors.cherry : Colors.ink}>
            {initial}
          </AppText>
        </View>
      ) : null}
      <View style={styles.text}>
        <AppText variant="heading" numberOfLines={titleLines}>
          {title}
        </AppText>
        {!!line && (
          <AppText variant="caption" color={Colors.inkMuted} numberOfLines={2}>
            {line}
          </AppText>
        )}
      </View>
      {!!status && (
        <View style={[styles.pill, { backgroundColor: TONE[status.tone].bg }]}>
          <AppText variant="label" color={TONE[status.tone].fg} numberOfLines={1}>
            {status.label}
          </AppText>
        </View>
      )}
      {action ?? (onPress ? <Icon name="forward" size={22} color={Colors.cherry} weight="bold" /> : null)}
    </>
  );
  const cardStyle = [styles.card, emphasis && styles.emphasis];
  if (!onPress) return <View style={cardStyle}>{body}</View>;
  return (
    <Animated.View style={a}>
      <Pressable
        accessibilityRole="button"
        accessibilityLabel={accessibilityLabel ?? [title, line, status?.label].filter(Boolean).join('. ')}
        onPressIn={() => press.set(withSpring(Physics.pressScale, Physics.press))}
        onPressOut={() => press.set(withSpring(1, Physics.press))}
        onPress={() => {
          Haptic.light();
          onPress();
        }}
        style={cardStyle}
      >
        {body}
      </Pressable>
    </Animated.View>
  );
}

const styles = StyleSheet.create({
  card: {
    minHeight: 76,
    flexDirection: 'row',
    alignItems: 'center',
    gap: 12,
    paddingHorizontal: 14,
    paddingVertical: 12,
    borderRadius: 20,
    borderWidth: 1,
    borderColor: Colors.bandLine,
    backgroundColor: Colors.surface,
  },
  emphasis: { borderWidth: 2, borderColor: Colors.cherry, backgroundColor: Colors.cherryMist },
  well: { width: 48, height: 48, borderRadius: 16, alignItems: 'center', justifyContent: 'center', backgroundColor: Colors.shell },
  avatar: { width: 48, height: 48, borderRadius: 24, alignItems: 'center', justifyContent: 'center', backgroundColor: Colors.shell },
  avatarAlert: { backgroundColor: Colors.cherryMist },
  text: { flex: 1, minWidth: 0, gap: 2 },
  pill: { maxWidth: '40%', paddingHorizontal: 10, paddingVertical: 4, borderRadius: 12 },
});
