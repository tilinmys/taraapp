import { router } from 'expo-router';
import { useState } from 'react';
import { Pressable, StyleSheet, TextInput, View } from 'react-native';

import { DOC } from '@/components/doctor/ui';
import { Icon } from '@/components/system/icon';
import { AppText } from '@/components/ui/app-text';
import { PressButton } from '@/components/ui/press-button';
import { Colors, Haptic } from '@/constants/theme';
import { fontFor } from '@/constants/typography';
import {
  BOOKING_CUTOFF_MS,
  ME_DOCTOR,
  dayStart,
  fmtDay,
  fmtTime,
  fmtWhen,
  isActive,
  latestBooking,
  network,
  sameDay,
  staff,
  useNetwork,
  useNow,
  zone,
  type BookingStatus,
  type Slot,
} from '@/data/care-network';
import { store, type MotherRecord } from '@/data/store';

// What the doctor decides after reading the report, and sends:
//   Doctor's advice   numbered steps he can edit, add, remove; "Send advice"
//                     puts them on the ASHA's page for her and on the
//                     mother's Home, and tells both (notices)
//   Consultation      her visit with its status, and what he can do next:
//                       none / completed / cancelled -> pick a day and a time
//                         from HIS open times; "Request" (her ASHA confirms
//                         with her) or "Confirm appointment" (booked now)
//                       requested -> Confirm now · Reschedule · Cancel
//                       confirmed / rescheduled -> Mark completed ·
//                         Reschedule · Cancel
//                     Every change is told to the ASHA and the mother, and
//                     confirmed here in one line.
// English only: the doctor screens are.

const STATUS: Record<BookingStatus, { label: string; fg: string; bg: string }> = {
  requested: { label: 'Requested', fg: Colors.plum, bg: Colors.lilacTint },
  confirmed: { label: 'Confirmed', fg: Colors.sage, bg: Colors.sageTint },
  rescheduled: { label: 'Rescheduled', fg: Colors.plum, bg: Colors.lilacTint },
  completed: { label: 'Completed', fg: Colors.sage, bg: Colors.sageTint },
  cancelled: { label: 'Cancelled', fg: Colors.cherry, bg: Colors.cherryMist },
};
const MAX_STEPS = 6;

/** The advice steps, editable. The screen owns them (the referral CTA sends them too). */
export function AdviceEditor({ mother, steps, onChange }: { mother: MotherRecord; steps: string[]; onChange: (s: string[]) => void }) {
  const [done, setDone] = useState<string | null>(null);
  const clean = steps.map((x) => x.trim()).filter(Boolean);
  const sentNow = !!mother.adviceAt && JSON.stringify(mother.advice ?? []) === JSON.stringify(clean);
  const asha = staff(zone(mother.zoneId)?.ashaId ?? '')?.name ?? 'her ASHA';
  const first = mother.name.split(' ')[0];

  const send = () => {
    if (!clean.length) {
      Haptic.warning();
      return;
    }
    store.sendAdvice(mother.id, clean, ME_DOCTOR);
    network.adviceSent(mother.id, mother.name, mother.zoneId, ME_DOCTOR, clean.length);
    onChange(clean);
    Haptic.success();
    setDone(`Advice sent to ASHA ${asha} and ${first}.`);
  };

  return (
    <View style={styles.box}>
      <View style={styles.headRow}>
        <AppText variant="bodyStrong" style={styles.flex}>
          {"Doctor's advice"}
        </AppText>
        <AppText variant="caption" color={sentNow ? Colors.sage : Colors.inkMuted}>
          {sentNow && mother.adviceAt ? `Sent ${fmtWhen(mother.adviceAt)}` : mother.adviceAt ? 'Edited, not sent' : 'Not sent yet'}
        </AppText>
      </View>
      <AppText variant="caption" color={Colors.inkMuted}>
        {`Recommendations and instructions. ${first} reads them on her phone; ASHA ${asha} sees them on her page.`}
      </AppText>
      {steps.map((step, i) => (
        <View key={i} style={styles.step}>
          <AppText variant="label" color={Colors.inkMuted} style={styles.stepNum}>
            {`${i + 1}.`}
          </AppText>
          <TextInput
            value={step}
            onChangeText={(v) => onChange(steps.map((x, j) => (j === i ? v : x)))}
            multiline
            maxLength={200}
            placeholder="Write a step, e.g. Come to the PHC tomorrow"
            placeholderTextColor={Colors.inkSubtle}
            selectionColor={Colors.cherry}
            accessibilityLabel={`Advice step ${i + 1}`}
            style={[styles.input, fontFor('input')]}
            maxFontSizeMultiplier={1.4}
          />
          <Pressable
            accessibilityRole="button"
            accessibilityLabel={`Remove step ${i + 1}`}
            hitSlop={8}
            onPress={() => {
              Haptic.light();
              onChange(steps.filter((_, j) => j !== i));
            }}
            style={({ pressed }) => [styles.remove, pressed && styles.pressed]}
          >
            <Icon name="close" size={14} color={Colors.inkMuted} weight="bold" />
          </Pressable>
        </View>
      ))}
      {steps.length < MAX_STEPS && (
        <Pressable
          accessibilityRole="button"
          onPress={() => {
            Haptic.light();
            onChange([...steps, '']);
          }}
          style={({ pressed }) => [styles.addStep, pressed && styles.pressed]}
        >
          <Icon name="plus" size={16} color={Colors.cherry} weight="bold" />
          <AppText variant="buttonSmall" color={Colors.cherry}>
            Add a step
          </AppText>
        </Pressable>
      )}
      <PressButton
        label={sentNow ? 'Advice sent' : mother.adviceAt ? 'Send updated advice' : 'Send advice'}
        disabled={sentNow || !clean.length}
        haptic="success"
        icon={<Icon name="send" size={18} color={Colors.onCherry} />}
        onPress={send}
      />
      {!!done && sentNow && <Confirmed text={done} />}
    </View>
  );
}

