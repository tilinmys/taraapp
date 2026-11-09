import { router, useLocalSearchParams } from 'expo-router';
import { useState } from 'react';
import { View } from 'react-native';

import { formStyles as styles, Row, savedAt, SOURCE, Value } from '@/components/asha/clinical-inputs';
import { Screen } from '@/components/system/screen';
import { AppText } from '@/components/ui/app-text';
import { Colors, Haptic } from '@/constants/theme';
import { ME_ASHA, dayStart, staff } from '@/data/care-network';
import { groupsFor, validField, type Entry, type FieldKind } from '@/data/clinical';
import { visitFor, type Visit } from '@/data/mockScreening';
import { store, useMother } from '@/data/store';
import { useOnboarding, useT } from '@/features/onboarding/context';
import { whenLabel } from '@/lib/day-label';
import { goBack } from '@/lib/nav';

// ASHA: what was MEASURED, EXAMINED or REPORTED at this visit (the MCP card).
// Sakhi never asks these. One card per group. Every value is saved with who
// entered it, when, and its source (the group's: measured, examination, MCP
// card). The same day's entry opens again for correction. Registration
// numbers carry over from her earlier entries. Lab results have their own
// screen (app/asha/lab), so saving here never touches them.

export default function ClinicalEntryScreen() {
  const { id, fromReview, report } = useLocalSearchParams<{ id: string; fromReview?: string; report?: string }>();
  const mother = useMother(id);
  if (!mother) return null;
  // Step 2 of the check-in (after the answers were checked): Save goes on to the report.
  return <Form key={mother.id} id={mother.id} next={fromReview === '1' ? { report } : undefined} />;
}

function Form({ id, next }: { id: string; next?: { report?: string } }) {
  const mother = useMother(id)!;
  const t = useT();
  const { language } = useOnboarding();
  const today = dayStart(0);
  const latest = mother.clinical?.[0];
  const todays = latest && latest.at >= today ? latest : undefined;
  // This visit: today's report's, else the one her record points to.
  const r0 = mother.reports[0];
  const visit: Visit = todays?.visit ?? (r0?.at && r0.at >= today && r0.visit ? r0.visit : visitFor(mother));
  const groups = groupsFor(visit);

  const [values, setValues] = useState<Record<string, string>>(() => {
    const v: Record<string, string> = {};
    for (const [k, e] of Object.entries(todays?.fields ?? {})) v[k] = e.value;
    // Registration numbers do not change between visits.
    for (const k of ['rchId', 'mcpNo', 'anm', 'regDate']) if (!v[k] && latest?.fields[k]) v[k] = latest.fields[k].value;
    // What the app already knows is filled in (the ASHA only checks it).
    const asha = staff(ME_ASHA);
    v.regDate ||= new Date(today).toLocaleDateString('en-IN', { day: '2-digit', month: 'short', year: 'numeric' });
    v.ashaNameId ||= asha ? `${asha.name} (${asha.id})` : ME_ASHA;
    v.phc ||= mother.village;
    v.phone ||= mother.phone ?? '';
    return v;
  });
  const [tried, setTried] = useState(false);

  const fieldKind = new Map(groups.flatMap((g) => g.fields.map((f) => [f.id, f as FieldKind])));
  const bad = new Set([...fieldKind].filter(([k, f]) => !validField(f, values[k] ?? '')).map(([k]) => k));
  const anything = Object.values(values).some((v) => v.trim());

  const save = () => {
    setTried(true);
    if (bad.size) {
      Haptic.warning();
      return;
    }
    const now = savedAt();
    const fields: Record<string, Entry> = {};
    for (const g of groups)
      for (const f of g.fields) {
        const raw = (values[f.id] ?? '').trim();
        if (!raw) continue;
        const v = f.kind === 'number' || f.kind === 'bp' ? raw.replace(/\s+/g, '') : raw;
        // Unchanged values keep their original trace (who and when).
        const prev = todays?.fields[f.id];
        fields[f.id] = prev && prev.value === v ? prev : { value: v, by: ME_ASHA, at: now, source: g.source };
      }
    store.saveClinical(mother.id, { visit, fields, by: ME_ASHA });
    Haptic.success();
    if (next) router.replace({ pathname: '/asha/summary/[id]', params: { id: mother.id, report: next.report } });
    else goBack({ pathname: '/asha/mother/[id]', params: { id: mother.id } });
  };

  return (
    <Screen
      eyebrow={`${mother.name} · ${t(visit === 1 ? 'visit1' : visit === 2 ? 'visit2' : 'visit3')}`}
      title={t('clinTitle')}
      compactTitle={t('clinTitle')}
      back={
        next
          ? { label: t('clinSkip'), onPress: () => router.replace({ pathname: '/asha/summary/[id]', params: { id: mother.id, report: next.report } }) }
          : { label: t('back'), onPress: () => goBack({ pathname: '/asha/mother/[id]', params: { id: mother.id } }) }
      }
      cta={{ label: next ? t('clinSaveReport') : t('clinSave'), enabled: anything, icon: 'checkCircle', haptic: 'success', context: tried && bad.size ? t('clinInvalid') : undefined, onPress: save }}
    >
      <AppText variant="caption" color={Colors.inkMuted}>
        {t('clinHint')}
      </AppText>
      {!!latest && (
        <AppText variant="caption" color={Colors.inkMuted}>
          {t('clinLast', { name: staff(latest.by)?.name ?? latest.by, when: whenLabel(latest.at, language) })}
        </AppText>
      )}

      {groups.map((g) => (
        <View key={g.id} style={styles.card}>
          <View style={styles.cardHead}>
            <AppText variant="heading" style={styles.flex}>
              {g.title[language]}
            </AppText>
            <AppText variant="caption" color={Colors.inkMuted}>
              {t('clinFrom', { source: t(SOURCE[g.source]) })}
            </AppText>
          </View>
          {g.fields.map((f) => (
            <Row key={f.id} label={f.label[language]} invalid={tried && bad.has(f.id)}>
              <Value kind={f} value={values[f.id] ?? ''} onChange={(v) => setValues((s) => ({ ...s, [f.id]: v }))} lang={language} t={t} label={f.label[language]} />
            </Row>
          ))}
        </View>
      ))}

    </Screen>
  );
}

