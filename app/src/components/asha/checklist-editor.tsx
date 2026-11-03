import { useState } from 'react';
import { Pressable, StyleSheet, TextInput, View } from 'react-native';
import Animated, { FadeIn, FadeOut, LinearTransition, useReducedMotion, type SharedValue } from 'react-native-reanimated';

import { AppIcon } from '@/components/system/app-icon';
import { Icon } from '@/components/system/icon';
import { AppText } from '@/components/ui/app-text';
import { BottomSheet } from '@/components/ui/bottom-sheet';
import { PressButton } from '@/components/ui/press-button';
import { Colors, Haptic } from '@/constants/theme';
import { fontFor } from '@/constants/typography';
import { carePlanStore, iconFor, MAX_PLAN_ITEMS, PLAN_PRESETS, useCarePlan, type PlanItem } from '@/data/care-plan';
import { ME_ASHA, network } from '@/data/care-network';
import { useT } from '@/features/onboarding/context';
import { planLabel } from '@/lib/plan-label';

// The ASHA sets a mother's daily checklist, in its own bottom sheet.
//   1 Her tasks      the current list; x removes a task
//   2 Add your own   free text; an icon is picked from the words
//   3 Quick add      one-tap chips for common tasks not yet on the list
// Save is pinned at the bottom of the sheet (always in view, however long
// the list). Nothing reaches the mother until Save; then it shows on her
// Home and the page confirms it (onSaved).

type SheetProps = {
  open: boolean;
  progress: SharedValue<number>;
  motherId: string;
  motherName: string;
  onClose: () => void;
  onSaved: () => void;
};

export function ChecklistSheet({ open, progress, motherId, motherName, onClose, onSaved }: SheetProps) {
  const t = useT();
  const saved = useCarePlan(motherId);
  const [items, setItems] = useState<PlanItem[]>(saved);
  // Every opening starts from what is saved (an unsaved edit is dropped).
  const [wasOpen, setWasOpen] = useState(open);
  if (open !== wasOpen) {
    setWasOpen(open);
    if (open) setItems(saved);
  }
  const changed = items.length !== saved.length || items.some((it, i) => it.id !== saved[i]?.id);
  const first = motherName.split(' ')[0];
  return (
    <BottomSheet
      open={open}
      onClose={onClose}
      progress={progress}
      title={t('dailyChecklist')}
      closeLabel={t('back')}
      footer={
        <PressButton
          label={changed ? `Save for ${first}` : 'Saved'}
          disabled={!changed}
          onPress={() => {
            carePlanStore.set(motherId, items);
            network.tellMother(motherId, 'checklist_updated', ME_ASHA);
            Haptic.success();
            onSaved();
          }}
        />
      }
    >
      {open && <ChecklistEditor motherName={motherName} items={items} setItems={setItems} />}
    </BottomSheet>
  );
}

