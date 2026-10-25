import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { JsonlAuditSink } from "../src/audit";
import { activate, createDraft, rollback } from "../src/calibration";
import type { EvalRun } from "../src/evalrun";
import type { GoldenCase } from "../src/golden";
import {
  formatRunJsonl,
  JsonlParseError,
  JsonlStore,
  parseJsonl,
  parseRunJsonl,
  readRunFile,
  writeRunFile,
} from "../src/store/jsonl";
import { MemoryStore } from "../src/store/memory";
import { DuplicateIdError, type EvalStore } from "../src/store/types";
import { resetClock, review, triple } from "./fixtures";

let dir: string;

beforeEach(async () => {
  resetClock();
  dir = await mkdtemp(join(tmpdir(), "evalroom-"));
});

afterEach(async () => {
  await rm(dir, { recursive: true, force: true });
});

const golden: GoldenCase = {
  id: "golden-t-1",
  itemId: "t-1",
  input: { message: "hi" },
  referenceOutput: { reply: "hello" },
  expectedRules: [{ path: "reply", op: "exists" }],
  provenance: {
    reviewIds: ["r1", "r2", "r3"],
    promotedBy: "m",
    promotedAt: "2026-10-20T00:00:00.000Z",
  },
  rubricVersion: "v1",
  tenant: "acme",
};

const run: EvalRun = {
  id: "base",
  label: "baseline",
  rubricVersion: "v1",
  createdAt: "2026-10-21T00:00:00.000Z",
  results: [
    { caseId: "golden-t-1", passed: true, failures: [], output: { reply: "hello" } },
    {
      caseId: "golden-t-2",
      passed: false,
      failures: [{ code: "X", path: "reply", detail: "bad" }],
      output: { reply: "?" },
    },
  ],
};

async function exercise(store: EvalStore): Promise<void> {
  await store.addItem({ id: "t-1", output: { reply: "hello" }, tenant: "acme" });
  await store.addItem({ id: "t-2", output: { reply: "bye" } });
  for (const row of [
    ...triple("t-1", "PASS", "PASS", "PASS", { tenant: "acme" }),
    review("t-2", "tone", "FAIL"),
  ]) {
    await store.addReview(row);
  }
  await store.addGolden(golden);
  await store.addRun(run);

  expect((await store.getItem("t-1"))?.output).toEqual({ reply: "hello" });
  expect(await store.getItem("nope")).toBeUndefined();
  expect((await store.listItems({ tenant: "acme" })).map((item) => item.id)).toEqual(["t-1"]);
  expect((await store.listReviews()).map((r) => r.id)).toEqual(["r1", "r2", "r3", "r4"]);
  expect((await store.listReviews({ itemId: "t-1", lens: "tone" })).map((r) => r.id)).toEqual([
    "r2",
  ]);
  expect(await store.listReviews({ tenant: "acme" })).toHaveLength(3);
  expect(await store.getGolden("golden-t-1")).toEqual(golden);
  expect(await store.listGolden({ tenant: "other" })).toEqual([]);
  expect(await store.getRun("base")).toEqual(run);
  expect((await store.listRuns()).map((r) => r.id)).toEqual(["base"]);

  await expect(store.addReview(review("t-1", "tone", "PASS", { id: "r1" }))).rejects.toThrow(
    DuplicateIdError,
  );
  await expect(store.addGolden(golden)).rejects.toThrow(DuplicateIdError);
  await expect(store.addRun(run)).rejects.toThrow(DuplicateIdError);
}

describe("MemoryStore", () => {
  it("implements the EvalStore contract and copies records in and out", async () => {
    const store = new MemoryStore();
    await exercise(store);
    const item = await store.getItem("t-1");
    if (item) item.output = "mutated";
    expect((await store.getItem("t-1"))?.output).toEqual({ reply: "hello" });
  });
});

