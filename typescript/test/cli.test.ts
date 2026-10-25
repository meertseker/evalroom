import { execFile } from "node:child_process";
import { copyFile, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { createRequire } from "node:module";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { promisify } from "node:util";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { stripAnsi, table, visibleLength } from "../src/cli/format";
import { type CliIO, EXIT, main } from "../src/cli/main";

const root = resolve(import.meta.dirname, "..");
const example = join(root, "..", "conformance", "data");
const run = promisify(execFile);
const tsxCli = createRequire(import.meta.url).resolve("tsx/cli");

let dir: string;

beforeEach(async () => {
  dir = await mkdtemp(join(tmpdir(), "evalroom-cli-"));
});

afterEach(async () => {
  await rm(dir, { recursive: true, force: true });
});

async function cli(args: string[], env: Record<string, string> = {}) {
  let stdout = "";
  let stderr = "";
  const io: CliIO = {
    stdout: (text) => {
      stdout += text;
    },
    stderr: (text) => {
      stderr += text;
    },
    env: { NO_COLOR: "1", ...env },
    isTTY: false,
    cwd: example,
  };
  const code = await main(args, io);
  return { code, stdout, stderr };
}

describe("cli: agreement", () => {
  it("prints kappa, raw agreement with interval, pairs and disagreements", async () => {
    const { code, stdout } = await cli(["agreement", "reviews.jsonl", "--rubric", "rubric.json"]);
    expect(code).toBe(EXIT.ok);
    expect(stdout).toContain("rubric 2026-10.1");
    expect(stdout).toContain(
      "19 complete items · 1 incomplete · 2 reviews from other rubric versions excluded",
    );
    expect(stdout).toMatch(/Raw agreement\s+58% \(11\/19\) · 36%–77% · not decidable/);
    expect(stdout).toContain("Disagreements (8)");
    expect(stdout).not.toContain("\u001b[");
  });

  it("emits the full report with --json and honours --version", async () => {
    const { stdout } = await cli([
      "agreement",
      "reviews.jsonl",
      "--rubric",
      "rubric.json",
      "--json",
    ]);
    const report = JSON.parse(stdout);
    expect(report.items).toBe(19);
    expect(report.rawAgreement).toMatchObject({ k: 11, n: 19 });
    const old = JSON.parse(
      (
        await cli([
          "agreement",
          "reviews.jsonl",
          "--rubric",
          "rubric.json",
          "--version",
          "2026-09.3",
          "--json",
        ])
      ).stdout,
    );
    expect(old.items).toBe(0);
    expect(old.incompleteItems).toBe(1);
  });

  it("returns 64 on usage errors and 65 on bad input, with line numbers", async () => {
    expect((await cli(["agreement", "reviews.jsonl"])).code).toBe(EXIT.usage);
    expect((await cli(["frobnicate"])).code).toBe(EXIT.usage);
    expect((await cli(["agreement", "nope.jsonl", "--rubric", "rubric.json"])).code).toBe(
      EXIT.data,
    );
    const bad = join(dir, "bad.jsonl");
    await writeFile(
      bad,
      '{"id":"x","itemId":"t-01","lens":"tone","verdict":"FAIL","reviewer":"b","rubricVersion":"2026-10.1","createdAt":"2026-10-01T00:00:00Z"}\n',
    );
    const result = await cli(["agreement", bad, "--rubric", "rubric.json"]);
    expect(result.code).toBe(EXIT.data);
    expect(result.stderr).toContain("bad.jsonl:1: a FAIL requires errorClass");
  });
});

describe("cli: gate, compare, score, golden", () => {
  it("gate exits 2 when undecidable, 0 on pass and 1 on fail", async () => {
    const undecidable = await cli(["gate", "run-candidate.jsonl", "--min-pass", "0.8"]);
    expect(undecidable.code).toBe(EXIT.undecidable);
    expect(undecidable.stdout).toContain("UNDECIDABLE");
    const loose = ["--min-n", "5", "--max-width", "1"];
    expect((await cli(["gate", "run-candidate.jsonl", "--min-pass", "0.5", ...loose])).code).toBe(
      EXIT.ok,
    );
    const failed = await cli(["gate", "run-base.jsonl", "--min-pass", "0.9", ...loose, "--json"]);
    expect(failed.code).toBe(EXIT.fail);
    expect(JSON.parse(failed.stdout)).toMatchObject({
      decision: "fail",
      interval: { k: 7, n: 11 },
    });
  });

  it("compare lists broken and fixed cases and exits 1 when something broke", async () => {
    const result = await cli(["compare", "run-base.jsonl", "run-candidate.jsonl"]);
    expect(result.code).toBe(EXIT.fail);
    expect(result.stdout).toMatch(/broken\s+1\s+golden-t-08/);
    expect(result.stdout).toMatch(/fixed\s+3\s+golden-t-02, golden-t-06, golden-t-17/);
    const json = JSON.parse(
      (await cli(["compare", "run-base.jsonl", "run-base.jsonl", "--json"])).stdout,
    );
    expect(json.broken).toEqual([]);
  });

  it("score writes a run file that gate and compare can read", async () => {
    const out = join(dir, "run.jsonl");
    const result = await cli([
      "score",
      "golden.jsonl",
      "outputs-candidate.jsonl",
      "--out",
      out,
      "--id",
      "c2",
      "--label",
      "again",
    ]);
    expect(result.code).toBe(EXIT.ok);
    expect(result.stdout).toContain("82% (9/11)");
    const header = JSON.parse((await readFile(out, "utf8")).split("\n")[0] ?? "{}");
    expect(header).toMatchObject({
      kind: "run",
      id: "c2",
      label: "again",
      rubricVersion: "2026-10.1",
    });
    expect((await cli(["compare", "run-candidate.jsonl", out])).code).toBe(EXIT.ok);
  });

  it("golden promote appends a case, audits it, refuses duplicates and split items", async () => {
    const out = join(dir, "golden.jsonl");
    const audit = join(dir, "audit.jsonl");
    const rules = join(dir, "rules.json");
    await writeFile(
      rules,
      JSON.stringify([{ path: "reply", op: "notContains", value: "guarantee" }]),
    );
    const base = [
      "golden",
      "promote",
      "t-01",
      "--items",
      "items.jsonl",
      "--reviews",
      "reviews.jsonl",
      "--rubric",
      "rubric.json",
      "--out",
      out,
      "--rules",
      rules,
      "--by",
      "qa",
    ];
    const ok = await cli([...base, "--audit", audit]);
    expect(ok.code).toBe(EXIT.ok);
    expect(ok.stdout).toContain("promoted golden-t-01");
    expect(JSON.parse(await readFile(out, "utf8"))).toMatchObject({
      id: "golden-t-01",
      provenance: { promotedBy: "qa" },
    });
    expect(JSON.parse(await readFile(audit, "utf8"))).toMatchObject({
      action: "golden.promote",
      actor: "qa",
    });
    expect((await cli(base)).code).toBe(EXIT.data);

    const split = await cli([...base.slice(0, 2), "t-03", ...base.slice(3), "--json"]);
    expect(split.code).toBe(EXIT.fail);
    expect(JSON.parse(split.stdout).refusals[0].code).toBe("NOT_UNANIMOUS_PASS");
    const noRules = await cli([
      "golden",
      "promote",
      "t-02",
      "--items",
      "items.jsonl",
      "--reviews",
      "reviews.jsonl",
      "--rubric",
      "rubric.json",
      "--out",
      out,
    ]);
    expect(noRules.stdout).toContain("NO_RULES");
  });
});

describe("cli: format", () => {
  it("aligns columns by visible width, ignoring ANSI codes", () => {
    const red = "\u001b[31mFAIL\u001b[39m";
    expect(visibleLength(red)).toBe(4);
    expect(
      stripAnsi(
        table([
          ["a", red, "x"],
          ["longer", "PASS", "y"],
        ]),
      ),
    ).toBe("  a       FAIL  x\n  longer  PASS  y");
  });
});

describe("cli: child process smoke", () => {
  it("runs the binary end to end and returns the gate exit code", async () => {
    const options = { cwd: example, env: { ...process.env, NO_COLOR: "1" } };
    const agreement = await run(
      process.execPath,
      [
        tsxCli,
        join(root, "src/cli/index.ts"),
        "agreement",
        "reviews.jsonl",
        "--rubric",
        "rubric.json",
      ],
      options,
    );
    expect(agreement.stdout).toContain("Fleiss' kappa");
    const version = await run(
      process.execPath,
      [tsxCli, join(root, "src/cli/index.ts"), "--version"],
      options,
    );
    expect(version.stdout.trim()).toBe("0.1.0");
    await copyFile(join(example, "run-candidate.jsonl"), join(dir, "run.jsonl"));
    const gated = await run(
      process.execPath,
      [tsxCli, join(root, "src/cli/index.ts"), "gate", join(dir, "run.jsonl")],
      options,
    ).then(
      () => 0,
      (error: { code?: number }) => error.code,
    );
    expect(gated).toBe(EXIT.undecidable);
  });
});