function ChecklistEditor({ motherName, items, setItems }: { motherName: string; items: PlanItem[]; setItems: (f: (xs: PlanItem[]) => PlanItem[]) => void }) {
  const reduceMotion = useReducedMotion();
  const t = useT();
  const [text, setText] = useState('');
  const full = items.length >= MAX_PLAN_ITEMS;
  const quick = PLAN_PRESETS.filter((p) => !items.some((it) => it.id === p.id));
  const first = motherName.split(' ')[0];

  const add = (item: PlanItem) => {
    if (full || items.some((it) => it.label.toLowerCase() === item.label.toLowerCase())) return;
    Haptic.light();
    setItems((xs) => [...xs, item]);
  };
  const addTyped = () => {
    const label = text.trim().replace(/\s+/g, ' ');
    if (!label) return;
    add({ id: `own-${label.toLowerCase().replace(/[^a-z0-9]+/g, '-')}`, label, icon: iconFor(label) });
    setText('');
  };
  const layout = reduceMotion ? undefined : LinearTransition.springify().stiffness(250).damping(25);

  return (
    <View style={styles.wrap}>
      <AppText variant="caption" color={Colors.inkMuted}>
        {`${first} sees these every day on her Home and ticks them off.`}
      </AppText>

      <View style={styles.list}>
        {items.length === 0 && (
          <AppText variant="caption" color={Colors.inkMuted} style={styles.empty}>
            No tasks yet. Add one below.
          </AppText>
        )}
        {items.map((it) => (
          <Animated.View key={it.id} layout={layout} entering={reduceMotion ? undefined : FadeIn.duration(180)} exiting={reduceMotion ? undefined : FadeOut.duration(140)} style={styles.item}>
            <AppIcon name={it.icon} size={30} />
            <AppText variant="label" style={styles.flex} numberOfLines={2}>
              {planLabel(it, t)}
            </AppText>
            <Pressable
              accessibilityRole="button"
              accessibilityLabel={`Remove ${it.label}`}
              hitSlop={8}
              onPress={() => {
                Haptic.light();
                setItems((xs) => xs.filter((x) => x.id !== it.id));
              }}
              style={({ pressed }) => [styles.remove, pressed && styles.pressed]}
            >
              <Icon name="close" size={14} color={Colors.inkMuted} weight="bold" />
            </Pressable>
          </Animated.View>
        ))}
      </View>

      <View style={styles.composer}>
        <TextInput
          value={text}
          onChangeText={setText}
          placeholder={full ? `Up to ${MAX_PLAN_ITEMS} tasks` : 'Add your own task'}
          placeholderTextColor={Colors.inkSubtle}
          selectionColor={Colors.cherry}
          editable={!full}
          maxLength={48}
          returnKeyType="done"
          onSubmitEditing={addTyped}
          accessibilityLabel="New task for the mother"
          style={[styles.input, fontFor('input')]}
          maxFontSizeMultiplier={1.4}
        />
        <Pressable
          accessibilityRole="button"
          accessibilityLabel="Add task"
          disabled={!text.trim() || full}
          onPress={addTyped}
          style={({ pressed }) => [styles.addBtn, (!text.trim() || full) && styles.disabled, pressed && styles.pressed]}
        >
          <Icon name="plus" size={20} color={Colors.onCherry} weight="bold" />
        </Pressable>
      </View>

      {quick.length > 0 && !full && (
        <View style={styles.quickWrap}>
          <AppText variant="overline" color={Colors.inkMuted}>
            QUICK ADD
          </AppText>
          <View style={styles.chips}>
            {quick.map((p) => (
              <Pressable key={p.id} accessibilityRole="button" accessibilityLabel={`Add ${p.label}`} onPress={() => add(p)} style={({ pressed }) => [styles.chip, pressed && styles.pressed]}>
                <AppIcon name={p.icon} size={20} />
                <AppText variant="caption" color={Colors.ink}>
                  {planLabel(p, t)}
                </AppText>
              </Pressable>
            ))}
          </View>
        </View>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  flex: { flex: 1, minWidth: 0 },
  pressed: { opacity: 0.7 },
  disabled: { opacity: 0.4 },
  wrap: { gap: 14, paddingBottom: 8 },
  list: { gap: 8 },
  empty: { paddingVertical: 8 },
  item: { minHeight: 56, flexDirection: 'row', alignItems: 'center', gap: 12, paddingHorizontal: 12, borderRadius: 16, backgroundColor: Colors.shell },
  remove: { width: 30, height: 30, borderRadius: 15, alignItems: 'center', justifyContent: 'center', backgroundColor: Colors.surface },
  composer: { flexDirection: 'row', alignItems: 'center', gap: 8 },
  input: {
    flex: 1,
    minHeight: 50,
    paddingHorizontal: 16,
    borderRadius: 25,
    borderWidth: 1.5,
    borderColor: Colors.outline,
    color: Colors.ink,
    backgroundColor: Colors.surface,
  },
  addBtn: { width: 50, height: 50, borderRadius: 25, alignItems: 'center', justifyContent: 'center', backgroundColor: Colors.cherry },
  quickWrap: { gap: 8 },
  chips: { flexDirection: 'row', flexWrap: 'wrap', gap: 8 },
  chip: {
    minHeight: 40,
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
    paddingHorizontal: 12,
    borderRadius: 20,
    borderWidth: 1.5,
    borderColor: Colors.blush,
    backgroundColor: Colors.surface,
  },
});
