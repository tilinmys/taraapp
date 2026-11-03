import { Asset } from 'expo-asset';
import { useEffect } from 'react';
import { useWindowDimensions, type ImageSourcePropType } from 'react-native';

import { weightAt } from '@/data/pregnancy';

// Canva media engine for the mother's weekly story.
//
// ART. Each image comes from Canva, cut out on a transparent background and
// converted to 400x400 WebP with the subject centred and ~16-20 % breathing
// room, so the whole catalogue reads as one system. Files are BUNDLED (no
// download, no blank hole on 2G/3G). Source design in Canva: "Tara app
// illustrations (asset sheet)".
// To swap in Canva STOCK PHOTOS: export each photo with a transparent
// background, keep the same file names in assets/images/art/ (same 400x400,
// centred, 20 % margin) and nothing else changes.
//
// IMAGES render with React Native's <Image> (no extra native module, so the
// app is stable in Expo Go). usePrefetchWeekWindow readies week +/- 2 only.

export const MIN_SCRUB_WEEK = 4;
export const MAX_SCRUB_WEEK = 40;

type Art = ImageSourcePropType;

// --- Art manifest (anchor weeks; a week shows the nearest anchor at or below it)
const SIZE_ART: { week: number; like: string; name: string; cm: number; grams: number; src: Art }[] = [
  { week: 4, like: 'a poppy seed', name: 'Poppy seed', cm: 0.2, grams: 0.5, src: require('@/assets/images/art/w04-poppy.webp') },
  { week: 8, like: 'a rajma bean', name: 'Rajma bean', cm: 1.6, grams: 1, src: require('@/assets/images/art/w08-bean.webp') },
  { week: 12, like: 'a lime', name: 'Lime', cm: 5.4, grams: 14, src: require('@/assets/images/art/w12-lime.webp') },
  { week: 16, like: 'a mango', name: 'Mango', cm: 11.6, grams: 100, src: require('@/assets/images/art/w16-mango.webp') },
  { week: 20, like: 'a banana', name: 'Banana', cm: 25.6, grams: 300, src: require('@/assets/images/art/w20-banana.webp') },
  { week: 24, like: 'a corn cob', name: 'Corn cob', cm: 30, grams: 600, src: require('@/assets/images/art/w24-corn.webp') },
  { week: 28, like: 'a brinjal', name: 'Brinjal', cm: 37.6, grams: 1000, src: require('@/assets/images/art/w28-brinjal.webp') },
  { week: 32, like: 'a coconut', name: 'Coconut', cm: 42.4, grams: 1700, src: require('@/assets/images/art/w32-coconut.webp') },
  { week: 36, like: 'a papaya', name: 'Papaya', cm: 47.4, grams: 2600, src: require('@/assets/images/art/w36-papaya.webp') },
  { week: 40, like: 'a watermelon', name: 'Watermelon', cm: 51.2, grams: 3400, src: require('@/assets/images/art/w40-watermelon.webp') },
];

// The baby herself (Canva AI, clay style, transparent 480 px WebP), one per
// stage: curled and tiny, thumb-sucking, eyes open and waving, full-term.
const BABY_ART: { upTo: number; src: Art; pose: string }[] = [
  { upTo: 13, src: require('@/assets/images/art/baby-1.webp'), pose: 'Curled up, fast asleep' },
  { upTo: 23, src: require('@/assets/images/art/baby-2.webp'), pose: 'Sucking my thumb' },
  { upTo: 33, src: require('@/assets/images/art/baby-3.webp'), pose: 'Eyes open, waving at you' },
  { upTo: 99, src: require('@/assets/images/art/baby-4.webp'), pose: 'Cosy and almost ready' },
];
/** Every weekly-story image (baby stages first), for the app-start preload. */
export const STORY_IMAGES: Art[] = [...BABY_ART.map((b) => b.src), ...SIZE_ART.map((a) => a.src)];

export const babyFor = (week: number) => BABY_ART.find((b) => week <= b.upTo) ?? BABY_ART[BABY_ART.length - 1];

export const clampWeek =(w: number) => Math.min(MAX_SCRUB_WEEK, Math.max(MIN_SCRUB_WEEK, Math.round(w)));
const anchorFor = (w: number) => [...SIZE_ART].reverse().find((a) => a.week <= clampWeek(w)) ?? SIZE_ART[0];

