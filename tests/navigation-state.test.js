const test = require("node:test");
const assert = require("node:assert/strict");

require("../frontend/navigation-state.js");

test("restores a protected chat target after authentication", () => {
  const target = NavigationState.chatTarget();
  assert.deepEqual(NavigationState.resolveAfterAuth(target), {
    page: "chat",
  });
});

test("rejects incomplete detail targets and closed authentication state", () => {
  assert.equal(NavigationState.resolveAfterAuth(null), null);
  assert.equal(NavigationState.resolveAfterAuth({ page: "detail" }), null);
  assert.deepEqual(
    NavigationState.resolveAfterAuth(NavigationState.detailTarget("plan-1")),
    { page: "detail", planId: "plan-1" },
  );
});

test("preserves itinerary and modification text through authentication", () => {
  const target = NavigationState.revisionTarget("plan-1", "  第二天安排轻松一点  ");

  assert.deepEqual(NavigationState.resolveAfterAuth(target), {
    page: "chat",
    mode: "revision",
    itineraryId: "plan-1",
    content: "第二天安排轻松一点",
  });
  assert.equal(NavigationState.revisionTarget("plan-1", "   "), null);
  assert.equal(NavigationState.revisionTarget("", "调整行程"), null);
});
