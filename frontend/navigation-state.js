// navigation-state.js — 可测试的认证后目标与对话入口规则

(function (global) {
  function chatTarget() {
    return { page: "chat" };
  }

  function detailTarget(planId) {
    return planId ? { page: "detail", planId } : null;
  }

  function revisionTarget(itineraryId, content) {
    const text = String(content || "").trim();
    return itineraryId && text
      ? { page: "chat", mode: "revision", itineraryId, content: text }
      : null;
  }

  function resolveAfterAuth(target) {
    if (!target || !["chat", "detail"].includes(target.page)) return null;
    if (target.page === "detail" && !target.planId) return null;
    if (target.mode === "revision" && (!target.itineraryId || !String(target.content || "").trim())) return null;
    return { ...target };
  }

  global.NavigationState = {
    chatTarget,
    detailTarget,
    revisionTarget,
    resolveAfterAuth,
  };
})(typeof window === "undefined" ? globalThis : window);