/** Her consultation: status, and the doctor's next move. */
export function Consultation({ mother }: { mother: MotherRecord }) {
  const net = useNetwork();
  const now = useNow();
  const b = latestBooking(mother.id, net);
  const slot = b && net.slots.find((s) => s.id === b.slotId);
  const live = !!b && isActive(b) && !!slot && slot.start > now - 2 * 60 * 60_000;
  const [picking, setPicking] = useState<'new' | 'move' | null>(null);
  const [cancelAsk, setCancelAsk] = useState(false);
  const [done, setDone] = useState<string | null>(null);
  const asha = staff(zone(mother.zoneId)?.ashaId ?? '')?.name ?? 'her ASHA';
  const first = mother.name.split(' ')[0];
  const told = `ASHA ${asha} and ${first} were told.`;

  const act = (msg: string) => {
    Haptic.success();
    setDone(`${msg} ${told}`);
    setPicking(null);
    setCancelAsk(false);
  };

  return (
    <View style={styles.box}>
      <View style={styles.headRow}>
        <AppText variant="bodyStrong" style={styles.flex}>
          Consultation
        </AppText>
        {b && slot && (
          <View style={[styles.pill, { backgroundColor: STATUS[b.status].bg }]}>
            <AppText variant="overline" color={STATUS[b.status].fg}>
              {STATUS[b.status].label.toUpperCase()}
            </AppText>
          </View>
        )}
      </View>

      {b && slot ? (
        <View style={styles.visit}>
          <Icon name="calendar" size={20} color={Colors.ink} />
          <View style={styles.flex}>
            <AppText variant="bodyStrong">{fmtWhen(slot.start)}</AppText>
            <AppText variant="caption" color={Colors.inkMuted}>
              {`${staff(slot.doctorId)?.name ?? ''} · ${b.bookedBy === 'doctor' ? 'arranged by you' : b.bookedBy === 'mother' ? 'booked by her' : `booked by ASHA ${asha}`}`}
            </AppText>
          </View>
        </View>
      ) : (
        <AppText variant="caption" color={Colors.inkMuted}>
          No consultation yet. Pick one of your open times below.
        </AppText>
      )}

      {live && b && !picking && !cancelAsk && (
        <View style={styles.actions}>
          {b.status === 'requested' && <Small label="Confirm now" primary onPress={() => {
                network.confirmBooking(b.id, mother.name);
                act('Consultation confirmed.');
              }} />}
          {b.status !== 'requested' && <Small label="Mark completed" primary onPress={() => {
                network.completeBooking(b.id, mother.name);
                act('Marked completed.');
              }} />}
          <Small label="Reschedule" onPress={() => setPicking('move')} />
          <Small label="Cancel" danger onPress={() => setCancelAsk(true)} />
        </View>
      )}
      {cancelAsk && b && (
        <View style={styles.ask}>
          <AppText variant="bodyStrong" color={Colors.cherry}>
            Cancel this consultation?
          </AppText>
          <AppText variant="caption" color={Colors.ink}>
            The time opens again for others. Her ASHA and she are told.
          </AppText>
          <View style={styles.actions}>
            <Small label="Yes, cancel" danger primary onPress={() => {
                network.cancelBooking(b.id, 'doctor', mother.name);
                act('Consultation cancelled.');
              }} />
            <Small label="Keep it" onPress={() => setCancelAsk(false)} />
          </View>
        </View>
      )}

      {(picking || !live) && (
        <SlotPicker
          key={picking ?? 'new'}
          mode={picking === 'move' ? 'move' : 'new'}
          exclude={slot?.id}
          onCancel={picking ? () => setPicking(null) : undefined}
          onPick={(slotId, status, note) => {
            if (picking === 'move' && b) {
              const r = network.rescheduleBooking(b.id, slotId, mother.name);
              if (r.ok) act('Moved to the new time.');
              else Haptic.warning();
              return;
            }
            const r = network.doctorSchedule(slotId, mother, ME_DOCTOR, status, note);
            if (r.ok) act(status === 'requested' ? `Consultation requested. ASHA ${asha} will confirm it with ${first}.` : 'Appointment confirmed.');
            else Haptic.warning();
          }}
        />
      )}
      {!!done && <Confirmed text={done} />}
    </View>
  );
}

