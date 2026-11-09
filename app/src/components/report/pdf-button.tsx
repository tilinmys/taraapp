import { useState } from 'react';

import { Icon } from '@/components/system/icon';
import { PressButton } from '@/components/ui/press-button';
import { Brand } from '@/constants/brand';
import type { CheckInReport, MotherRecord } from '@/data/store';
import { Haptic } from '@/lib/haptics';
import { shareReportPdf } from '@/lib/report-pdf';
import { useT } from '@/features/onboarding/context';

// "Download PDF report": the same export for the ASHA and the doctor.
type Props = { mother: MotherRecord; report: CheckInReport; label?: string };

export function PdfButton({ mother, report, label }: Props) {
  const t = useT();
  const [state, setState] = useState<'idle' | 'busy' | 'error'>('idle');
  return (
    <PressButton
      variant="secondary"
      label={state === 'busy' ? t('pdfPreparing') : state === 'error' ? t('pdfFailed') : (label ?? t('pdfDownload'))}
      icon={<Icon name="pdf" size={22} color={Brand.cherry} tone={Brand.pumpkin} />}
      disabled={state === 'busy'}
      onPress={async () => {
        setState('busy');
        try {
          await shareReportPdf(mother, report);
          Haptic.success();
          setState('idle');
        } catch {
          Haptic.warning();
          setState('error');
        }
      }}
    />
  );
}
