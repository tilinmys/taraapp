import { memo } from 'react';
import { Pressable, StyleSheet, View } from 'react-native';

import { Icon, type IconName } from '@/components/system/icon';
import { AppText } from '@/components/ui/app-text';
import { Colors, Haptic } from '@/constants/theme';
import { reviewOf, triageOf, useDoctorReview, type Triage } from '@/data/doctor-review';
import { fmtWhen } from '@/data/care-network';
import { RULES } from '@/data/mockScreening';
import { riskOf, type MotherRecord } from '@/data/store';

// The doctor's building blocks. A clinical look, not a decorative one:
//   * white rectangles (10 pt corners) on a hairline border; colour is ONLY
//     status, as a 4 pt bar on the left edge and one small tag
//   * four statuses, each with ONE colour a clinician already reads that
//     way: red = act now, blue = new / unread, amber = follow up, green = done
//   * anything that opens something shows a › and dims when pressed
// No gradients, glows, pastel rainbows or emoji.

export const TRIAGE: Record<Triage, { label: string; fg: string; bg: string; bar: string }> = {
  urgent: { label: 'Urgent', fg: Colors.cherry, bg: '#FBEAEE', bar: Colors.cherry },
  new: { label: 'New report', fg: '#1D4E89', bg: '#E8F0FA', bar: '#1D4E89' },
  referred: { label: 'Referred', fg: Colors.pumpkinInk, bg: '#FFF1E6', bar: Colors.pumpkin },
  done: { label: 'Reviewed', fg: Colors.sage, bg: Colors.sageTint, bar: Colors.sage },
};

export const DOC = { radius: 10, border: Colors.bandLine } as const;

export function Tag({ triage, label }: { triage: Triage; label?: string }) {
  const t = TRIAGE[triage];
  return (
    <View style={[styles.tag, { backgroundColor: t.bg }]}>
      <AppText variant="overline" color={t.fg} numberOfLines={1}>
        {(label ?? t.label).toUpperCase()}
      </AppText>
    </View>
  );
}

/** One line on why this case is where it is. */
export function reasonLine(m: MotherRecord, triage: Triage, reviewedAt?: number) {
  const { hits } = riskOf(m);
  const top = hits.find((h) => h.code === 'PE-04') ?? hits.find((h) => RULES[h.code].severity !== 'watch') ?? hits[0];
  const report = m.reports[0];
  switch (triage) {
    case 'urgent':
      return top ? RULES[top.code].title : `Marked "needs care" by ASHA ${m.asha}`;
    case 'new':
      return report ? `Check-in by ASHA ${report.asha} · ${report.when}` : 'New report';
    case 'referred':
      return `Referral sent · ${top ? RULES[top.code].title : 'follow up'}`;
    case 'done':
      return reviewedAt ? `Reviewed ${fmtWhen(reviewedAt)}` : hits.length ? `${hits.length} watch sign${hits.length === 1 ? '' : 's'}` : 'All answers normal';
  }
}

/** THE case row: the same rectangle in every doctor list. */
export const CaseRow = memo(function CaseRow({ m, onPress }: { m: MotherRecord; onPress: (id: string) => void }) {
  const review = useDoctorReview();
  const triage = triageOf(m, review);
  const t = TRIAGE[triage];
  const reviewed = reviewOf(m, review);
  const line = reasonLine(m, triage, reviewed?.at);
  return (
    <Pressable
      accessibilityRole="button"
      accessibilityLabel={`${m.name}, ${m.weeksLabel}. ${t.label}. ${line}. Opens the case.`}
      onPress={() => {
        Haptic.light();
        onPress(m.id);
      }}
      style={({ pressed }) => [styles.row, pressed && styles.pressed]}
    >
      <View style={[styles.bar, { backgroundColor: t.bar }]} />
      <View style={styles.rowBody}>
        <View style={styles.rowTop}>
          <AppText variant="bodyStrong" numberOfLines={1} style={styles.flex}>
            {m.name}
          </AppText>
          <Tag triage={triage} />
        </View>
        <AppText variant="caption" color={Colors.inkMuted} numberOfLines={1}>
          {`${m.weeksLabel} · ${m.village} · ASHA ${m.asha}`}
        </AppText>
        <AppText variant="caption" color={triage === 'urgent' ? Colors.cherry : Colors.ink} numberOfLines={2}>
          {line}
        </AppText>
      </View>
      <Icon name="forward" size={18} color={Colors.inkMuted} weight="bold" />
    </Pressable>
  );
});