// --- Week content -------------------------------------------------------------

const MILESTONES: Record<number, string> = {
  4: 'The tiny heart is starting to form.', 5: 'The brain and spine are beginning.', 6: 'A little heartbeat shows on a scan.',
  7: 'Arms and legs are budding.', 8: 'Fingers and toes are forming.', 9: 'All the main organs have started.',
  10: 'Baby can bend arms and legs.', 11: 'Tiny nails are growing.', 12: 'Baby can open and close fists.',
  13: 'Fingerprints are forming.', 14: 'Baby can make little faces.', 15: 'Baby can sense light.',
  16: 'You may feel the first flutters soon.', 17: 'Baby is building a little fat.', 18: 'Baby can hear sounds inside you.',
  19: 'A soft coating protects the skin.', 20: 'Halfway there! Kicks feel stronger.', 21: 'Baby swallows and tastes.',
  22: 'Baby can feel your gentle touch and hear your heartbeat.', 23: 'Baby moves to music and voices.', 24: 'The lungs are growing fast.',
  25: 'Baby responds to your voice.', 26: 'The eyes are starting to open.', 27: 'Baby sleeps and wakes in cycles.',
  28: 'Baby can blink and respond to your voice.', 29: 'Bones are getting stronger.', 30: 'Baby can tell light from dark.',
  31: 'Baby turns the head side to side.', 32: 'Baby practises breathing.', 33: 'Baby’s immunity is growing.',
  34: 'Baby may turn head-down.', 35: 'The kidneys are fully formed.', 36: 'Baby gains about 30 g a day.',
  37: 'Baby is now early term.', 38: 'Baby is getting ready to meet you.', 39: 'The lungs are almost ready.',
  40: 'Your due week: any day now.',
};

/**
 * The same milestones in the baby's own voice, spoken to Amma. Each line ties
 * what baby can do now to something the mother does (her voice, touch, food,
 * tablets, rest) or picks up from an earlier week, so the weeks read as one
 * continuing story rather than separate facts. Shown in the hero bubble and
 * on the hello screen.
 */
const BABY_SAYS: Record<number, string> = {
  4: 'Amma, I’m only a tiny seed, but my heart has already started forming, right under yours.',
  5: 'My brain and spine are starting now. Your folic acid tablet is helping me build them.',
  6: 'Listen, Amma: my heart has started beating! Soon it will show on a scan.',
  7: 'Little buds are growing into my arms and legs, so one day I can hold on to you.',
  8: 'Fingers and toes are forming on those new arms and legs. Ten of each, just for you.',
  9: 'All my main parts have started. We’re both working hard, so rest when you’re tired, Amma.',
  10: 'I can bend my arms and legs now. I’m moving inside you, even if you can’t feel it yet.',
  11: 'Tiny nails are growing on my new fingers and toes.',
  12: 'I can open and close my fists now, practising to hold your finger one day.',
  13: 'My fingerprints are forming. No one else in the world will have hands like mine.',
  14: 'I can make little faces now: a squint, a frown, maybe even a smile for you.',
  15: 'I can sense light now. When you step out into the sun, I notice.',
  16: 'I’m stretching and turning so much that soon you may feel me flutter, Amma.',
  17: 'I’m building a little fat to keep me warm. Your ragi and dal help.',
  18: 'I can hear sounds now, Amma. Your heartbeat is my favourite song.',
  19: 'A soft coating is wrapping my skin, keeping me safe while I float in here with you.',
  20: 'Halfway there, Amma! My kicks are getting stronger. Did you feel that one?',
  21: 'I can swallow and taste now, so I get a little taste of every meal you eat.',
  22: 'I can feel it when you touch your belly, and I hear your heartbeat all day.',
  23: 'I move when you sing or talk. Please keep talking to me, Amma.',
  24: 'My lungs are growing fast, getting ready for my first breath next to you.',
  25: 'I know your voice now. When you speak, I turn towards it.',
  26: 'My eyes are starting to open. The first face I look for will be yours.',
  27: 'I sleep and wake in little cycles now. Sometimes I wake up just as you lie down.',
  28: 'I can blink now, and when you talk to me, I answer with a kick.',
  29: 'My bones are getting stronger. Your calcium tablet goes straight to them.',
  30: 'I can tell light from dark now, but your voice is still my favourite thing.',
  31: 'I can turn my head from side to side, looking for where your voice comes from.',
  32: 'I’m practising breathing, in and out, so I’m ready to breathe next to you.',
  33: 'My body is learning to fight germs, and you’re sharing your strength with me.',
  34: 'I may turn head-down soon, getting ready for the way out to meet you.',
  35: 'My kidneys are fully formed. Most of me is ready now; I’m just growing.',
  36: 'I’m gaining about 30 g a day. Your extra meal is making me strong.',
  37: 'I’m early term now, Amma. It won’t be long until you hold me.',
  38: 'I’m getting ready to meet you. Keep your hospital bag close.',
  39: 'My lungs are almost ready for my first cry, and our first cuddle.',
  40: 'This is our week, Amma. Any day now, you’ll finally see me.',
};

