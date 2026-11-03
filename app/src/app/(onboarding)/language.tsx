import { useCallback } from 'react';
import { Pressable, ScrollView, StyleSheet, View } from 'react-native';

import { Icon } from '@/components/system/icon';
import { AppText } from '@/components/ui/app-text';
import { Brand } from '@/constants/brand';
import { useOnboarding, useOnboardingCta, useOnboardingNav, useT } from '@/features/onboarding/context';
import { LANGUAGES } from '@/i18n/strings';

// Screen 02: language. Each option is written in its own script so people
// recognise their language before they can read anything else; the whole UI
// switches as soon as one is chosen.

export default function LanguageStep() {
  const t = useT();
  const { language, setLanguage } = useOnboarding();

  // A language is always selected (Kannada by default), so Continue is live;
  // its label follows the chosen language.
  const { forward } = useOnboardingNav();
  const next = useCallback(() => forward('/role'), [forward]);
  useOnboardingCta(t('continue'), true, next);

  return (
    <ScrollView contentContainerStyle={styles.scroll} showsVerticalScrollIndicator={false}>
      <View style={styles.column}>
        <View style={styles.header}>
          <AppText variant="display" accessibilityRole="header">
            {t('languageTitle')}
          </AppText>
          <AppText variant="subtitle" color={Brand.inkMuted}>
            {t('languageSubtitle')}
          </AppText>
        </View>

        <View style={styles.options} accessibilityRole="radiogroup">
          {LANGUAGES.map((l) => {
            const selected = l.id === language;
            return (
              <Pressable
                key={l.id}
                accessibilityRole="radio"
                accessibilityState={{ checked: selected }}
                aria-checked={selected}
                accessibilityLabel={l.english === l.native ? l.native : `${l.native}, ${l.english}`}
                onPress={() => setLanguage(l.id)}
                style={({ pressed }) => [styles.option, selected && styles.optionOn, pressed && styles.pressed]}
              >
                <View style={styles.optionText}>
                  <AppText variant="title">{l.native}</AppText>
                  {l.english !== l.native && (
                    <AppText variant="caption" color={Brand.inkMuted}>
                      {l.english}
                    </AppText>
                  )}
                </View>
                <View style={[styles.radio, selected && styles.radioOn]}>
                  {selected && <Icon name="check" size={14} color={Brand.onCherry} weight="bold" />}
                </View>
              </Pressable>
            );
          })}
        </View>
      </View>
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  scroll: {
    flexGrow: 1,
    paddingHorizontal: 24,
    paddingTop: 24,
    paddingBottom: 24,
    alignItems: 'center',
  },
  column: {
    width: '100%',
    maxWidth: 560,
    gap: 28,
  },
  header: {
    gap: 10,
  },
  options: {
    gap: 12,
  },
  option: {
    minHeight: 72,
    flexDirection: 'row',
    alignItems: 'center',
    gap: 14,
    paddingHorizontal: 20,
    paddingVertical: 14,
    borderRadius: 16,
    borderWidth: 1.5,
    borderColor: Brand.outline,
    backgroundColor: Brand.surface,
  },
  optionOn: {
    borderWidth: 2,
    borderColor: Brand.cherry,
    backgroundColor: Brand.cherryMist,
  },
  optionText: {
    flex: 1,
    gap: 2,
  },
  radio: {
    width: 26,
    height: 26,
    borderRadius: 13,
    borderWidth: 2,
    borderColor: Brand.outline,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: Brand.surface,
  },
  radioOn: {
    borderColor: Brand.cherry,
    backgroundColor: Brand.cherry,
  },
  pressed: {
    opacity: 0.8,
  },
});
