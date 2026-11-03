import { useLocalSearchParams } from 'expo-router';
import { useState } from 'react';
import { Pressable, StyleSheet, View } from 'react-native';

import { goBack } from '@/lib/nav';
import { AppIcon } from '@/components/system/app-icon';
import { Emblem, TONES } from '@/components/system/emblem';
import { Icon } from '@/components/system/icon';
import { Screen } from '@/components/system/screen';
import { StateView } from '@/components/system/state-view';
import { AppText } from '@/components/ui/app-text';
import { Colors, Haptic } from '@/constants/theme';
import { TOPICS, type TopicCard, type TopicId } from '@/data/pregnancy';

// Topic page (Maternity / Fertility / Reproductive health): the topic's
// emblem on its own tint, then one calm list of short guides. A row shows
// or hides its three plain tips under a down arrow, with no motion; urgent
// guides sit first in cherry. Reading is private: nothing here is tracked
// or shared.

/**
 * One guide as a plain list row: clay icon, title, down arrow. Tapping only
 * shows or hides the tips underneath; nothing slides, resizes or springs,
 * so the page stays still and easy to read.
 */
function Guide({ card, tone, open, onToggle, first }: { card: TopicCard; tone: keyof typeof TONES; open: boolean; onToggle: () => void; first: boolean }) {
  const t = TONES[tone];
  return (
    <View style={[!first && styles.divider, card.urgent && styles.urgent]}>
      <Pressable
        accessibilityRole="button"
        accessibilityState={{ expanded: open }}
        accessibilityLabel={card.urgent ? `${card.title}, important` : card.title}
        onPress={() => {
          Haptic.light();
          onToggle();
        }}
        style={({ pressed }) => [styles.row, pressed && styles.pressed]}
      >
        <View style={[styles.iconWell, { backgroundColor: card.urgent ? Colors.surface : t.bg }]}>
          <AppIcon name={card.icon} size={30} />
        </View>
        <AppText variant="bodyStrong" color={card.urgent ? Colors.cherry : Colors.ink} style={styles.flex}>
          {card.title}
        </AppText>
        <View style={open && styles.flip}>
          <Icon name="down" size={20} color={card.urgent ? Colors.cherry : Colors.inkMuted} weight="bold" />
        </View>
      </Pressable>
      {open && (
        <View style={styles.tips}>
          {card.tips.map((tip) => (
            <View key={tip} style={styles.tip}>
              <View style={[styles.bullet, { backgroundColor: card.urgent ? Colors.cherry : t.fg }]} />
              <AppText variant="body" style={[styles.flex, styles.tipText]}>
                {tip}
              </AppText>
            </View>
          ))}
        </View>
      )}
    </View>
  );
}

export default function TopicPage() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const topic = TOPICS[id as TopicId];
  const [open, setOpen] = useState<string | null>(topic?.cards.find((c) => c.urgent)?.id ?? null);
  const back = { label: 'Home', onPress: () => goBack('/mother') };

  if (!topic) {
    return (
      <Screen title="Not found" back={back}>
        <StateView title="This guide has moved" body="Go back home to see all care topics." action={{ label: 'Go to home', onPress: back.onPress }} />
      </Screen>
    );
  }
  return (
    <Screen
      band
      eyebrow="Explore care"
      title={topic.title}
      back={back}
      accessory={
        <View style={styles.hero}>
          <Emblem icon={topic.icon} tone={topic.tone} size={64} />
          <AppText variant="body" color={Colors.inkMuted} style={styles.flex}>
            {topic.blurb}
          </AppText>
        </View>
      }
    >
      <View style={styles.list}>
        {topic.cards.map((card, i) => (
          <Guide
            key={card.id}
            card={card}
            tone={topic.tone}
            first={i === 0}
            open={open === card.id}
            onToggle={() => setOpen((o) => (o === card.id ? null : card.id))}
          />
        ))}
      </View>
      <View style={styles.privacy}>
        <AppIcon name="privacy" size={24} />
        <AppText variant="caption" color={Colors.inkMuted} style={styles.flex}>
          Private: nothing you open here is saved or shared.
        </AppText>
      </View>
    </Screen>
  );
}

const styles = StyleSheet.create({
  flex: { flex: 1, minWidth: 0 },
  pressed: { opacity: 0.7 },
  hero: { flexDirection: 'row', alignItems: 'center', gap: 14 },
  list: { borderRadius: 20, borderWidth: 1, borderColor: Colors.bandLine, backgroundColor: Colors.surface, overflow: 'hidden' },
  divider: { borderTopWidth: 1, borderTopColor: Colors.hairline },
  urgent: { backgroundColor: Colors.cherryMist },
  row: { minHeight: 68, flexDirection: 'row', alignItems: 'center', gap: 14, paddingHorizontal: 16, paddingVertical: 12 },
  iconWell: { width: 44, height: 44, borderRadius: 14, alignItems: 'center', justifyContent: 'center' },
  // Tips line up under the title (16 + 44 + 14 = 74 pt), not under the icon.
  tips: { gap: 12, paddingLeft: 74, paddingRight: 20, paddingBottom: 18 },
  tip: { flexDirection: 'row', gap: 10, alignItems: 'flex-start' },
  tipText: { lineHeight: 26 },
  bullet: { width: 6, height: 6, borderRadius: 3, marginTop: 10 },
  privacy: { flexDirection: 'row', alignItems: 'center', gap: 8 },
  flip: { transform: [{ rotate: '180deg' }] },
});