type Range<T> = { from: number; to: number; value: T };
const pick = <T>(ranges: Range<T>[], w: number) => (ranges.find((r) => w >= r.from && w <= r.to) ?? ranges[ranges.length - 1]).value;
type Copy = { title: string; short: string; long: string };

/** Tile 1: baby's senses. */
const SENSES: Range<Copy>[] = [
  { from: 4, to: 12, value: { title: 'Tiny heartbeat', short: 'Baby’s heart beats twice as fast as yours.', long: 'The heart started beating around week 6 and is now strong. Your ASHA can hear it with a doppler from about week 12.' } },
  { from: 13, to: 17, value: { title: 'Making faces', short: 'Baby can squint, frown and suck a thumb.', long: 'Baby is practising expressions: squinting, frowning and sucking a thumb. These little movements build the muscles for feeding.' } },
  { from: 18, to: 23, value: { title: 'Hears your voice', short: 'Talk or sing; baby knows your voice.', long: 'Baby can hear your voice and heartbeat. Talking, singing or reading aloud helps baby know you even before birth.' } },
  { from: 24, to: 27, value: { title: 'Feels your touch', short: 'Rubbing your belly calms baby.', long: 'Baby can feel gentle pressure. Rubbing your belly and talking softly can calm baby’s heart rate.' } },
  { from: 28, to: 34, value: { title: 'Sees the light', short: 'Baby blinks and turns toward light.', long: 'Baby’s eyes open and close and can sense light through your belly. Baby also sleeps and dreams in cycles.' } },
  { from: 35, to: 40, value: { title: 'Ready to cuddle', short: 'Baby will know your smell and voice.', long: 'Baby is getting ready for birth and will recognise your voice and smell. Skin-to-skin after birth calms both of you.' } },
];

/** Tile 2: mother's body & comfort (linked to the Sakhi check-in questions). */
const BODY: Range<Copy & { sakhi: string }>[] = [
  { from: 4, to: 12, value: { title: 'Rest when tired', short: 'Nausea is common. Eat small, often.', long: 'Nausea and tiredness are common now. Eat small meals often. Bleeding or severe belly pain: tell your ASHA today.', sakhi: 'Sakhi Q10' } },
  { from: 13, to: 20, value: { title: 'Gentle walks', short: '30 minutes a day keeps you strong.', long: 'A 30-minute gentle walk most days helps your back, sleep and mood. Note any headache or swollen feet for your ASHA.', sakhi: 'Sakhi Q9 · Q24' } },
  { from: 21, to: 27, value: { title: 'Watch your BP', short: 'Headache or swelling? Tell your ASHA.', long: 'Severe headache, blurred vision or a swollen face can mean high BP. Tell your ASHA the same day.', sakhi: 'Sakhi Q6 · Q9' } },
  { from: 28, to: 34, value: { title: 'Rest on left side', short: 'Helps healthy blood flow to baby.', long: 'Lying on your left side helps blood flow to baby. Count kicks daily: at least 10 in 2 hours.', sakhi: 'Sakhi Q12' } },
  { from: 35, to: 40, value: { title: 'Know labour signs', short: 'Pains every 10 min? Go to hospital.', long: 'Water leaking, bleeding, or pains every 10 minutes: go to the hospital and call 108.', sakhi: 'Sakhi Q10' } },
];

