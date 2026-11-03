import { forwardRef, useState, type RefObject } from 'react';
import { Pressable, StyleSheet, TextInput, View, type TextInputInstance } from 'react-native';

import { AppText } from '@/components/ui/app-text';
import { Colors, Haptic } from '@/constants/theme';

// One-time code (4 or 6 digits). One real TextInput (so SMS autofill, paste
// and screen readers work) drawn as cells; the cell being typed into gets
// the 2 pt Cherry focus border. Light haptic per digit.

type Props = {
  value: string;
  onChange: (code: string) => void;
  label: string;
  onComplete?: () => void;
  length?: number;
};

export const OtpInput = forwardRef<TextInputInstance, Props>(function OtpInput(
  { value, onChange, label, onComplete, length = 6 },
  ref,
) {
  const [focused, setFocused] = useState(false);
  const digits = value.split('');
  const active = Math.min(value.length, length - 1);

  return (
    <Pressable
      onPress={() => (ref as RefObject<TextInputInstance | null>)?.current?.focus()}
      accessible={false}
      style={styles.row}
    >
      {Array.from({ length }, (_, i) => (
        <View
          key={i}
          style={[styles.cell, digits[i] && styles.cellFilled, focused && i === active && styles.cellActive]}
          aria-hidden
        >
          <AppText variant="metric" align="center">
            {digits[i] ?? ''}
          </AppText>
        </View>
      ))}
      <TextInput
        ref={ref}
        value={value}
        onChangeText={(text) => {
          const code = text.replace(/\D/g, '').slice(0, length);
          if (code.length > value.length) Haptic.light();
          onChange(code);
          if (code.length === length) onComplete?.();
        }}
        onFocus={() => setFocused(true)}
        onBlur={() => setFocused(false)}
        keyboardType="number-pad"
        textContentType="oneTimeCode"
        autoComplete="sms-otp"
        maxLength={length}
        accessibilityLabel={label}
        caretHidden
        style={styles.hidden}
      />
    </Pressable>
  );
});

const styles = StyleSheet.create({
  row: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    gap: 8,
  },
  cell: {
    flex: 1,
    maxWidth: 64,
    aspectRatio: 0.86,
    borderRadius: 14,
    borderWidth: 2,
    borderColor: Colors.outline,
    backgroundColor: Colors.surface,
    alignItems: 'center',
    justifyContent: 'center',
  },
  cellFilled: {
    borderColor: Colors.ink,
  },
  cellActive: {
    borderColor: Colors.cherry,
    backgroundColor: Colors.cherryMist,
  },
  hidden: {
    position: 'absolute',
    left: 0,
    top: 0,
    width: '100%',
    height: '100%',
    opacity: 0.011, // still focusable / tappable, never visible
    color: 'transparent',
  },
});
