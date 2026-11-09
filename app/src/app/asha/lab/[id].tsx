import { useLocalSearchParams } from 'expo-router';
import { useState } from 'react';
import { View } from 'react-native';

import { formStyles as styles, Pills, Row, savedAt, TEST_STATUS, Value } from '@/components/asha/clinical-inputs';
import { Screen } from '@/components/system/screen';
import { AppText } from '@/components/ui/app-text';
import { Colors, Haptic } from '@/constants/theme';
import { ME_ASHA, staff } from '@/data/care-network';
import { testsFor, validField, type TestEntry, type TestStatus } from '@/data/clinical';
import { visitFor, type Visit } from '@/data/mockScreening';
import { store, useMother } from '@/data/store';
import { useOnboarding, useT } from '@/features/onboarding/context';
import { whenLabel } from '@/lib/day-label';
import { goBack } from '@/lib/nav';

// ASHA: the Lab report. The results that come back from the lab or the scan
// (Hb, urine, blood group, HIV / syphilis / HBsAg, glucose, ultrasound), for
// her current visit. Each test is Done (with its result), Pending, Not
// available or Not needed, so a missing result is never read as a normal
// one. Saved with her visit's entry (store.saveLab): the rules read Hb from
// it, the Hb trend gets its point, and the PDF report prints it.

export default function LabReportScreen() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const mother = useMother(id);
  if (!mother) return null;
  return <LabForm key={mother.id} id={mother.id} />;
}

function LabForm({ id }: { id: string }) {
  const mother = useMother(id)!;
  const t = useT();
  const { language } = useOnboarding();
  const entry = mother.clinical?.[0];
  const visit: Visit = entry?.visit ?? visitFor(mother);
  const tests = testsFor(visit);
  const [status, setStatus] = useState<Record<string, TestStatus>>(() => Object.fromEntries(Object.entries(entry?.tests ?? {}).map(([k, x]) => [k, x.status])));
  const [results, setResults] = useState<Record<string, string>>(() => Object.fromEntries(Object.entries(entry?.tests ?? {}).map(([k, x]) => [k, x.result ?? ''])));
  const [tried, setTried] = useState(false);

  // A Done test needs a valid result (Hb 3-20 g/dL, a choice, ...).
  const bad = new Set(tests.filter((x) => status[x.id] === 'done' && x.result && !validField(x.result, results[x.id] ?? '')).map((x) => x.id));
  const missing = new Set(tests.filter((x) => status[x.id] === 'done' && x.result && x.result.kind !== 'text' && !(results[x.id] ?? '').trim()).map((x) => x.id));
  const anything = Object.values(status).some(Boolean);
  const back = () => goBack({ pathname: '/asha/mother/[id]', params: { id: mother.id } });

  const save = () => {
    setTried(true);
    if (bad.size || missing.size) {
      Haptic.warning();
      return;
    }
    const now = savedAt();
    const out: Record<string, TestEntry> = {};
    for (const x of tests) {
      const st = status[x.id];
      if (!st) continue;
      const result = st === 'done' ? (results[x.id] ?? '').trim() || undefined : undefined;
      const prev = entry?.tests[x.id];
      // Unchanged results keep their original trace (who and when).
      out[x.id] = prev && prev.status === st && prev.result === result ? prev : { status: st, result, by: ME_ASHA, at: now };
    }
    store.saveLab(mother.id, { visit, tests: out, by: ME_ASHA });
    Haptic.success();
    back();
  };

  return (
    <Screen
      eyebrow={`${mother.name} · ${t(visit === 1 ? 'visit1' : visit === 2 ? 'visit2' : 'visit3')}`}
      title={t('labTitle')}
      compactTitle={t('labTitle')}
      back={{ label: t('back'), onPress: back }}
      cta={{ label: t('labSave'), enabled: anything, icon: 'checkCircle', haptic: 'success', context: tried && (bad.size || missing.size) ? t('labInvalid') : undefined, onPress: save }}
    >
      <AppText variant="caption" color={Colors.inkMuted}>
        {t('labHint')}
      </AppText>
      {!!entry && Object.keys(entry.tests).length > 0 && (
        <AppText variant="caption" color={Colors.inkMuted}>
          {t('clinLast', { name: staff(entry.by)?.name ?? entry.by, when: whenLabel(entry.at, language) })}
        </AppText>
      )}
      <View style={styles.card}>
        {tests.map((x) => (
          <Row key={x.id} label={x.label[language]} invalid={tried && (bad.has(x.id) || missing.has(x.id))}>
            <Pills
              options={TEST_STATUS.map((o) => ({ id: o.id, label: t(o.key) }))}
              value={status[x.id]}
              label={x.label[language]}
              onChange={(v) => setStatus((s) => ({ ...s, [x.id]: v as TestStatus }))}
            />
            {status[x.id] === 'done' && x.result && (
              <Value kind={x.result} value={results[x.id] ?? ''} onChange={(v) => setResults((s) => ({ ...s, [x.id]: v }))} lang={language} t={t} label={x.label[language]} />
            )}
          </Row>
        ))}
      </View>
    </Screen>
  );
}
