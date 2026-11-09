import { Directory, File, Paths } from 'expo-file-system';

// Uploaded lab reports on the phone: copied out of the picker's cache into
// documents/lab-reports/ (the cache can be cleared by the system), one file
// per report, named by its id. Returns the kept file's uri.

const dir = new Directory(Paths.document, 'lab-reports');

export async function keepLabFile(id: string, uri: string, ext: string): Promise<string> {
  if (!dir.exists) dir.create({ intermediates: true, idempotent: true });
  const dest = new File(dir, `${id}.${ext}`);
  if (dest.exists) dest.delete();
  await new File(uri).copy(dest);
  return dest.uri;
}
