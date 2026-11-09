import { StyleSheet, View } from 'react-native';

import { Icon } from '@/components/system/icon';
import { AppText } from '@/components/ui/app-text';
import { Brand } from '@/constants/brand';
import { MONO } from '@/constants/typography';
import { RULES, triggersOf, type AnswerRecord, type RuleCode } from '@/data/mockScreening';
import { useOnboarding, useT } from '@/features/onboarding/context';
import { tx } from '@/i18n/content';

// Why the app said "high risk": each fired rule with its exact logic, the
// action it asks for, and the answers that triggered it. Deterministic, so
// a doctor can check it line by line (no black box). A rule on a
// measurement (BP, examination) names the measured value instead.

type Props = { rules: RuleCode[]; answers: Record<string, AnswerRecord> };

export function RuleAudit({ rules, answers }: Props) {
  const t = useT();
  const { language } = useOnboarding();
  return (
    <View style={styles.card}>
      <View style={styles.head}>
        <Icon name="shield" size={20} color={Brand.cherry} tone={Brand.pumpkin} />
        <AppText variant="overline" color={Brand.inkMuted} style={styles.flex}>
          {t('raTitle')}
        </AppText>
        <AppText variant="caption" color={Brand.inkMuted}>
          {t('raFired', { n: rules.length })}
        </AppText>
      </View>
      {rules.map((code) => {
        const r = RULES[code];
        const triggers = triggersOf(code, answers);
        return (
          <View key={code} style={[styles.rule, r.severity === 'watch' && styles.watch]}>
            <AppText style={styles.mono}>{`#${code}  ${r.logic}`}</AppText>
            <AppText variant="bodyStrong">{tx(r.title, language)}</AppText>
            <AppText variant="caption" color={Brand.ink}>
              {r.action}
            </AppText>
            {triggers.map((id) => (
              <AppText key={id} variant="caption" color={Brand.inkMuted}>
                {`“${answers[id]?.value}”`}
              </AppText>
            ))}
          </View>
        );
      })}
    </View>
  );
}

const styles = StyleSheet.create({
  card: { gap: 12, padding: 16, borderRadius: 20, backgroundColor: Brand.shell },
  head: { flexDirection: 'row', alignItems: 'center', gap: 8 },
  flex: { flex: 1, minWidth: 0 },
  rule: { gap: 3, paddingLeft: 12, borderLeftWidth: 3, borderLeftColor: Brand.cherry },
  watch: { borderLeftColor: Brand.pumpkin },
  mono: { fontFamily: MONO, fontSize: 13, lineHeight: 19, color: Brand.ink },
});