describe("JsonlStore", () => {
  it("implements the EvalStore contract and survives a fresh instance (round trip)", async () => {
    await exercise(new JsonlStore(dir));
    const reopened = new JsonlStore(dir);
    expect((await reopened.listReviews()).map((r) => r.id)).toEqual(["r1", "r2", "r3", "r4"]);
    expect(await reopened.getRun("base")).toEqual(run);
    const reviewsFile = await readFile(join(dir, "reviews.jsonl"), "utf8");
    expect(reviewsFile.trim().split("\n")).toHaveLength(4);
  });

  it("keeps calibration history append-only and reads the last line per id", async () => {
    const store = new JsonlStore<{ model: string }>(dir);
    const audit = new JsonlAuditSink(join(dir, "audit.jsonl"));
    const one = await createDraft(
      store,
      { config: { model: "a" }, reason: "r", author: "ana" },
      audit,
    );
    if (one.ok) await activate(store, { id: one.value.id, actor: "ben", reason: "ok" }, audit);
    const two = await createDraft(
      store,
      { config: { model: "b" }, reason: "r", author: "ana" },
      audit,
    );
    if (two.ok) await activate(store, { id: two.value.id, actor: "ben", reason: "ok" }, audit);
    await rollback(store, { actor: "ben", reason: "regressed" }, audit);

    const fresh = new JsonlStore<{ model: string }>(dir);
    expect((await fresh.listVersions()).map((v) => [v.id, v.status])).toEqual([
      ["cal-1", "active"],
      ["cal-2", "rolled_back"],
    ]);
    const lines = (await readFile(join(dir, "calibration.jsonl"), "utf8")).trim().split("\n");
    expect(lines.length).toBeGreaterThan(2);
    expect(
      (await new JsonlAuditSink(join(dir, "audit.jsonl")).list()).map((e) => e.action),
    ).toEqual([
      "calibration.draft",
      "calibration.activate",
      "calibration.draft",
      "calibration.activate",
      "calibration.rollback",
    ]);
  });

  it("refuses run ids that are not safe file names", async () => {
    await expect(new JsonlStore(dir).addRun({ ...run, id: "../escape" })).rejects.toThrow(
      RangeError,
    );
  });
});

describe("JSONL helpers", () => {
  it("skips blank lines, accepts CRLF and a BOM, and reports the failing line", () => {
    expect(parseJsonl('﻿{"a":1}\r\n\r\n{"a":2}\n')).toEqual([{ a: 1 }, { a: 2 }]);
    try {
      parseJsonl('{"a":1}\n{oops}\n', "reviews.jsonl");
      expect.unreachable();
    } catch (error) {
      expect(error).toBeInstanceOf(JsonlParseError);
      expect((error as JsonlParseError).line).toBe(2);
      expect((error as JsonlParseError).message).toMatch(/^reviews\.jsonl:2: /);
    }
  });

  it("round-trips a run file with its header, and tolerates a headerless file", async () => {
    const path = join(dir, "out", "run.jsonl");
    await writeRunFile(path, run);
    expect(await readRunFile(path)).toEqual(run);
    expect(formatRunJsonl(run).split("\n")[0]).toContain('"kind":"run"');

    const headerless = join(dir, "candidate.jsonl");
    await writeFile(headerless, '{"caseId":"a","passed":true}\n');
    expect(await readRunFile(headerless)).toEqual({
      id: "candidate",
      label: "candidate",
      rubricVersion: "unknown",
      results: [{ caseId: "a", passed: true, failures: [], output: null }],
    });
  });

  it("rejects malformed result lines and duplicate case ids", () => {
    expect(() => parseRunJsonl('{"caseId":"a","passed":"yes"}', "r.jsonl")).toThrow(
      /r\.jsonl:1: passed/,
    );
    expect(() =>
      parseRunJsonl('{"caseId":"a","passed":true}\n{"caseId":"a","passed":false}'),
    ).toThrow(/duplicate/);
  });
});
