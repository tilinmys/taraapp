// Web preview: the picked file's own (blob / data) uri is kept as it is.
export async function keepLabFile(_id: string, uri: string, _ext: string): Promise<string> {
  return uri;
}
