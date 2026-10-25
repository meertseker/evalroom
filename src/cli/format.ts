/**
 * Terminal formatting for the CLI: ANSI colour (respecting NO_COLOR / FORCE_COLOR) and plain
 * text tables. No dependencies.
 *
 * @module
 */

/** Colour helpers. Every function is the identity when colour is disabled. */
export interface Colors {
  /** Bold text. */
  bold(text: string): string;
  /** Dim text. */
  dim(text: string): string;
  /** Red text. */
  red(text: string): string;
  /** Green text. */
  green(text: string): string;
  /** Yellow text. */
  yellow(text: string): string;
  /** Cyan text. */
  cyan(text: string): string;
}

function wrap(open: number, close: number, enabled: boolean): (text: string) => string {
  return enabled ? (text) => `\u001b[${open}m${text}\u001b[${close}m` : (text) => text;
}

/** Builds {@link Colors}; all identity functions when `enabled` is false. */
export function createColors(enabled: boolean): Colors {
  return {
    bold: wrap(1, 22, enabled),
    dim: wrap(2, 22, enabled),
    red: wrap(31, 39, enabled),
    green: wrap(32, 39, enabled),
    yellow: wrap(33, 39, enabled),
    cyan: wrap(36, 39, enabled),
  };
}

/**
 * Decides whether to colour output: `NO_COLOR` (any value) disables, `FORCE_COLOR` (other than
 * `"0"`) enables, otherwise colour follows whether the stream is a TTY.
 */
export function colorEnabled(
  isTTY: boolean,
  env: Readonly<Record<string, string | undefined>>,
): boolean {
  if (env.NO_COLOR !== undefined && env.NO_COLOR !== "") return false;
  if (env.FORCE_COLOR !== undefined && env.FORCE_COLOR !== "0") return true;
  return isTTY;
}

const ANSI = new RegExp(`${String.fromCharCode(27)}\\[[0-9;]*m`, "g");

/** Length of `text` as displayed, ignoring ANSI escape codes. */
export function visibleLength(text: string): number {
  return text.replace(ANSI, "").length;
}

/** Removes ANSI escape codes. */
export function stripAnsi(text: string): string {
  return text.replace(ANSI, "");
}

/** Options for {@link table}. */
export interface TableOptions {
  /** Header row, rendered above a rule. */
  header?: string[];
  /** Per-column alignment. Default left. */
  align?: Array<"left" | "right">;
  /** Left indent for every line. Default two spaces. */
  indent?: string;
  /** Space between columns. Default two spaces. */
  gap?: string;
}

/** Renders rows as a fixed-width text table. ANSI codes do not disturb alignment. */
export function table(rows: string[][], options: TableOptions = {}): string {
  const indent = options.indent ?? "  ";
  const gap = options.gap ?? "  ";
  const all = options.header ? [options.header, ...rows] : rows;
  const widths: number[] = [];
  for (const row of all) {
    row.forEach((cell, i) => {
      widths[i] = Math.max(widths[i] ?? 0, visibleLength(cell));
    });
  }
  const render = (row: string[]) =>
    indent +
    row
      .map((cell, i) => {
        const pad = " ".repeat((widths[i] ?? 0) - visibleLength(cell));
        if (i === row.length - 1 && options.align?.[i] !== "right") return cell;
        return options.align?.[i] === "right" ? pad + cell : cell + pad;
      })
      .join(gap);
  const lines: string[] = [];
  if (options.header) {
    lines.push(render(options.header));
    lines.push(indent + widths.map((width) => "─".repeat(width)).join(gap));
  }
  for (const row of rows) lines.push(render(row));
  return lines.map((line) => line.replace(/\s+$/, "")).join("\n");
}