/** Day chips, then time chips, from the doctor's own open times. */
function SlotPicker({ mode, exclude, onPick, onCancel }: { mode: 'new' | 'move'; exclude?: string; onPick: (slotId: string, status: 'requested' | 'confirmed', note?: string) => void; onCancel?: () => void }) {
  const net = useNetwork();
  const now = useNow();
  const open = net.slots.filter((s: Slot) => s.doctorId === ME_DOCTOR && s.status === 'open' && s.id !== exclude && s.start > now + BOOKING_CUTOFF_MS);
  const days = [...new Set(open.map((s) => dayStart(0, s.start)))].sort((a, b) => a - b);
  const [day, setDay] = useState<number | null>(days[0] ?? null);
  const [slotId, setSlotId] = useState<string | null>(null);
  const [note, setNote] = useState('');
  const times = day === null ? [] : open.filter((s) => sameDay(s.start, day)).sort((a, b) => a.start - b.start);

  if (!days.length) {
    return (
      <View style={styles.ask}>
        <AppText variant="caption" color={Colors.ink}>
          You have no open times in the next days.
        </AppText>
        <Small label="Open visit times" onPress={() => router.push('/doctor/slots')} />
      </View>
    );
  }
  return (
    <View style={styles.picker}>
      <AppText variant="overline" color={Colors.inkMuted}>
        {mode === 'move' ? 'MOVE TO' : 'AVAILABLE DATES'}
      </AppText>
      <View style={styles.chips}>
        {days.map((d) => (
          <Chip key={d} label={fmtDay(d)} on={day === d} onPress={() => {
              setDay(d);
              setSlotId(null);
            }} />
        ))}
      </View>
      <AppText variant="overline" color={Colors.inkMuted}>
        TIMES
      </AppText>
      <View style={styles.chips}>
        {times.map((s) => (
          <Chip key={s.id} label={fmtTime(s.start)} on={slotId === s.id} onPress={() => setSlotId(s.id)} />
        ))}
      </View>
      {mode === 'new' && (
        <TextInput
          value={note}
          onChangeText={setNote}
          placeholder="Note for the ASHA (optional)"
          placeholderTextColor={Colors.inkSubtle}
          selectionColor={Colors.cherry}
          maxLength={200}
          accessibilityLabel="Note for the ASHA"
          style={[styles.input, styles.noteInput, fontFor('input')]}
          maxFontSizeMultiplier={1.4}
        />
      )}
      <View style={styles.actions}>
        {mode === 'new' ? (
          <>
            <Small label="Request" disabled={!slotId} onPress={() => slotId && onPick(slotId, 'requested', note)} />
            <Small label="Confirm appointment" primary disabled={!slotId} onPress={() => slotId && onPick(slotId, 'confirmed', note)} />
          </>
        ) : (
          <Small label="Move visit here" primary disabled={!slotId} onPress={() => slotId && onPick(slotId, 'confirmed')} />
        )}
        {onCancel && <Small label="Back" onPress={onCancel} />}
      </View>
      <AppText variant="caption" color={Colors.inkMuted}>
        {mode === 'new' ? 'Request: her ASHA confirms the time with her. Confirm: it is booked now.' : 'The old time opens again for others.'}
      </AppText>
    </View>
  );
}

