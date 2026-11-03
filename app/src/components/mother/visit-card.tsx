import { memo, useEffect, useState } from 'react';
import { Pressable, StyleSheet, View } from 'react-native';
import Animated, {
  FadeInDown,
  LinearTransition,
  useAnimatedStyle,
  useReducedMotion,
  useSharedValue,
  withSpring,
} from 'react-native-reanimated';

import { TONES, type EmblemTone } from '@/components/system/emblem';
import { Rise } from '@/components/system/rise';
import { Icon } from '@/components/system/icon';
import { AppText } from '@/components/ui/app-text';
import { Brand } from '@/constants/brand';
import { Haptic } from '@/lib/haptics';

// One visit in My history, compact. A mother needs four things at a glance:
//   what it was, who and where, when, and when to come back. Plus the paper.
//   Row 1   fixed date tile (day + month) on the visit's tint | title +
//           "doctor · place" (no icons: the date is the anchor)
//   Row 2   "Next visit" pill (only if booked)         | Download button
// Readings (BP, Hb, sugar) and medicine details are never on the card: they
// live inside the PDF, so the list stays calm and private on a shared phone.
// One file downloads straight away; several open a small list under the card.

export type VisitDoc = {
  id: string;
  title: string;
  meta: string;
  kind: 'prescription' | 'report';
  share: () => Promise<void>;
};

type Props = {
  title: string;
  who: string;
  day: string;
  month: string;
  tone: EmblemTone;
  next?: string;
  docs: VisitDoc[];
  index: number;
};

const SPRING = { stiffness: 250, damping: 25 };
type DlState = 'idle' | 'busy' | 'done' | 'error';

function useDownload(share: () => Promise<void>) {
  const [state, setState] = useState<DlState>('idle');
  const run = async () => {
    Haptic.medium();
    setState('busy');
    try {
      await share();
      Haptic.success();
      setState('done');
    } catch {
      Haptic.warning();
      setState('error');
    }
  };
  return [state, run] as const;
}

const dlLabel = (s: DlState, idle: string) => (s === 'busy' ? 'Preparing…' : s === 'done' ? 'Saved' : s === 'error' ? 'Retry' : idle);

function DownloadPill({ state, label, onPress, chevron }: { state: DlState; label: string; onPress: () => void; chevron?: object }) {
  return (
    <Pressable
      accessibilityRole="button"
      accessibilityLabel={label}
      disabled={state === 'busy'}
      onPress={onPress}
      hitSlop={6}
      style={({ pressed }) => [styles.dl, state === 'done' && styles.dlDone, pressed && styles.pressed]}
    >
      <Icon name={state === 'done' ? 'check' : 'download'} size={15} color={Brand.onCherry} weight="bold" />
      <AppText variant="buttonSmall" color={Brand.onCherry}>
        {dlLabel(state, chevron ? 'Papers' : 'PDF')}
      </AppText>
      {chevron && (
        <Animated.View style={chevron}>
          <Icon name="down" size={13} color={Brand.onCherry} weight="bold" />
        </Animated.View>
      )}
    </Pressable>
  );
}

function DocRow({ doc }: { doc: VisitDoc }) {
  const [state, run] = useDownload(doc.share);
  return (
    <View style={styles.doc}>
      <View style={styles.flex}>
        <AppText variant="label">{doc.title}</AppText>
        <AppText variant="caption" color={Brand.inkMuted}>
          {doc.meta}
        </AppText>
      </View>
      <DownloadPill state={state} label={`Download ${doc.title}`} onPress={run} />
    </View>
  );
}

export const VisitCard = memo(function VisitCard({ title, who, day, month, tone, next, docs, index }: Props) {
  const reduceMotion = useReducedMotion();
  const [open, setOpen] = useState(false);
  const single = docs.length === 1 ? docs[0] : null;
  const [state, run] = useDownload(single?.share ?? (async () => {}));
  const flip = useSharedValue(0);
  useEffect(() => {
    flip.set(reduceMotion ? (open ? 1 : 0) : withSpring(open ? 1 : 0, SPRING));
  }, [flip, open, reduceMotion]);
  const chevron = useAnimatedStyle(() => ({ transform: [{ rotate: `${flip.get() * 180}deg` }] }));
  const t = TONES[tone];

  return (
    <Rise index={index}>
    <Animated.View
      layout={reduceMotion ? undefined : LinearTransition.springify().stiffness(250).damping(25)}
      style={styles.card}
    >
      <View style={styles.top} accessible accessibilityLabel={`${title}, ${who}, ${day} ${month}${next ? `. Next visit ${next}` : ''}`}>
        <View style={[styles.date, { backgroundColor: t.bg }]}>
          <AppText style={[styles.day, { color: t.fg }]}>{day}</AppText>
          <AppText variant="overline" color={t.fg}>
            {month.toUpperCase()}
          </AppText>
        </View>
        <View style={styles.flex}>
          <AppText variant="bodyStrong" numberOfLines={1}>
            {title}
          </AppText>
          <AppText variant="caption" color={Brand.inkMuted} numberOfLines={1}>
            {who}
          </AppText>
        </View>
      </View>

      <View style={styles.foot}>
        {next ? (
          <View style={styles.next}>
            <Icon name="calendar" size={14} color={Brand.plum} />
            <AppText variant="caption" color={Brand.ink} numberOfLines={1}>
              {`Next visit · ${next}`}
            </AppText>
          </View>
        ) : (
          <View style={styles.flex} />
        )}
        {single ? (
          <DownloadPill state={state} label={`Download ${single.title}`} onPress={run} />
        ) : (
          <DownloadPill
            state="idle"
            label={`${docs.length} papers, ${open ? 'hide' : 'show'}`}
            chevron={chevron}
            onPress={() => {
              Haptic.light();
              setOpen((o) => !o);
            }}
          />
        )}
      </View>

      {open && !single && (
        <Animated.View entering={reduceMotion ? undefined : FadeInDown.duration(200)} style={styles.docs}>
          {docs.map((d) => (
            <DocRow key={d.id} doc={d} />
          ))}
        </Animated.View>
      )}
    </Animated.View>
    </Rise>
  );
});

const styles = StyleSheet.create({
  flex: { flex: 1, minWidth: 0 },
  pressed: { opacity: 0.75 },
  card: { gap: 12, padding: 14, borderRadius: 20, backgroundColor: Brand.surface, borderWidth: 1, borderColor: Brand.hairline },
  top: { flexDirection: 'row', alignItems: 'center', gap: 12 },
  // Fixed-size date tile: the date is what she looks for first.
  date: { width: 56, height: 56, borderRadius: 16, alignItems: 'center', justifyContent: 'center' },
  day: { fontSize: 22, lineHeight: 26, fontWeight: '700' },
  foot: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: 10 },
  next: {
    flexShrink: 1,
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
    paddingHorizontal: 10,
    minHeight: 32,
    borderRadius: 16,
    backgroundColor: Brand.lilacTint,
  },
  dl: {
    minHeight: 36,
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
    paddingHorizontal: 14,
    borderRadius: 18,
    backgroundColor: Brand.cherry,
  },
  dlDone: { backgroundColor: Brand.sage },
  docs: { borderTopWidth: 1, borderTopColor: Brand.hairline },
  doc: { flexDirection: 'row', alignItems: 'center', gap: 10, paddingTop: 10 },
});
