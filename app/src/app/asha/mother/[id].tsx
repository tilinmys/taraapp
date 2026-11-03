import { router, useLocalSearchParams } from 'expo-router';
import { useState } from 'react';
import { Linking, Pressable, StyleSheet, View } from 'react-native';
import { useSharedValue } from 'react-native-reanimated';

import { goBack } from '@/lib/nav';
import { ChecklistSheet } from '@/components/asha/checklist-editor';
import { ConditionPicker } from '@/components/asha/condition-picker';
import { TellDoctorSheet } from '@/components/asha/tell-doctor';
import { VisitThread } from '@/components/care/visit-thread';
import { AppIcon } from '@/components/system/app-icon';
import { Icon } from '@/components/system/icon';
import { NavCard } from '@/components/system/nav-card';
import { Screen } from '@/components/system/screen';
import { StateView } from '@/components/system/state-view';
import { AppText } from '@/components/ui/app-text';
import { BottomSheet } from '@/components/ui/bottom-sheet';
import { PressButton } from '@/components/ui/press-button';
import { Colors, Haptic } from '@/constants/theme';
import { useCarePlan } from '@/data/care-plan';
import { ME_ASHA, network, slotOf, staff, upcomingBooking, useNetwork, zone, zoneName } from '@/data/care-network';
import { useMother, type MotherRecord } from '@/data/store';
import { useOnboarding, useT } from '@/features/onboarding/context';
import { LANGUAGES } from '@/i18n/strings';
import { STATUS_PILL } from '@/lib/appt-status';
import { relLabel, whenLabel } from '@/lib/day-label';
import { planLabel } from '@/lib/plan-label';

// ONE woman, everything the ASHA does for her, top to bottom in the order
// she does it:
//   1 How is she?       the condition dropdown (Needs care -> "Tell the
//                       doctor?" with a required "What happened?"); once
//                       told, her note stays under it
//   2 Talk with Sakhi   the one place a check-in starts (big card)
//   3 Doctor visit      booked: when + its status (Requested / Confirmed /
//                       Rescheduled) + messages; a doctor's request is
//                       confirmed here. Not booked: "Book a doctor visit"
//   4 Doctor's advice   what the doctor sent after reading her report
//   5 Daily checklist   what she ticks on her phone; "Change" opens the editor
//   6 Her details       months, language, village, phone; "Edit" opens the
//                       details form (sticky Save); "Details saved" shows here
//   7 Last report, Call her
// Every save confirms itself in ONE note at the top. Back returns to where
// the ASHA came from.

type Sheet = 'checklist' | 'thread' | null;

export default function AshaMotherPage() {
  const { id, added, saved } = useLocalSearchParams<{ id: string; added?: string; saved?: string }>();
  const mother = useMother(id);
  const t = useT();
  if (!mother) {
    return (
      <Screen title="" back={{ label: t('back'), onPress: () => goBack('/asha') }}>
        <StateView title="Not found" body="This woman is not in your villages." action={{ label: t('back'), onPress: () => goBack('/asha') }} />
      </Screen>
    );
  }
  return <Page mother={mother} added={added === '1'} saved={saved} />;
}

