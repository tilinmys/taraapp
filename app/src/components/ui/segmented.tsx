import { useState } from 'react';
import { Pressable, StyleSheet, View, type LayoutChangeEvent } from 'react-native';
import Animated, { useAnimatedStyle, useReducedMotion, useSharedValue, withSpring } from 'react-native-reanimated';

import { AppText } from '@/components/ui/app-text';
import { Colors, Haptic, Physics } from '@/constants/theme';

// Segmented toggle of two or three options (Create Account / Sign In; first /
// second / third visit). The active
// pill (Cherry Mist + 2 pt Cherry border) springs between the options;
// light haptic on change.

type Option<T extends string> = { id: T; label: string };

export function Segmented<T extends string>({
  options,
  value,
  onChange,
}: {
  options: [Option<T>, Option<T>] | [Option<T>, Option<T>, Option<T>];
  value: T;
  onChange: (id: T) => void;
}) {
  const reduceMotion = useReducedMotion();
  const [width, setWidth] = useState(0);
  const n = options.length;
  const index = options.findIndex((o) => o.id === value);
  const x = useSharedValue(index);

  const thumbStyle = useAnimatedStyle(() => ({
    width: width / n - 4,
    transform: [{ translateX: (x.get() * width) / n }],
  }));

  const choose = (i: number) => {
    if (i === index) return;
    Haptic.light();
    x.set(reduceMotion ? i : withSpring(i, Physics.page));
    onChange(options[i].id);
  };

  return (
    <View
      style={styles.track}
      accessibilityRole="tablist"
      onLayout={(e: LayoutChangeEvent) => setWidth(e.nativeEvent.layout.width)}
    >
      {width > 0 && <Animated.View style={[styles.thumb, thumbStyle]} />}
      {options.map((o, i) => (
        <Pressable
          key={o.id}
          accessibilityRole="tab"
          accessibilityState={{ selected: i === index }}
          aria-selected={i === index}
          accessibilityLabel={o.label}
          onPress={() => choose(i)}
          style={styles.option}
        >
          <AppText variant="buttonSmall" color={i === index ? Colors.cherryPressed : Colors.inkMuted} align="center">
            {o.label}
          </AppText>
        </Pressable>
      ))}
    </View>
  );
}

const styles = StyleSheet.create({
  track: {
    flexDirection: 'row',
    minHeight: 48,
    padding: 2,
    borderRadius: 14,
    backgroundColor: Colors.shell,
    borderWidth: 1,
    borderColor: Colors.hairline,
  },
  thumb: {
    position: 'absolute',
    top: 2,
    bottom: 2,
    left: 2,
    borderRadius: 12,
    backgroundColor: Colors.cherryMist,
    borderWidth: 2,
    borderColor: Colors.cherry,
  },
  option: {
    flex: 1,
    minHeight: 44,
    alignItems: 'center',
    justifyContent: 'center',
  },
});
