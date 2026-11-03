import { router, type Href } from 'expo-router';

// Back that always goes somewhere. router.back() alone does nothing when the
// screen has no history: opened from a notification, a link, a reload on the
// web, or after a router.replace. Every back button names where it lands
// otherwise (its home), so no back button is ever dead.
export function goBack(fallback: Href) {
  if (router.canGoBack()) router.back();
  else router.replace(fallback);
}