function Page({ mother, added, saved }: { mother: MotherRecord; added: boolean; saved?: string }) {
  const t = useT();
  const { language } = useOnboarding();
  const net = useNetwork();
  const plan = useCarePlan(mother.id);
  // One progress per sheet: a shared one would slide every sheet up at once.
  const sheet = useSharedValue(0);
  const thread = useSharedValue(0);
  const confirm = useSharedValue(0);
  const [open, setOpen] = useState<Sheet>(null);
  const [asking, setAsking] = useState<MotherRecord | null>(null);
  const [note, setNote] = useState<string | null>(saved ? t('detailsSaved') : added ? t('addedNext', { name: mother.name.split(' ')[0] }) : null);
  const first = mother.name.split(' ')[0];
  // Back from the details form with a fresh save (a new `saved` stamp):
  // confirm it here. Adjusted while rendering, not in an effect.
  const [seenSave, setSeenSave] = useState(saved);
  if (saved !== seenSave) {
    setSeenSave(saved);
    if (saved) setNote(t('detailsSaved'));
  }
  // The page comes back scrolled to where she tapped Edit: the card itself
  // also says it was saved, so the confirmation is where her eyes are.
  const justSaved = !!saved && note === t('detailsSaved');
  const z = zone(mother.zoneId);
  const b = upcomingBooking(mother.id, net);
  const slot = b && slotOf(b);
  const doctor = slot && staff(slot.doctorId);
  const lang = LANGUAGES.find((l) => l.id === mother.language);
  const back = () => goBack('/asha');
  const pill = b ? STATUS_PILL[b.status] : null;

  return (
    <Screen
      band
      eyebrow={z ? zoneName(z, language) : ''}
      title={mother.name}
      compactTitle={mother.name}
      back={{ label: t('back'), onPress: back }}
      sheetProgress={open === 'thread' ? thread : open ? sheet : confirm}
      accessory={
        <AppText variant="bodyStrong" color={Colors.inkMuted}>
          {[t('monthsShort', { n: mother.months }), lang?.native].filter(Boolean).join(' · ')}
        </AppText>
      }
      overlay={
        <>
          <ChecklistSheet
            open={open === 'checklist'}
            progress={sheet}
            motherId={mother.id}
            motherName={mother.name}
            onClose={() => setOpen(null)}
            onSaved={() => {
              setOpen(null);
              setNote(t('checklistSaved', { name: first }));
            }}
          />
          <BottomSheet open={open === 'thread'} onClose={() => setOpen(null)} progress={thread} title={t('visitMessages')} closeLabel={t('back')}>
            {open === 'thread' && b && <VisitThread bookingId={b.id} me="asha" myId={ME_ASHA} motherName={mother.name} />}
          </BottomSheet>
          <TellDoctorSheet
            mother={asking}
            progress={confirm}
            onClose={(names) => {
              if (names) setNote(t('doctorTold', { names: names.join(', ') }));
              setAsking(null);
            }}
          />
        </>
      }
    >
      {!!note && (
        <View style={styles.note} accessibilityLiveRegion="polite">
          <Icon name="checkCircle" size={20} color={Colors.sage} weight="fill" />
          <AppText variant="bodyStrong" color={Colors.sage} style={styles.flex}>
            {note}
          </AppText>
          <Pressable accessibilityRole="button" accessibilityLabel={t('back')} hitSlop={10} onPress={() => setNote(null)}>
            <Icon name="close" size={16} color={Colors.sage} weight="bold" />
          </Pressable>
        </View>
      )}

      {/* 1 How is she? (+ what she told the doctor) */}
      <View style={styles.card}>
        <ConditionPicker mother={mother} onNeedsCare={(m) => setAsking(m)} />
        {!!mother.escalation && mother.ashaFlag === 'needs_care' && (
          <View style={styles.told}>
            <Icon name="send" size={16} color={Colors.cherry} />
            <AppText variant="caption" color={Colors.ink} style={styles.flex}>
              {`${t('tellDoctorWhat')} “${mother.escalation.note}” · ${whenLabel(mother.escalation.at, language)}`}
            </AppText>
          </View>
        )}
      </View>

      {/* 2 Talk with Sakhi: the only place a check-in starts */}
      <Pressable
        accessibilityRole="button"
        accessibilityLabel={`${t('talkSakhi')}. ${t('talkSakhiHint')}`}
        onPress={() => {
          Haptic.medium();
          router.push({ pathname: '/asha/session/[id]', params: { id: mother.id } });
        }}
        style={({ pressed }) => [styles.sakhi, pressed && styles.pressed]}
      >
        <View style={styles.sakhiWell}>
          <AppIcon name="voice" size={44} />
        </View>
        <View style={styles.flex}>
          <AppText variant="heading" color={Colors.onCherry}>
            {t('talkSakhi')}
          </AppText>
          <AppText variant="caption" color={Colors.onCherry}>
            {t('talkSakhiHint')}
          </AppText>
        </View>
        <View style={styles.go}>
          <Icon name="arrow" size={22} color={Colors.cherry} weight="bold" />
        </View>
      </Pressable>

      {/* 3 Doctor visit, with its status */}
      {slot && b ? (
        <View style={styles.visit}>
          <NavCard
            icon="calendar"
            title={whenLabel(slot.start, language)}
            line={`${doctor?.name ?? ''}\n${b.comments.length ? t('messagesCount', { n: b.comments.length }) : t('openMessages')}`}
            status={pill ? { label: t(pill.key), tone: pill.tone } : undefined}
            onPress={() => setOpen('thread')}
          />
          {b.status === 'requested' && (
            <View style={styles.request}>
              <AppText variant="caption" color={Colors.plum}>
                {t('apptRequestedHint', { doctor: doctor?.name ?? '', name: first })}
              </AppText>
              <PressButton
                label={t('apptConfirmBtn')}
                haptic="success"
                onPress={() => {
                  network.confirmBooking(b.id, mother.name);
                  setNote(t('aApptConfirmedT', { mother: first }));
                }}
              />
            </View>
          )}
        </View>
      ) : (
        <NavCard
          icon="calendar"
          title={t('bookForHer')}
          line={t('notBooked')}
          onPress={() => router.push({ pathname: '/asha/visits', params: { mother: mother.id } })}
        />
      )}

      {/* 4 Doctor's advice (once sent) */}
      {!!mother.advice?.length && !!mother.adviceAt && (
        <View style={styles.card}>
          <View style={styles.head}>
            <AppIcon name="doctor" size={36} />
            <View style={styles.flex}>
              <AppText variant="heading">{t('doctorAdvice')}</AppText>
              <AppText variant="caption" color={Colors.inkMuted}>
                {t('adviceFrom', { doctor: staff(mother.adviceBy ?? '')?.name ?? '', when: whenLabel(mother.adviceAt, language) })}
              </AppText>
            </View>
          </View>
          {mother.advice.map((a, i) => (
            <View key={`${i}-${a}`} style={[styles.task, i > 0 && styles.taskLine]}>
              <AppText variant="label" color={Colors.inkMuted} style={styles.num}>
                {`${i + 1}.`}
              </AppText>
              <AppText variant="body" style={styles.flex}>
                {a}
              </AppText>
            </View>
          ))}
        </View>
      )}

      {/* 5 Daily checklist */}
      <View style={styles.card}>
        <View style={styles.head}>
          <AppIcon name="checklist" size={36} />
          <View style={styles.flex}>
            <AppText variant="heading">{t('dailyChecklist')}</AppText>
            <AppText variant="caption" color={Colors.inkMuted}>
              {t('checklistHint', { name: first })}
            </AppText>
          </View>
          <SmallButton label={t('change')} a11y={`${t('change')} ${t('dailyChecklist')}`} onPress={() => setOpen('checklist')} />
        </View>
        {plan.map((it, i) => (
          <View key={it.id} style={[styles.task, i > 0 && styles.taskLine]}>
            <View style={styles.taskWell}>
              <AppIcon name={it.icon} size={26} />
            </View>
            <AppText variant="body" style={styles.flex}>
              {planLabel(it, t)}
            </AppText>
          </View>
        ))}
      </View>

      {/* 6 Her details: what the ASHA entered; Edit opens the form */}
      <View style={styles.card}>
        <View style={styles.head}>
          <AppIcon name="mother" size={36} />
          <AppText variant="heading" style={styles.flex}>
            {t('herDetails')}
          </AppText>
          <SmallButton
            label={t('editDetails')}
            a11y={`${t('editDetails')} ${t('herDetails')}`}
            onPress={() => router.push({ pathname: '/asha/edit/[id]', params: { id: mother.id } })}
          />
        </View>
        {justSaved && (
          <View style={styles.savedLine} accessibilityLiveRegion="polite">
            <Icon name="checkCircle" size={16} color={Colors.sage} weight="fill" />
            <AppText variant="caption" color={Colors.sage} style={styles.flex}>
              {t('detailsSaved')}
            </AppText>
          </View>
        )}
        <Detail label={t('addMonths')} value={`${t('monthsShort', { n: mother.months })} · ${mother.weeksLabel}`} />
        <Detail label={t('detailsLanguage')} value={lang ? (lang.english === lang.native ? lang.native : `${lang.native} · ${lang.english}`) : '-'} />
        <Detail label={t('detailsVillage')} value={z ? zoneName(z, language) : '-'} />
        <Detail label={t('detailsPhone')} value={mother.phone ? `+91 ${mother.phone}` : t('phoneNone')} muted={!mother.phone} />
      </View>

      {/* 7 Last report, call her */}
      {mother.reports[0] && (
        <NavCard
          icon="report"
          title={t('lastReport')}
          line={relLabel(mother.lastCheck, language)}
          onPress={() => router.push({ pathname: '/asha/summary/[id]', params: { id: mother.id, report: mother.reports[0].id } })}
        />
      )}
      {!!mother.phone && (
        <NavCard
          icon="mother"
          title={t('callHer', { name: first })}
          line={`+91 ${mother.phone}`}
          onPress={() => {
            Haptic.medium();
            Linking.openURL(`tel:+91${mother.phone}`).catch(() => {});
          }}
        />
      )}
    </Screen>
  );
}

