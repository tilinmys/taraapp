import { useRef, type ComponentRef, type ReactNode } from 'react';
import { Pressable, StyleSheet, TextInput, View } from 'react-native';

import { AppText } from '@/components/ui/app-text';
import { Colors, Haptic } from '@/constants/theme';
import { fontFor, scriptForLang } from '@/constants/typography';
import type { FieldKind, Source, TestStatus } from '@/data/clinical';
import type { Lang, StringKey } from '@/i18n/strings';

// The inputs of the ASHA's forms (Measurements: app/asha/clinical, Lab
// report: app/asha/lab): one labelled row, a value of any kind (number with
// its unit, BP as two boxes, words, yes / no, a few choices) and pills.

export const TEST_STATUS: { id: TestStatus; key: StringKey }[] = [
  { id: 'done', key: 'tsDone' },
  { id: 'pending', key: 'tsPending' },
  { id: 'unavailable', key: 'tsUnavailable' },
  { id: 'not_indicated', key: 'tsNotIndicated' },
];

export const SOURCE: Record<Source, StringKey> = { measured: 'srcMeasured', examination: 'srcExamination', lab: 'srcLab', ultrasound: 'srcUltrasound', mcp: 'srcMcp' };

/** When a value is saved (called from Save, never while rendering). */
export const savedAt = () => Date.now();

export function Row({ label, invalid, children }: { label: string; invalid?: boolean; children: ReactNode }) {
  return (
    <View style={formStyles.row}>
      <AppText variant="label" color={invalid ? Colors.cherry : Colors.ink}>
        {label}
      </AppText>
      {children}
    </View>
  );
}

/** One value: a number (with its unit), a BP "120/80", words, yes / no, or one of a few choices. */
export function Value({ kind, value, onChange, lang, t, label }: { kind: FieldKind; value: string; onChange: (v: string) => void; lang: Lang; t: (k: StringKey) => string; label: string }) {
  if (kind.kind === 'yesno')
    return (
      <Pills
        options={[
          { id: 'yes', label: t('clinYes') },
          { id: 'no', label: t('clinNo') },
        ]}
        value={value || undefined}
        label={label}
        onChange={onChange}
      />
    );
  if (kind.kind === 'choice') return <Pills options={kind.options.map((o) => ({ id: o.id, label: o.label[lang] }))} value={value || undefined} label={label} onChange={onChange} />;
  if (kind.kind === 'bp') return <BpValue value={value} onChange={onChange} label={label} t={t} />;
  const numeric = kind.kind === 'number';
  return (
    <View style={formStyles.inputRow}>
      <TextInput
        value={value}
        onChangeText={onChange}
        placeholder=""
        placeholderTextColor={Colors.inkSubtle}
        selectionColor={Colors.cherry}
        keyboardType={numeric ? 'decimal-pad' : 'default'}
        accessibilityLabel={label}
        maxLength={kind.kind === 'text' ? 80 : 7}
        style={[formStyles.input, numeric ? formStyles.inputNum : formStyles.inputText, fontFor('input', numeric ? 'latin' : scriptForLang(lang))]}
        maxFontSizeMultiplier={1.4}
      />
      {kind.kind === 'number' && (
        <AppText variant="body" color={Colors.inkMuted}>
          {kind.unit}
        </AppText>
      )}
    </View>
  );
}

/**
 * BP as two big number boxes, [ 120 ] / [ 80 ] mmHg: the number pad only, no
 * hunting for "/" on the keyboard. Stored as "120/80" (the same as before).
 */