function Chip({ label, on, onPress }: { label: string; on: boolean; onPress: () => void }) {
  return (
    <Pressable
      accessibilityRole="radio"
      accessibilityState={{ checked: on }}
      aria-checked={on}
      accessibilityLabel={label}
      onPress={() => {
        Haptic.light();
        onPress();
      }}
      style={({ pressed }) => [styles.chip, on && styles.chipOn, pressed && styles.pressed]}
    >
      <AppText variant="label" color={on ? Colors.onCherry : Colors.ink}>
        {label}
      </AppText>
    </Pressable>
  );
}

function Small({ label, onPress, primary, danger, disabled }: { label: string; onPress: () => void; primary?: boolean; danger?: boolean; disabled?: boolean }) {
  const fg = primary ? Colors.onCherry : danger ? Colors.cherry : Colors.ink;
  return (
    <Pressable
      accessibilityRole="button"
      accessibilityState={{ disabled: !!disabled }}
      disabled={disabled}
      onPress={onPress}
      style={({ pressed }) => [styles.small, primary && (danger ? styles.smallDanger : styles.smallPrimary), !primary && danger && styles.smallDangerLine, disabled && styles.disabled, pressed && styles.pressed]}
    >
      <AppText variant="buttonSmall" color={fg} numberOfLines={1}>
        {label}
      </AppText>
    </Pressable>
  );
}

function Confirmed({ text }: { text: string }) {
  return (
    <View style={styles.done} accessibilityLiveRegion="polite">
      <Icon name="checkCircle" size={18} color={Colors.sage} weight="fill" />
      <AppText variant="caption" color={Colors.sage} style={styles.flex}>
        {text}
      </AppText>
    </View>
  );
}

const styles = StyleSheet.create({
  flex: { flex: 1, minWidth: 0 },
  pressed: { opacity: 0.6 },
  disabled: { opacity: 0.4 },
  box: { gap: 10, padding: 14, borderRadius: DOC.radius, borderWidth: 1, borderColor: DOC.border, backgroundColor: Colors.surface },
  headRow: { flexDirection: 'row', alignItems: 'center', gap: 8 },
  step: { flexDirection: 'row', alignItems: 'flex-start', gap: 8 },
  stepNum: { width: 22, paddingTop: 14 },
  input: { flex: 1, minHeight: 48, paddingHorizontal: 12, paddingVertical: 10, borderRadius: 10, borderWidth: 1.5, borderColor: Colors.outline, color: Colors.ink, backgroundColor: Colors.surface, textAlignVertical: 'top' },
  noteInput: { flex: 0, alignSelf: 'stretch' },
  remove: { width: 32, height: 32, marginTop: 8, borderRadius: 16, alignItems: 'center', justifyContent: 'center', backgroundColor: Colors.shell },
  addStep: { minHeight: 44, flexDirection: 'row', alignItems: 'center', gap: 6, alignSelf: 'flex-start', paddingHorizontal: 4 },
  pill: { paddingHorizontal: 8, paddingVertical: 3, borderRadius: 4 },
  visit: { flexDirection: 'row', alignItems: 'center', gap: 10, padding: 12, borderRadius: DOC.radius, backgroundColor: Colors.shell },
  actions: { flexDirection: 'row', flexWrap: 'wrap', gap: 8 },
  ask: { gap: 8, padding: 12, borderRadius: DOC.radius, backgroundColor: Colors.cherryMist },
  picker: { gap: 8 },
  chips: { flexDirection: 'row', flexWrap: 'wrap', gap: 8 },
  chip: { minHeight: 40, justifyContent: 'center', paddingHorizontal: 14, borderRadius: 20, borderWidth: 1.5, borderColor: Colors.bandLine, backgroundColor: Colors.surface },
  chipOn: { backgroundColor: Colors.cherry, borderColor: Colors.cherry },
  small: { minHeight: 44, flexGrow: 1, alignItems: 'center', justifyContent: 'center', paddingHorizontal: 14, borderRadius: 22, borderWidth: 1.5, borderColor: Colors.outline, backgroundColor: Colors.surface },
  smallPrimary: { backgroundColor: Colors.cherry, borderColor: Colors.cherry },
  smallDanger: { backgroundColor: Colors.cherry, borderColor: Colors.cherry },
  smallDangerLine: { borderColor: Colors.cherry },
  done: { flexDirection: 'row', alignItems: 'center', gap: 8, padding: 10, borderRadius: DOC.radius, backgroundColor: Colors.sageTint },
});
