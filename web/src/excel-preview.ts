export type ExcelExcerpt = { id?: string; locator?: string; text?: string; [key: string]: unknown };
export type ExcelCell = { row: number; column: number; text: string; excerptIds: string[]; excerpts: ExcelExcerpt[] };
export type ExcelSheet = { name: string; cells: ExcelCell[]; rowStart: number; rowEnd: number; columnStart: number; columnEnd: number };
export type ExcelPreviewResult = { sheets: ExcelSheet[]; fallback: ExcelExcerpt[] };

export const EXCEL_PREVIEW_MAX_ROWS = 200;
export const EXCEL_PREVIEW_MAX_COLUMNS = 40;
export const EXCEL_PREVIEW_MAX_GRID_CELLS = 8000;

function columnNumber(letters: string): number {
  let value = 0;
  for (const letter of letters.toUpperCase()) value = value * 26 + letter.charCodeAt(0) - 64;
  return value;
}

export function excelColumnLetters(number: number): string {
  let value = number, letters = '';
  while (value > 0) { value--; letters = String.fromCharCode(65 + value % 26) + letters; value = Math.floor(value / 26); }
  return letters;
}

type LocatedExcerpt = { excerpt: ExcelExcerpt; row: number; column: number; offset?: number };
const LOCATOR = /^工作表「([\s\S]+?)」\s*\/\s*([A-Z]{1,3})([1-9][0-9]*)(?:\s*\/\s*字符\s*(0|[1-9][0-9]*))?$/i;

function locate(excerpt: ExcelExcerpt): LocatedExcerpt | undefined {
  const match = String(excerpt.locator || '').match(LOCATOR);
  if (!match) return undefined;
  const column = columnNumber(match[2]), row = Number(match[3]);
  const offset = match[4] === undefined ? undefined : Number(match[4]);
  if (!match[1] || column < 1 || column > 16384 || row < 1 || row > 1048576 || (offset !== undefined && !Number.isSafeInteger(offset))) return undefined;
  return { excerpt, row, column, offset };
}

function fragmentsAreContiguous(parts: LocatedExcerpt[]): boolean {
  if (parts.length === 1) return parts[0].offset === undefined || parts[0].offset === 0;
  if (parts.some(part => part.offset === undefined)) return false;
  const sorted = [...parts].sort((a, b) => a.offset! - b.offset!);
  if (sorted[0].offset !== 0) return false;
  for (let index = 1; index < sorted.length; index++) {
    const previous = sorted[index - 1], current = sorted[index];
    if (current.offset === previous.offset || current.offset !== previous.offset! + Array.from(previous.excerpt.text || '').length) return false;
  }
  return true;
}

export function excelPreview(excerpts: ExcelExcerpt[], visibleExcerptIds?: string[]): ExcelPreviewResult {
  const visibleIds = visibleExcerptIds ? new Set(visibleExcerptIds) : undefined;
  const isVisible = (excerpt: ExcelExcerpt) => !visibleIds || !excerpt.id || visibleIds.has(excerpt.id);
  const fallbackExcerpts = new Set<ExcelExcerpt>();
  const grouped = new Map<string, { name: string; parts: LocatedExcerpt[] }>();

  for (const excerpt of excerpts) {
    const located = locate(excerpt);
    if (!located) { if (isVisible(excerpt)) fallbackExcerpts.add(excerpt); continue; }
    const sheet = String(excerpt.locator).match(LOCATOR)![1];
    const key = `${sheet}\u0000${located.row}\u0000${located.column}`;
    const group = grouped.get(key) || { name: sheet, parts: [] };
    group.parts.push(located);
    grouped.set(key, group);
  }

  const bySheet = new Map<string, ExcelCell[]>();
  for (const group of grouped.values()) {
    if (!fragmentsAreContiguous(group.parts)) {
      for (const part of group.parts) if (isVisible(part.excerpt)) fallbackExcerpts.add(part.excerpt);
      continue;
    }
    const visibleParts = group.parts.filter(part => isVisible(part.excerpt));
    if (!visibleParts.length) continue;
    if (visibleParts.length !== group.parts.length) {
      for (const part of visibleParts) fallbackExcerpts.add(part.excerpt);
      continue;
    }
    const parts = [...group.parts].sort((a, b) => (a.offset ?? 0) - (b.offset ?? 0));
    const first = parts[0];
    const name = group.name;
    const cell: ExcelCell = { row: first.row, column: first.column, text: parts.map(part => part.excerpt.text || '').join(''), excerptIds: parts.map(part => part.excerpt.id).filter((id): id is string => !!id), excerpts: parts.map(part => part.excerpt) };
    const cells = bySheet.get(name) || [];
    cells.push(cell);
    bySheet.set(name, cells);
  }

  const sheets: ExcelSheet[] = [];
  let totalGridCells = 0;
  for (const [name, cells] of bySheet) {
    const rowStart = Math.min(...cells.map(cell => cell.row)), rowEnd = Math.max(...cells.map(cell => cell.row));
    const columnStart = Math.min(...cells.map(cell => cell.column)), columnEnd = Math.max(...cells.map(cell => cell.column));
    const rows = rowEnd - rowStart + 1, columns = columnEnd - columnStart + 1;
    const gridCells = rows * columns;
    const sparse = gridCells > Math.max(400, cells.length * 12);
    if (rows > EXCEL_PREVIEW_MAX_ROWS || columns > EXCEL_PREVIEW_MAX_COLUMNS || gridCells > EXCEL_PREVIEW_MAX_GRID_CELLS || totalGridCells + gridCells > EXCEL_PREVIEW_MAX_GRID_CELLS || sparse) {
      for (const cell of cells) for (const excerpt of cell.excerpts) fallbackExcerpts.add(excerpt);
      continue;
    }
    totalGridCells += gridCells;
    sheets.push({ name, cells, rowStart, rowEnd, columnStart, columnEnd });
  }

  return { sheets, fallback: excerpts.filter(excerpt => fallbackExcerpts.has(excerpt)) };
}