/** A rectangular number tile that opens its list. */
export function StatTile({ label, value, tone, onPress }: { label: string; value: number; tone?: Triage; onPress: () => void }) {
  const t = tone && value > 0 ? TRIAGE[tone] : null;
  return (
    <Pressable
      accessibilityRole="button"
      accessibilityLabel={`${label}: ${value}`}
      onPress={() => {
        Haptic.light();
        onPress();
      }}
      style={({ pressed }) => [styles.stat, t && { borderColor: t.bar }, pressed && styles.pressed]}
    >
      {t && <View style={[styles.statBar, { backgroundColor: t.bar }]} />}
      <AppText variant="caption" color={Colors.inkMuted} numberOfLines={1}>
        {label}
      </AppText>
      <View style={styles.statRow}>
        <AppText variant="metric" color={t ? t.fg : Colors.ink}>
          {String(value)}
        </AppText>
        <Icon name="forward" size={16} color={Colors.inkMuted} weight="bold" />
      </View>
    </Pressable>
  );
}

/** Section title with an optional count and a "See all ›" link. */
export function SectionHead({ title, count, link }: { title: string; count?: number; link?: { label: string; onPress: () => void } }) {
  return (
    <View style={styles.head}>
      <AppText variant="heading" accessibilityRole="header" style={styles.flex} numberOfLines={1}>
        {count === undefined ? title : `${title} · ${count}`}
      </AppText>
      {link && (
        <Pressable accessibilityRole="link" onPress={link.onPress} hitSlop={8} style={({ pressed }) => [styles.link, pressed && styles.pressed]}>
          <AppText variant="buttonSmall" color={Colors.cherry}>
            {link.label}
          </AppText>
          <Icon name="forward" size={16} color={Colors.cherry} weight="bold" />
        </Pressable>
      )}
    </View>
  );
}

/** A plain list row that opens a page (icon, title, line, ›). */
export function LinkRow({ icon, title, line, onPress }: { icon: IconName; title: string; line?: string; onPress: () => void }) {
  return (
    <Pressable
      accessibilityRole="button"
      accessibilityLabel={[title, line].filter(Boolean).join('. ')}
      onPress={() => {
        Haptic.light();
        onPress();
      }}
      style={({ pressed }) => [styles.linkRow, pressed && styles.pressed]}
    >
      <View style={styles.linkIcon}>
        <Icon name={icon} size={20} color={Colors.ink} />
      </View>
      <View style={styles.flex}>
        <AppText variant="bodyStrong" numberOfLines={1}>
          {title}
        </AppText>
        {!!line && (
          <AppText variant="caption" color={Colors.inkMuted} numberOfLines={1}>
            {line}
          </AppText>
        )}
      </View>
      <Icon name="forward" size={18} color={Colors.inkMuted} weight="bold" />
    </Pressable>
  );
}

/** Calm "nothing here" line inside a section. */
export function Empty({ text }: { text: string }) {
  return (
    <View style={styles.empty}>
      <Icon name="checkCircle" size={20} color={Colors.sage} weight="fill" />
      <AppText variant="body" color={Colors.inkMuted} style={styles.flex}>
        {text}
      </AppText>
    </View>
  );
}

const styles = StyleSheet.create({
  flex: { flex: 1, minWidth: 0 },
  pressed: { opacity: 0.6 },
  tag: { paddingHorizontal: 8, paddingVertical: 3, borderRadius: 6 },
  row: {
    minHeight: 84,
    flexDirection: 'row',
    alignItems: 'center',
    gap: 12,
    paddingRight: 14,
    borderRadius: DOC.radius,
    borderWidth: 1,
    borderColor: DOC.border,
    backgroundColor: Colors.surface,
    overflow: 'hidden',
  },
  bar: { alignSelf: 'stretch', width: 4 },
  rowBody: { flex: 1, minWidth: 0, gap: 2, paddingVertical: 12 },
  rowTop: { flexDirection: 'row', alignItems: 'center', gap: 8 },
  stat: {
    flex: 1,
    minWidth: 0,
    gap: 2,
    paddingHorizontal: 12,
    paddingTop: 10,
    paddingBottom: 8,
    borderRadius: DOC.radius,
    borderWidth: 1,
    borderColor: DOC.border,
    backgroundColor: Colors.surface,
    overflow: 'hidden',
  },
  statBar: { position: 'absolute', left: 0, right: 0, top: 0, height: 3 },
  statRow: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' },
  head: { flexDirection: 'row', alignItems: 'center', gap: 8, paddingTop: 4 },
  link: { minHeight: 44, flexDirection: 'row', alignItems: 'center', gap: 2 },
  linkRow: {
    minHeight: 64,
    flexDirection: 'row',
    alignItems: 'center',
    gap: 12,
    paddingHorizontal: 14,
    borderRadius: DOC.radius,
    borderWidth: 1,
    borderColor: DOC.border,
    backgroundColor: Colors.surface,
  },
  linkIcon: { width: 36, height: 36, borderRadius: 8, alignItems: 'center', justifyContent: 'center', backgroundColor: Colors.shell },
  empty: { flexDirection: 'row', alignItems: 'center', gap: 10, padding: 14, borderRadius: DOC.radius, backgroundColor: Colors.shell },
});
