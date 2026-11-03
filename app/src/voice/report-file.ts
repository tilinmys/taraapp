import * as Print from 'expo-print';
import { Platform } from 'react-native';

import type { CheckInReport, MotherRecord } from '@/data/store';
import { buildReportHtml } from '@/lib/report-pdf';

import { voiceDb } from './db';

// The voice check-in's report as a PDF FILE on the phone, made right after
// the check-in (same template the ASHA and doctor share from the report
// page). Its path is saved in sessions.pdf_uri (SQLite), so the document
// exists offline, before anyone taps Share. Built once per report.

const made = new Set<string>();

export async function saveReportPdf(m: MotherRecord, r: CheckInReport) {
  if (Platform.OS === 'web' || made.has(r.id)) return null;
  made.add(r.id);
  try {
    const { uri } = await Print.printToFileAsync({ html: buildReportHtml(m, r), width: 595, height: 842 });
    await voiceDb.setPdf(r.id, uri);
    return uri;
  } catch {
    made.delete(r.id);
    return null;
  }
}