function SmallButton({ label, a11y, onPress }: { label: string; a11y: string; onPress: () => void }) {
  return (
    <Pressable
      accessibilityRole="button"
      accessibilityLabel={a11y}
      onPress={() => {
        Haptic.light();
        onPress();
      }}
      style={({ pressed }) => [styles.change, pressed && styles.pressed]}
    >
      <AppText variant="buttonSmall" color={Colors.cherry}>
        {label}
      </AppText>
    </Pressable>
  );
}

function Detail({ label, value, muted }: { label: string; value: string; muted?: boolean }) {
  return (
    <View style={[styles.task, styles.taskLine]} accessible accessibilityLabel={`${label}: ${value}`}>
      <AppText variant="caption" color={Colors.inkMuted} style={styles.detailLabel}>
        {label}
      </AppText>
      <AppText variant="bodyStrong" color={muted ? Colors.inkMuted : Colors.ink} style={styles.flex}>
        {value}
      </AppText>
    </View>
  );
}

const styles = StyleSheet.create({
  flex: { flex: 1, minWidth: 0 },
  pressed: { opacity: 0.75 },
  note: { flexDirection: 'row', alignItems: 'center', gap: 8, padding: 12, borderRadius: 14, backgroundColor: Colors.sageTint },
  card: { gap: 10, padding: 14, borderRadius: 20, borderWidth: 1, borderColor: Colors.bandLine, backgroundColor: Colors.surface },
  told: { flexDirection: 'row', alignItems: 'flex-start', gap: 8, padding: 10, borderRadius: 12, backgroundColor: Colors.cherryMist },
  sakhi: { minHeight: 92, flexDirection: 'row', alignItems: 'center', gap: 14, padding: 16, borderRadius: 22, backgroundColor: Colors.cherry },
  sakhiWell: { width: 64, height: 64, borderRadius: 20, alignItems: 'center', justifyContent: 'center', backgroundColor: Colors.surface },
  go: { width: 44, height: 44, borderRadius: 22, alignItems: 'center', justifyContent: 'center', backgroundColor: Colors.surface },
  visit: { gap: 8 },
  request: { gap: 10, padding: 14, borderRadius: 16, backgroundColor: Colors.lilacTint },
  head: { flexDirection: 'row', alignItems: 'center', gap: 12 },
  change: { minHeight: 40, justifyContent: 'center', paddingHorizontal: 14, borderRadius: 20, borderWidth: 1.5, borderColor: Colors.cherry },
  task: { minHeight: 48, flexDirection: 'row', alignItems: 'center', gap: 12 },
  taskLine: { borderTopWidth: 1, borderTopColor: Colors.hairline, paddingTop: 8 },
  taskWell: { width: 40, height: 40, borderRadius: 12, alignItems: 'center', justifyContent: 'center', backgroundColor: Colors.shell },
  num: { width: 22 },
  detailLabel: { width: 108 },
  savedLine: { flexDirection: 'row', alignItems: 'center', gap: 6, paddingHorizontal: 10, paddingVertical: 8, borderRadius: 10, backgroundColor: Colors.sageTint },
});
