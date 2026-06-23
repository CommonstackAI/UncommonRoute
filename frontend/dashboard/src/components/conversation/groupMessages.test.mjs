import { test } from "node:test";
import assert from "node:assert/strict";

import { group } from "./groupMessages.js";

const userMsg = (text) => ({ role: "user", text });
const asstText = (text, decision = { model: "m" }) => ({
  role: "assistant",
  text,
  tool_calls: [],
  decision,
});
const asstCall = (tools, decision = { model: "m" }) => ({
  role: "assistant",
  text: "",
  tool_calls: tools.map((name) => ({ id: name, name, input: {} })),
  decision,
});
const toolRes = (text) => ({ role: "tool_result", text, tool_use_id: "tu_x" });

test("bare conversation: user + assistant text", () => {
  const out = group({
    messages: [userMsg("hi"), asstText("hello")],
    compact_breaks: [],
  });
  assert.equal(out.length, 2);
  assert.equal(out[0].type, "message");
  assert.equal(out[1].type, "message");
});

test("single agent-loop cycle becomes one tool_group + final assistant", () => {
  const out = group({
    messages: [
      userMsg("do thing"),
      asstCall(["Read"]),
      toolRes("file contents"),
      asstText("done"),
    ],
    compact_breaks: [],
  });
  assert.equal(out.length, 3);
  assert.equal(out[0].type, "message");
  assert.equal(out[1].type, "tool_group");
  assert.equal(out[1].steps.length, 1);
  assert.equal(out[1].steps[0].results.length, 1);
  assert.equal(out[2].type, "message");
});

test("multi-step agent loop folds into one group", () => {
  const out = group({
    messages: [
      userMsg("question"),
      asstCall(["Read"]),
      toolRes("a"),
      asstCall(["Read", "Bash"]),
      toolRes("b"),
      toolRes("c"),
      asstCall(["Read"]),
      toolRes("d"),
      asstText("answer"),
    ],
    compact_breaks: [],
  });
  const groups = out.filter((it) => it.type === "tool_group");
  assert.equal(groups.length, 1);
  assert.equal(groups[0].steps.length, 3);
});

test("tool_result split by intermediate assistant text -> two groups", () => {
  const out = group({
    messages: [
      userMsg("q"),
      asstCall(["Read"]),
      toolRes("a"),
      asstText("interim"),
      asstCall(["Bash"]),
      toolRes("b"),
      asstText("final"),
    ],
    compact_breaks: [],
  });
  const groups = out.filter((it) => it.type === "tool_group");
  assert.equal(groups.length, 2);
});

test("compact_breaks inserts a compact_break item after the k-th captured assistant", () => {
  const out = group({
    messages: [
      userMsg("q1"),
      asstText("a1", { model: "m1" }),
      userMsg("q2"),
      asstText("a2", { model: "m2" }),
      userMsg("q3"),
      asstText("a3", { model: "m3" }),
    ],
    compact_breaks: [2],
  });
  const breakIdx = out.findIndex((it) => it.type === "compact_break");
  assert.notEqual(breakIdx, -1);
  const before = out[breakIdx - 1];
  assert.equal(before.type, "message");
  assert.equal(before.message.text, "a2");
});

test("pre-capture assistant (decision === null) does not increment the compact_break counter", () => {
  const out = group({
    messages: [
      userMsg("q1"),
      asstText("pre-capture reply", null),
      userMsg("q2"),
      asstText("captured 1", { model: "m1" }),
      userMsg("q3"),
      asstText("captured 2", { model: "m2" }),
    ],
    compact_breaks: [1],
  });
  const breakIdx = out.findIndex((it) => it.type === "compact_break");
  const before = out[breakIdx - 1];
  assert.equal(before.message.text, "captured 1");
});

test("pre-capture assistant tool-call still folds into a tool_group", () => {
  const out = group({
    messages: [
      userMsg("q"),
      asstCall(["Read"], null),
      toolRes("a"),
      asstText("done"),
    ],
    compact_breaks: [],
  });
  const grp = out.find((it) => it.type === "tool_group");
  assert.ok(grp);
  assert.equal(grp.steps.length, 1);
  assert.equal(grp.steps[0].assistant.decision, null);
});
