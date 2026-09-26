// main.jsx — App 壳：导航 / 主题 / 认证 / 路由

function App() {
  const legacyHistoryPath = window.location.pathname === "/history";
  const initialPathPage = "home";
  const [profileOpen, setProfileOpen] = React.useState(window.location.pathname === "/profile");
  const [pageError, setPageError] = React.useState("");
  const [page, setPage] = React.useState(initialPathPage);
  const [authUser, setAuthUser] = React.useState(() => getAuth()?.username || null);
  const [showAuthModal, setShowAuthModal] = React.useState(false);
  const [authReason, setAuthReason] = React.useState("");
  const [pendingAuthAction, setPendingAuthAction] = React.useState(null);
  const navigationGuardRef = React.useRef(null);
  // 行程详情页数据
  const [detailPlan, setDetailPlan] = React.useState(null);
  const [detailPlanId, setDetailPlanId] = React.useState(null);
  const [revisionTrigger, setRevisionTrigger] = React.useState(null);
  const [homePrompt, setHomePrompt] = React.useState("");
  const [workspaceTransition, setWorkspaceTransition] = React.useState("idle");
  const revisionNonceRef = React.useRef(0);
  const workspaceTransitionTimersRef = React.useRef([]);

  React.useEffect(() => () => {
    workspaceTransitionTimersRef.current.forEach(window.clearTimeout);
  }, []);

  React.useEffect(() => {
    requestAnimationFrame(() => requestAnimationFrame(() => {
      document.documentElement.classList.add("anim-ready");
    }));
    // 处理 URL 参数
    const params = new URLSearchParams(window.location.search);
    if (window.location.pathname === "/profile" && !getAuth()) {
      setAuthReason("请先登录管理旅行画像");
      setShowAuthModal(true);
    }
    if (legacyHistoryPath || window.location.pathname === "/profile") history.replaceState({}, "", "/");
    if (params.get("login") === "1" && !getAuth()) {
      setPendingAuthAction(NavigationState.chatTarget(false));
      setShowAuthModal(true);
      history.replaceState({}, "", "/");
    }
    const viewId = params.get("view_plan_id");
    if (viewId) {
      history.replaceState({}, "", "/");
      const a = getAuth();
      if (!a) {
        setPendingAuthAction(NavigationState.detailTarget(viewId));
        setAuthReason("登录后继续打开这份行程");
        setShowAuthModal(true);
        return;
      }
      getHistoryItem(viewId).then(data => {
        if (data?.plan) {
          const adapted = adaptPlan(data.plan, a.username);
          setDetailPlan(adapted);
          setDetailPlanId(viewId);
          setPage("detail");
        }
      }).catch(error => setPageError(error.message || "行程加载失败"));
    }
  }, []);

  // 页面加载时验证 token 有效性
  React.useEffect(() => {
    checkAuth().then(result => {
      if (!result) setAuthUser(null);
    });
    const onExpired = () => { setAuthUser(null); setDetailPlan(null); setDetailPlanId(null); setPage("home"); setProfileOpen(false); };
    window.addEventListener("auth:expired", onExpired);
    return () => window.removeEventListener("auth:expired", onExpired);
  }, []);

  const go = (p) => {
    if (p !== page && navigationGuardRef.current?.() === false) return;
    setPage(p);
    window.scrollTo({ top: 0 });
  };

  const beginRevision = (query, planId) => {
    setRevisionTrigger({
      itineraryId: planId,
      content: query.trim(),
      nonce: ++revisionNonceRef.current,
    });
    setPage("chat");
    window.scrollTo({ top: 0 });
  };

  const beginFromHome = (prompt) => {
    setHomePrompt(prompt || "");
    setDetailPlan(null); setDetailPlanId(null);
    if (!authUser) {
      requestLogin("登录后开始规划你的旅行", NavigationState.chatTarget());
      return;
    }
    if (workspaceTransition !== "idle") return;
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) { setPage("chat"); window.scrollTo({ top: 0 }); return; }
    setHomePrompt(prompt || "");
    setWorkspaceTransition("home-out");
    const leaveTimer = window.setTimeout(() => {
      setPage("chat");
      window.scrollTo({ top: 0 });
      setWorkspaceTransition("chat-in");
      const settleTimer = window.setTimeout(() => setWorkspaceTransition("idle"), 520);
      workspaceTransitionTimersRef.current.push(settleTimer);
    }, 210);
    workspaceTransitionTimersRef.current.push(leaveTimer);
  };

  const onRequestModify = (query, planId) => {
    const target = NavigationState.revisionTarget(planId, query);
    if (!target) return;
    if (!authUser) {
      requestLogin("登录后继续修改这份行程", target);
      return;
    }
    beginRevision(target.content, target.itineraryId);
  };

  const requestLogin = (reason = "请先登录再继续", continuation = null) => {
    setAuthReason(reason);
    setPendingAuthAction(continuation);
    setShowAuthModal(true);
  };

  const onAuthSuccess = (username) => {
    setAuthUser(username);
    setShowAuthModal(false);
    setAuthReason("");
    const continuation = NavigationState.resolveAfterAuth(pendingAuthAction);
    setPendingAuthAction(null);
    if (!continuation) return;
    if (continuation.page === "detail" && continuation.planId) {
      getHistoryItem(continuation.planId).then(data => {
        if (data?.plan) onOpenHistoryPlan(data.plan, continuation.planId);
      }).catch(error => setPageError(error.message || "行程加载失败"));
      return;
    }
    if (continuation.mode === "revision") {
      beginRevision(continuation.content, continuation.itineraryId);
      return;
    }
    if (continuation.page === "chat") {
      setPage("chat");
      window.scrollTo({ top: 0 });
    }
  };

  const openChat = () => {
    if (page === "chat" || page === "detail") return;
    if (!authUser) {
      requestLogin(
        "登录后继续你的旅行对话",
        NavigationState.chatTarget(),
      );
      return;
    }
    setPage("chat");
    window.scrollTo({ top: 0 });
  };

  const logout = () => {
    if (navigationGuardRef.current?.() === false) return;
    clearAuth();
    setAuthUser(null);
    setDetailPlan(null); setDetailPlanId(null); setProfileOpen(false);
    setPage("home");
  };

  const onOpenHistoryPlan = (rawPlan, planId) => {
    const adapted = adaptPlan(rawPlan, authUser);
    setDetailPlan(adapted);
    setDetailPlanId(planId);
    setPage("detail");
    window.scrollTo({ top: 0 });
  };

  const initial = authUser ? authUser.slice(-1) : "";

  return (
    <div className={`app-shell page-${page} workspace-transition-${workspaceTransition}`}>
      <header className="topbar">
        <div className="brand" role="button" tabIndex="0" aria-label="返回首页" onKeyDown={event => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); go("home"); } }} onClick={() => go("home")}>
          <div className="brand-glyph"><BrandMark size={38} decorative /></div>
          <div>
            <div className="brand-name">途见 <span>FloatTrip</span></div>
            <div className="brand-sub">把旅途，慢慢展开</div>
          </div>
        </div>
        <nav className="topnav">
          <button className={`topnav-link ${page === "home" ? "active" : ""}`}
            onClick={() => go("home")}>
            首页
          </button>
          <button className={`topnav-link ${page === "chat" || page === "detail" ? "active" : ""}`}
            onClick={openChat}>
            旅行工作区
          </button>

        </nav>

        {authUser ? (
          <button className="user-chip" aria-label="打开我的旅行画像" aria-haspopup="dialog" onClick={() => setProfileOpen(true)}>
            <span className="chip-name">{authUser}</span>
            <span className="avatar-dot">{initial}</span>
          </button>
        ) : (
          <button className="user-login-btn" onClick={() => requestLogin("")}>登录 / 注册</button>
        )}
      </header>

      {showAuthModal && (
        <AuthModal
          reason={authReason}
          onSuccess={onAuthSuccess}
          onClose={() => { setShowAuthModal(false); setAuthReason(""); setPendingAuthAction(null); }}
        />
      )}

      {pageError && <div className="chat-error" role="alert">{pageError}<button onClick={() => setPageError("")}>关闭</button></div>}
      {(page === "chat" || page === "detail") && (
        <TripDetailPage
          key={authUser || "guest"}
          navigationGuardRef={navigationGuardRef} profileOpen={profileOpen}
          plan={detailPlan} planId={detailPlanId}
          onRequestModify={onRequestModify} currentUsername={authUser}
          onBack={() => { setPage("home"); window.scrollTo({ top: 0 }); }}
          onRequestLogin={() => requestLogin("登录后继续你的旅行对话", NavigationState.chatTarget())}
          onClearPlan={() => { setDetailPlan(null); setDetailPlanId(null); }}
          initialDraft={homePrompt} onInitialDraftConsumed={() => setHomePrompt("")}
          revisionTrigger={revisionTrigger}
          onRevisionConsumed={nonce => setRevisionTrigger(current => current?.nonce === nonce ? null : current)}
          onPlanChange={(rawPlan, nextPlanId) => { setDetailPlan(adaptPlan(rawPlan, authUser)); setDetailPlanId(nextPlanId); }}
        />
      )}
      {page === "home" && (
        <DashboardPage
          currentUsername={authUser}
          onStart={beginFromHome}
          onFocusComposer={() => beginFromHome("")}
          onDraftChange={setHomePrompt}
          onOpenWorkspace={openChat}
          onOpenPlan={(planId) => getHistoryItem(planId).then(data => {
            if (data?.plan) onOpenHistoryPlan(data.plan, planId);
          }).catch(error => setPageError(error.message || "行程加载失败"))}
        />
      )}
      {profileOpen && authUser && <ProfileModal currentUsername={authUser} onLogout={logout} onClose={() => setProfileOpen(false)} />}

    </div>
  );
}

ReactDOM.createRoot(document.getElementById("root")).render(<App />);
