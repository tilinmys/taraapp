import type { Status } from '@/components/system/nav-card';
import type { BookingStatus } from '@/data/care-network';
import type { StringKey } from '@/i18n/strings';

/** A consultation's status as one word in one colour (ASHA and mother screens). */
export const STATUS_PILL: Record<BookingStatus, { key: StringKey; tone: Status['tone'] }> = {
  requested: { key: 'apptRequested', tone: 'info' },
  confirmed: { key: 'apptConfirmed', tone: 'ok' },
  rescheduled: { key: 'apptRescheduled', tone: 'info' },
  completed: { key: 'apptCompleted', tone: 'ok' },
  cancelled: { key: 'apptCancelled', tone: 'alert' },
};