function BpValue({ value, onChange, label, t }: { value: string; onChange: (v: string) => void; label: string; t: (k: StringKey) => string }) {
  const [sys, dia] = value.split('/');
  // The third digit of the upper number moves straight to the lower box
  // (the number pad stays open). A 2-digit upper (e.g. 85) needs a tap.
  const diaRef = useRef<ComponentRef<typeof TextInput>>(null);
  const set = (s: string, d: string) => onChange(s || d ? `${s}/${d}` : '');
  const box = (v: string, part: 'sys' | 'dia') => (
    <TextInput
      ref={part === 'dia' ? diaRef : undefined}
      value={v ?? ''}
      onChangeText={(x) => {
        const n = x.replace(/[^0-9]/g, '').slice(0, 3);
        if (part === 'sys') {
          set(n, dia ?? '');
          if (n.length === 3 && (sys ?? '').length < 3) diaRef.current?.focus();
        } else set(sys ?? '', n);
      }}
      returnKeyType={part === 'sys' ? 'next' : 'done'}
      blurOnSubmit={part !== 'sys'}
      onSubmitEditing={part === 'sys' ? () => diaRef.current?.focus() : undefined}
      keyboardType="number-pad"
      maxLength={3}
      placeholder={part === 'sys' ? '120' : '80'}
      placeholderTextColor={Colors.inkSubtle}
      selectionColor={Colors.cherry}
      accessibilityLabel={`${label}: ${t(part === 'sys' ? 'clinSys' : 'clinDia')}`}
      style={[formStyles.input, formStyles.bpBox, fontFor('metric')]}
      maxFontSizeMultiplier={1.3}
    />
  );
  return (
    <View style={formStyles.inputRow}>
      {box(sys, 'sys')}
      <AppText variant="metric" color={Colors.inkMuted}>
        /
      </AppText>
      {box(dia, 'dia')}
      <AppText variant="body" color={Colors.inkMuted}>
        mmHg
      </AppText>
    </View>
  );
}

/** Choices as pills that wrap (a tap on the chosen one clears it). */
export function Pills({ options, value, onChange, label }: { options: { id: string; label: string }[]; value?: string; onChange: (id: string) => void; label: string }) {
  return (
    <View style={formStyles.pills} accessibilityRole="radiogroup" accessibilityLabel={label}>
      {options.map((o) => {
        const on = o.id === value;
        return (
          <Pressable
            key={o.id}
            accessibilityRole="radio"
            accessibilityState={{ checked: on }}
            onPress={() => {
              Haptic.light();
              onChange(on ? '' : o.id);
            }}
            style={({ pressed }) => [formStyles.pill, on && formStyles.pillOn, pressed && formStyles.pressed]}
          >
            <AppText variant="buttonSmall" color={on ? Colors.onCherry : Colors.ink} numberOfLines={1}>
              {o.label}
            </AppText>
          </Pressable>
        );
      })}
    </View>
  );
}

export const formStyles = StyleSheet.create({
  flex: { flex: 1, minWidth: 0 },
  pressed: { opacity: 0.75 },
  card: { gap: 12, padding: 14, borderRadius: 20, borderWidth: 1, borderColor: Colors.bandLine, backgroundColor: Colors.surface },
  cardHead: { flexDirection: 'row', alignItems: 'center', flexWrap: 'wrap', gap: 8 },
  row: { gap: 6 },
  inputRow: { flexDirection: 'row', alignItems: 'center', gap: 8 },
  input: { minHeight: 48, paddingHorizontal: 14, borderRadius: 14, borderWidth: 1.5, borderColor: Colors.outline, color: Colors.ink, backgroundColor: Colors.surface },
  inputText: { flex: 1, minWidth: 0 },
  inputNum: { width: 140 },
  // High contrast, big digits: read at a glance in sunlight.
  bpBox: { width: 92, textAlign: 'center', borderWidth: 2, borderColor: Colors.ink, color: Colors.ink },
  pills: { flexDirection: 'row', flexWrap: 'wrap', gap: 6 },
  pill: { minHeight: 40, justifyContent: 'center', paddingHorizontal: 14, borderRadius: 20, borderWidth: 1.5, borderColor: Colors.bandLine, backgroundColor: Colors.surface },
  pillOn: { backgroundColor: Colors.cherry, borderColor: Colors.cherry },
});
