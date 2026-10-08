interface ClipboardTransferData {
  items?: ArrayLike<Pick<DataTransferItem, 'kind' | 'getAsFile'>> | null;
  files?: ArrayLike<File> | null;
}

interface DropTransferData {
  files?: ArrayLike<File> | null;
}

function uniqueFiles(files: ArrayLike<File>): File[] {
  return Array.from(new Set(Array.from(files)));
}

export function clipboardFiles(data: ClipboardTransferData): File[] {
  const itemFiles = Array.from(data.items || [])
    .filter((item) => item.kind === 'file')
    .map((item) => item.getAsFile())
    .filter((file): file is File => file !== null);

  return itemFiles.length > 0
    ? uniqueFiles(itemFiles)
    : uniqueFiles(data.files || []);
}

export function droppedFiles(data: DropTransferData): File[] {
  return uniqueFiles(data.files || []);
}

export function hasFileTransfer(types: ArrayLike<string>): boolean {
  return Array.from(types).includes('Files');
}
