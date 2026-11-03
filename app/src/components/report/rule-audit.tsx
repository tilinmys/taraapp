import { StyleSheet, View } from 'react-native';

import { Icon } from '@/components/system/icon';
import { AppText } from '@/components/ui/app-text';
import { Brand } from '@/constants/brand';
import { MONO } from '@/constants/typography';
import { ANTENATAL_QUESTIONS, RULES, type AnswerRecord, type RuleCode } from '@/data/mockScreening';

// Why the app said "high risk": each fired rule with its exact logic, the
// action it asks for, and the answers that triggered it. Deterministic, so
// a doctor can check it line by line (no black box).

type Props = { rules: RuleCode[]; answers: Record<string, AnswerRecord> };

export function RuleAudit({ rules, answers }: Props) {
  return (
    <View style={styles.card}>
      <View style={styles.head}>
        <Icon name="shield" size={20} color={Brand.cherry} tone={Brand.pumpkin} />
        <AppText variant="overline" color={Brand.inkMuted} style={styles.flex}>
          RULE AUDIT
        </AppText>
        <AppText variant="caption" color={Brand.inkMuted}>
          {`${rules.length} fired`}
        </AppText>
      </View>
      {rules.map((code) => {
        const r = RULES[code];
        const triggers = ANTENATAL_QUESTIONS.filter((q) => q.rule === code && answers[q.id]?.danger);
        return (
          <View key={code} style={[styles.rule, r.severity === 'watch' && styles.watch]}>
            <AppText style={styles.mono}>{`#${code}  ${r.logic}`}</AppText>
            <AppText variant="bodyStrong">{r.title}</AppText>
            <AppText variant="caption" color={Brand.ink}>
              {r.action}
            </AppText>
            {triggers.map((q) => (
              <AppText key={q.id} variant="caption" color={Brand.inkMuted}>
                {`“${answers[q.id]?.value}”`}
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
