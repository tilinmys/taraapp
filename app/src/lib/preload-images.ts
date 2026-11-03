import { Asset } from 'expo-asset';

import { APP_ICONS } from '@/components/system/app-icon';
import { STORY_IMAGES } from '@/services/canva-media-engine';

// Warm every in-app image once, in the background, right after the app
// starts (the native splash only waits for the brand intro images). By the
// time a mother reaches her home, the baby, the size fruits and every clay
// icon are already on the device, so nothing pops in late. In Expo Go this
// is what hides the dev server / tunnel download; in a release build the
// files are bundled and this simply pre-decodes them.
//
// Order = priority: the baby's hello and the baby stages first.

export const HELLO_ART = require('@/assets/images/art/baby-hello.webp');

let started = false;

export function preloadAppImages() {
  if (started) return;
  started = true;
  Asset.loadAsync([HELLO_ART, ...STORY_IMAGES])
    .then(() => Asset.loadAsync(Object.values(APP_ICONS)))
    .catch(() => {
      // Never fatal: an image that failed here simply loads when shown.
    });
}
