import { useWindowDimensions } from 'react-native';

// Responsive content column, centred, so text lines stay a comfortable
// length from a 320 pt phone to a 12" tablet:
//   phones  (< 768 pt)  520 pt column, 24 pt gutter
//   tablets (>= 768 pt) 760 pt column, 32 pt gutter (2-column bentos)
export function useContentWidth() {
  const { width } = useWindowDimensions();
  const isTablet = width >= 768;
  return { maxWidth: isTablet ? 760 : 520, gutter: isTablet ? 32 : 24, isTablet, width };
}
