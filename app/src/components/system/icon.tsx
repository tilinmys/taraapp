import {
  ArrowCounterClockwise,
  ArrowRight,
  Baby,
  Bell,
  BowlFood,
  CalendarDots,
  CaretDown,
  CaretLeft,
  CaretRight,
  ChartLineUp,
  ChatCircleDots,
  Check,
  CheckCircle,
  ClipboardText,
  Clock,
  DownloadSimple,
  Drop,
  FilePdf,
  FirstAidKit,
  FlowerLotus,
  Footprints,
  GearSix,
  HandWaving,
  House,
  Info,
  Leaf,
  ListChecks,
  LockSimple,
  MagnifyingGlass,
  MapPin,
  Microphone,
  Moon,
  PaperPlaneTilt,
  Phone,
  Pill,
  Plant,
  Plus,
  Pulse,
  Ruler,
  Scales,
  ShieldCheck,
  SignOut,
  Signature,
  SpeakerHigh,
  SpeakerSlash,
  Stethoscope,
  Stop,
  Sun,
  Thermometer,
  Timer,
  Translate,
  UserCircle,
  UsersThree,
  Warning,
  Waveform,
  WifiSlash,
  X,
  type IconProps,
} from 'phosphor-react-native';
import type { ComponentType } from 'react';
import { View } from 'react-native';

import { Brand } from '@/constants/brand';

// The app's single icon family: Phosphor (humanist, less "template-generic"
// than the usual stroke packs). One rule everywhere:
//   inactive  weight "regular" (thin ~1.5 px look)
//   active    weight "fill"   (solid), or "bold" where a fill reads poorly
//   tone      weight "duotone": outline in `color`, inner fill in `tone` at
//             30 % (pumpkin / blush), used for feature tiles and the tab bar
// No star or heart shapes anywhere: they read as "AI sparkle" / "dating app".
// Add new icons to ICONS below so the whole app keeps one vocabulary.

const ICONS = {
  back: CaretLeft,
  forward: CaretRight,
  down: CaretDown,
  close: X,
  check: Check,
  checkCircle: CheckCircle,
  warning: Warning,
  info: Info,
  mic: Microphone,
  stop: Stop,
  wave: Waveform,
  speaker: SpeakerHigh,
  mute: SpeakerSlash,
  asha: FirstAidKit,
  mother: Baby,
  doctor: Stethoscope,
  care: HandWaving, // "reach out": Care Circle, check-in prep
  pulse: Pulse,
  search: MagnifyingGlass,
  language: Translate,
  profile: UserCircle,
  settings: GearSix,
  signOut: SignOut,
  history: Clock,
  home: House,
  phone: Phone,
  checklist: ListChecks,
  shield: ShieldCheck,
  clipboard: ClipboardText,
  team: UsersThree,
  send: PaperPlaneTilt,
  chat: ChatCircleDots, // Sakhi / Tara conversation mark
  arrow: ArrowRight,
  reset: ArrowCounterClockwise,
  lotus: FlowerLotus, // reproductive health
  sprout: Plant, // fertility
  leaf: Leaf,
  lock: LockSimple,
  calendar: CalendarDots,
  download: DownloadSimple,
  pdf: FilePdf,
  drop: Drop, // haemoglobin / sugar
  scales: Scales, // weight
  thermometer: Thermometer,
  footprints: Footprints, // kicks
  ruler: Ruler, // baby size
  pill: Pill,
  food: BowlFood,
  moon: Moon,
  sun: Sun,
  timer: Timer,
  bell: Bell,
  trend: ChartLineUp,
  pin: MapPin,
  signature: Signature,
  plus: Plus,
  offline: WifiSlash,
} satisfies Record<string, ComponentType<IconProps>>;

export type IconName = keyof typeof ICONS;

type Props = {
  name: IconName;
  size?: number;
  color?: string;
  /** Active / selected state: switches to the filled weight. */
  active?: boolean;
  weight?: IconProps['weight'];
  /** Duotone inner fill colour (30 % opacity). Overrides `active`. */
  tone?: string;
};

export function Icon({ name, size = 24, color = Brand.ink, active = false, weight, tone }: Props) {
  const Glyph = ICONS[name];
  return (
    <View aria-hidden pointerEvents="none">
      <Glyph
        size={size}
        color={color}
        weight={weight ?? (tone ? 'duotone' : active ? 'fill' : 'regular')}
        duotoneColor={tone}
        duotoneOpacity={tone ? 0.3 : undefined}
      />
    </View>
  );
}
