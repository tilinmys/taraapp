import { forwardRef, useState } from 'react';
import { StyleSheet, TextInput, View, type TextInputInstance, type TextInputProps } from 'react-native';

import { AppText } from '@/components/ui/app-text';
import { Brand } from '@/constants/brand';
import { fontFor, splitByScript } from '@/constants/typography';

// Labelled text field: label always visible above the input (no placeholder-
// only labels), 56 pt tall, 2 pt cherry border when focused, optional fixed
// prefix (e.g. "+91"), hint and error.

type Props = TextInputProps & {
  label: string;
  hint?: string;
  error?: string;
  /** Fixed, non-editable text before the input, e.g. a country code. */
  prefix?: string;
};

export const TextField = forwardRef<TextInputInstance, Props>(function TextField(
  { label, hint, error, prefix, style, ...input },
  ref,
) {
  const [focused, setFocused] = useState(false);
  const message = error ?? hint;
  // Set what the user types in the Baloo cut for its script (Kannada, Hindi, English).
  const typedScript = splitByScript(String(input.value ?? ''))[0]?.script ?? 'latin';
  return (
    <View style={styles.wrap}>
      <AppText variant="label" nativeID={`${label}-label`}>
        {label}
      </AppText>
      <View style={[styles.box, focused && styles.boxFocused, !!error && styles.boxError]}>
        {!!prefix && (
          <View style={styles.prefix}>
            <AppText variant="input" color={Brand.ink}>
              {prefix}
            </AppText>
          </View>
        )}
        <TextInput
          ref={ref}
          accessibilityLabel={prefix ? `${label} ${prefix}` : label}
          accessibilityLabelledBy={`${label}-label`}
          placeholderTextColor={Brand.inkSubtle}
          selectionColor={Brand.cherry}
          maxFontSizeMultiplier={1.4}
          {...input}
          onFocus={(e) => {
            setFocused(true);
            input.onFocus?.(e);
          }}
          onBlur={(e) => {
            setFocused(false);
            input.onBlur?.(e);
          }}
          style={[styles.input, fontFor('input', typedScript), style]}
        />
      </View>
      {!!message && (
        <AppText variant={error ? 'label' : 'caption'} color={error ? Brand.cherry : Brand.inkMuted}>
          {message}
        </AppText>
      )}
    </View>
  );
});

const styles = StyleSheet.create({
  wrap: {
    gap: 8,
  },
  box: {
    minHeight: 56,
    flexDirection: 'row',
    alignItems: 'center',
    borderRadius: 14,
    borderWidth: 2,
    borderColor: Brand.outline,
    backgroundColor: Brand.surface,
    overflow: 'hidden',
  },
  boxFocused: {
    borderColor: Brand.cherry,
  },
  boxError: {
    borderColor: Brand.cherry,
  },
  prefix: {
    alignSelf: 'stretch',
    justifyContent: 'center',
    paddingHorizontal: 14,
    borderRightWidth: 1,
    borderRightColor: Brand.hairline,
    backgroundColor: Brand.shell,
  },
  input: {
    flex: 1,
    minHeight: 52,
    paddingHorizontal: 16,
    paddingVertical: 12,
    color: Brand.ink,
  },
});