/** Tile 3: today's nutrition (local, affordable foods). */
const FOOD: Range<Copy>[] = [
  { from: 4, to: 13, value: { title: 'Folic acid daily', short: 'Dal, palak and a glass of milk.', long: 'Take your folic acid tablet every day. Add dal, palak (spinach) and a glass of milk.' } },
  { from: 14, to: 26, value: { title: 'Ragi, dal & greens', short: '1 iron + 1 calcium tablet after lunch.', long: 'Ragi, dal and green leaves every day. Take 1 iron and 1 calcium tablet after lunch, with lemon or amla, not tea.' } },
  { from: 27, to: 40, value: { title: 'One extra meal', short: 'Ragi, dal, banana. Iron + calcium daily.', long: 'Add one extra meal: ragi mudde, dal, banana, eggs or curd. Keep taking iron and calcium every day.' } },
];

/** Tile 4 (normal): what the clinic / ASHA does around this week. */
const CLINIC: Range<{ title: string; long: string }>[] = [
  { from: 4, to: 8, value: { title: 'Register pregnancy', long: 'Your ASHA registers you and gives the mother (MCP) card.' } },
  { from: 9, to: 12, value: { title: 'First check-up', long: 'BP, Hb, urine, blood group and a dating scan.' } },
  { from: 13, to: 16, value: { title: 'Td injection 1', long: 'First Td dose. Start iron and calcium tablets.' } },
  { from: 17, to: 20, value: { title: 'Anomaly scan', long: 'Detailed scan between 18 and 20 weeks.' } },
  { from: 21, to: 24, value: { title: 'Td injection 2', long: 'Second Td dose. BP and urine protein check.' } },
  { from: 25, to: 28, value: { title: 'Sugar test', long: 'Glucose test between 24 and 28 weeks, and Hb.' } },
  { from: 29, to: 32, value: { title: 'Third check-up', long: 'BP, weight and Hb with your ASHA or PHC.' } },
  { from: 33, to: 36, value: { title: 'Growth scan', long: 'Check baby’s growth and choose your delivery hospital.' } },
  { from: 37, to: 40, value: { title: 'Weekly checks', long: 'Weekly BP. Hospital bag packed, 108 saved.' } },
];

export type WeekInfo = {
  week: number;
  trimester: 1 | 2 | 3;
  like: string;
  fruit: string;
  cm: number;
  weightLabel: string;
  milestone: string;
  quote: string;
  art: Art;
  senses: Copy;
  body: Copy & { sakhi: string };
  food: Copy;
  clinic: { title: string; long: string };
};

const weight = (w: number) => {
  const g = w >= 12 ? weightAt(w) : anchorFor(w).grams;
  return g < 1 ? 'under 1 g' : g < 1000 ? `~${g < 20 ? Math.round(g) : Math.round(g / 10) * 10} g` : `~${(g / 1000).toFixed(1)} kg`;
};

export function weekInfo(week: number): WeekInfo {
  const w = clampWeek(week);
  const a = anchorFor(w);
  return {
    week: w,
    trimester: w <= 12 ? 1 : w <= 26 ? 2 : 3,
    like: a.like,
    fruit: a.name,
    cm: a.cm,
    weightLabel: weight(w),
    milestone: MILESTONES[w],
    quote: BABY_SAYS[w],
    art: a.src,
    senses: pick(SENSES, w),
    body: pick(BODY, w),
    food: pick(FOOD, w),
    clinic: pick(CLINIC, w),
  };
}

/** Same art = same key: the image view is reused, not re-created. */
export const artKey = (week: number) => `stock-week-${anchorFor(week).week}`;

// --- Sliding-window preload (week +/- 2) ----------------------------------------

export function usePrefetchWeekWindow(week: number) {
  const anchor = anchorFor(week).week;
  useEffect(() => {
    const mods = new Set<number>();
    for (let w = week - 2; w <= week + 2; w++) {
      if (w >= MIN_SCRUB_WEEK && w <= MAX_SCRUB_WEEK) mods.add(anchorFor(w).src as number);
    }
    Asset.loadAsync([...mods]).catch(() => {});
    // Only when the visible art anchor changes, not on every week.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [anchor]);
}

// --- Responsive sizes ------------------------------------------------------------

export function useStoryLayout() {
  const { width } = useWindowDimensions();
  const isTablet = width >= 768;
  return { isTablet, circle: isTablet ? 112 : 92, tileHeight: isTablet ? 180 : 164, gap: 14 };
}
