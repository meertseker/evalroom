import { describe, expect, it } from "vitest";
import { MemoryAuditSink } from "../src/audit";
import {
  activate,
  activeVersion,
  createDraft,
  diffValues,
  rollback,
  snapshot,
} from "../src/calibration";
import { MemoryStore } from "../src/store/memory";

type Config = { model: string; temperature: number; stop?: string[] };

async function seeded() {
  const store = new MemoryStore<Config>();
  const audit = new MemoryAuditSink();
  const v1 = await createDraft(
    store,
    {
      config: { model: "m-1", temperature: 0.2 },
      reason: "initial",
      author: "ana",
      at: "2026-10-01T00:00:00.000Z",
    },
    audit,
  );
  if (!v1.ok) throw new Error("seed failed");
  const a1 = await activate(
    store,
    { id: v1.value.id, actor: "ben", reason: "approved", at: "2026-10-02T00:00:00.000Z" },
    audit,
  );
  if (!a1.ok) throw new Error("seed failed");
  return { store, audit };
}

describe("snapshot", () => {
  it("deep-copies, sorts keys, drops undefined and freezes", () => {
    const source = { b: 1, a: { d: [1, 2], c: "x" }, gone: undefined };
    const snap = snapshot(source);
    expect(JSON.stringify(snap)).toBe('{"a":{"c":"x","d":[1,2]},"b":1}');
    expect(Object.isFrozen(snap)).toBe(true);
    expect(Object.isFrozen(snap.a.d)).toBe(true);
    source.a.c = "changed";
    expect(snap.a.c).toBe("x");
  });

  it("rejects values that are not plain JSON", () => {
    const cyclic: Record<string, unknown> = {};
    cyclic.self = cyclic;
    expect(() => snapshot(cyclic)).toThrow(/circular/);
    expect(() => snapshot({ f: () => 1 })).toThrow(TypeError);
    expect(() => snapshot({ when: new Date() })).toThrow(TypeError);
    expect(() => snapshot({ n: Number.NaN })).toThrow(/finite/);
  });
});

describe("diffValues", () => {
  it("writes one line per changed leaf, old to new", () => {
    expect(
      diffValues(
        { model: "m-1", temperature: 0.2, retry: { max: 3 } },
        { model: "m-2", temperature: 0.2, retry: { max: 5 }, stop: ["\n"] },
      ),
    ).toEqual(['model: "m-1" → "m-2"', "retry.max: 3 → 5", 'stop: (absent) → ["\\n"]']);
  });

  it("returns nothing for equal values and compares arrays whole", () => {
    expect(diffValues({ a: [1, { b: 2 }] }, { a: [1, { b: 2 }] })).toEqual([]);
    expect(diffValues({ a: [1, 2] }, { a: [2, 1] })).toEqual(["a: [1,2] → [2,1]"]);
    expect(diffValues(1, 2)).toEqual(["(root): 1 → 2"]);
  });
});

describe("calibration lifecycle", () => {
  it("drafts with a base version, activates with four eyes, supersedes the previous one", async () => {
    const { store, audit } = await seeded();
    const draft = await createDraft(
      store,
      { config: { model: "m-2", temperature: 0.2 }, reason: "try m-2", author: "ana" },
      audit,
    );
    expect(draft.ok && draft.value).toMatchObject({
      id: "cal-2",
      version: 2,
      status: "draft",
      baseVersionId: "cal-1",
    });

    const self = await activate(store, { id: "cal-2", actor: "ana", reason: "ship it" }, audit);
    expect(!self.ok && self.error.code).toBe("SELF_ACTIVATION");

    const done = await activate(store, { id: "cal-2", actor: "ben", reason: "kappa held" }, audit);
    expect(done.ok && done.value.status).toBe("active");
    expect((await store.listVersions()).map((v) => [v.id, v.status])).toEqual([
      ["cal-1", "superseded"],
      ["cal-2", "active"],
    ]);
    expect((await audit.list()).map((event) => event.action)).toEqual([
      "calibration.draft",
      "calibration.activate",
      "calibration.draft",
      "calibration.activate",
    ]);
  });

  it("refuses to activate a missing or non-draft version, or without a reason", async () => {
    const { store } = await seeded();
    expect(await activate(store, { id: "nope", actor: "ben", reason: "x" })).toMatchObject({
      ok: false,
      error: { code: "NOT_FOUND" },
    });
    expect(await activate(store, { id: "cal-1", actor: "ben", reason: "x" })).toMatchObject({
      ok: false,
      error: { code: "NOT_DRAFT" },
    });
    expect(await activate(store, { id: "cal-1", actor: "ben", reason: " " })).toMatchObject({
      ok: false,
      error: { code: "REASON_REQUIRED" },
    });
    expect(
      await createDraft(store, { config: { model: "x", temperature: 0 }, reason: "", author: "a" }),
    ).toMatchObject({ ok: false, error: { code: "REASON_REQUIRED" } });
  });

  it("rolls back to the most recent superseded version and audits it", async () => {
    const { store, audit } = await seeded();
    for (const [model, author] of [
      ["m-2", "ana"],
      ["m-3", "ana"],
    ] as const) {
      const draft = await createDraft(store, {
        config: { model, temperature: 0.2 },
        reason: model,
        author,
      });
      if (draft.ok) await activate(store, { id: draft.value.id, actor: "ben", reason: "go" });
    }
    const restored = await rollback(
      store,
      { actor: "ben", reason: "m-3 regressed on tone" },
      audit,
    );
    expect(restored.ok && restored.value.id).toBe("cal-2");
    expect((await activeVersion(store))?.config.model).toBe("m-2");
    expect((await store.listVersions()).map((v) => v.status)).toEqual([
      "superseded",
      "active",
      "rolled_back",
    ]);
    const last = (await audit.list({ action: "calibration.rollback" }))[0];
    expect(last).toMatchObject({ actor: "ben", target: "cal-2", reason: "m-3 regressed on tone" });
  });

  it("refuses a rollback with nothing to restore", async () => {
    const empty = new MemoryStore<Config>();
    expect(await rollback(empty, { actor: "ben", reason: "x" })).toMatchObject({
      ok: false,
      error: { code: "NO_ACTIVE" },
    });
    const { store } = await seeded();
    expect(await rollback(store, { actor: "ben", reason: "x" })).toMatchObject({
      ok: false,
      error: { code: "NO_PREVIOUS" },
    });
  });
});
