import { Image, type ImageStyle, type StyleProp } from 'react-native';

// Clay-style 3D icons generated with Canva AI (transparent WebP, 192 px).
// Used across the mother screens in place of line icons and emojis.

export const APP_ICONS = {
  home: require('../../../assets/images/icons/home.webp'),
  pregnancy: require('../../../assets/images/icons/pregnancy.webp'),
  talk: require('../../../assets/images/icons/talk.webp'),
  history: require('../../../assets/images/icons/history.webp'),
  senses: require('../../../assets/images/icons/senses.webp'),
  rest: require('../../../assets/images/icons/rest.webp'),
  food: require('../../../assets/images/icons/food.webp'),
  clinic: require('../../../assets/images/icons/clinic.webp'),
  calendar: require('../../../assets/images/icons/calendar.webp'),
  checklist: require('../../../assets/images/icons/checklist.webp'),
  medicine: require('../../../assets/images/icons/medicine.webp'),
  kicks: require('../../../assets/images/icons/kicks.webp'),
  maternity: require('../../../assets/images/icons/maternity.webp'),
  fertility: require('../../../assets/images/icons/fertility.webp'),
  reproductive: require('../../../assets/images/icons/reproductive.webp'),
  ask: require('../../../assets/images/icons/ask.webp'),
  asha: require('../../../assets/images/icons/asha.webp'),
  ambulance: require('../../../assets/images/icons/ambulance.webp'),
  prescription: require('../../../assets/images/icons/prescription.webp'),
  report: require('../../../assets/images/icons/report.webp'),
  // People and settings (sign-in role cards, profile, language pill).
  mother: require('../../../assets/images/icons/mother.webp'),
  doctor: require('../../../assets/images/icons/doctor.webp'),
  profile: require('../../../assets/images/icons/profile.webp'),
  language: require('../../../assets/images/icons/language.webp'),
  family: require('../../../assets/images/icons/family.webp'),
  // Care topics (topic guide cards).
  alert: require('../../../assets/images/icons/alert.webp'),
  bag: require('../../../assets/images/icons/bag.webp'),
  water: require('../../../assets/images/icons/water.webp'),
  weight: require('../../../assets/images/icons/weight.webp'),
  scan: require('../../../assets/images/icons/scan.webp'),
  privacy: require('../../../assets/images/icons/privacy.webp'),
  tip: require('../../../assets/images/icons/tip.webp'),
  // ASHA workspace.
  ashaWorker: require('../../../assets/images/icons/ashaWorker.webp'),
  voice: require('../../../assets/images/icons/voice.webp'),
} as const;

export type AppIconName = keyof typeof APP_ICONS;

export function AppIcon({ name, size = 32, style, dim }: { name: AppIconName; size?: number; style?: StyleProp<ImageStyle>; dim?: boolean }) {
  return (
    <Image
      fadeDuration={0}
      source={APP_ICONS[name]}
      style={[{ width: size, height: size }, dim && { opacity: 0.55 }, style]}
      resizeMode="contain"
      accessibilityIgnoresInvertColors
      aria-hidden
    />
  );
}
