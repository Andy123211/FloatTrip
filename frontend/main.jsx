// main.jsx — App 壳：导航 / 主题 / 认证 / 路由

const TWEAK_DEFAULTS = /*EDITMODE-BEGIN*/{
  "theme": "morning",
  "mascot": true
}/*EDITMODE-END*/;

const THEME_OPTIONS = [
  { value: "morning", label: "晨光 · 蜂蜜黄" },
  { value: "celadon", label: "青野 · 嫩芽绿" },
  { value: "night",   label: "夜旅 · 可可棕" },
  { value: "sky",     label: "晴日 · 奶油白" },
];
const THEME_ICONS = { morning: "sun", celadon: "leaf", night: "moon", sky: "sparkle" };

function App() {
  const [t, setTweak] = useTweaks(TWEAK_DEFAULTS);
  const legacyHistoryPath = window.location.pathname === "/history";
  const initialPathPage = window.location.pathname === "/profile" ? "profile" : "home";
  const [page, setPage] = React.useState(initialPathPage);
  const [authUser, setAuthUser] = React.useState(() => getAuth()?.username || null);
  const [showAuthModal, setShowAuthModal] = React.useState(false);
  const [authReason, setAuthReason] = React.useState("");
  const [pendingAuthAction, setPendingAuthAction] = React.useState(null);
  const [showUserMenu, setShowUserMenu] = React.useState(false);
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
    if (initialPathPage === "profile" && !getAuth()) {
      setAuthReason("请先登录管理旅行画像");
      setShowAuthModal(true);
    }
    if (legacyHistoryPath) history.replaceState({}, "", "/");
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
      }).catch(() => {});
    }
  }, []);

  // 页面加载时验证 token 有效性
  React.useEffect(() => {
    checkAuth().then(result => {
      if (!result) setAuthUser(null);
    });
    const onExpired = () => setAuthUser(null);
    window.addEventListener("auth:expired", onExpired);
    return () => window.removeEventListener("auth:expired", onExpired);
  }, []);

  React.useEffect(() => {
    document.documentElement.setAttribute("data-theme", t.theme || "morning");
  }, [t.theme]);

  React.useEffect(() => {
    document.documentElement.setAttribute("data-mascot", t.mascot ? "on" : "off");
  }, [t.mascot]);

  // 点击空白关闭用户菜单（mousedown 避免与按钮 click 冲突）
  React.useEffect(() => {
    if (!showUserMenu) return;
    const handler = (e) => {
      if (!e.target.closest(".user-chip")) {
        setShowUserMenu(false);
      }
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [showUserMenu]);

  const go = (p) => {
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
    if (!authUser) {
      requestLogin("登录后开始规划你的旅行", NavigationState.chatTarget());
      return;
    }
    if (workspaceTransition !== "idle") return;
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
      }).catch(() => {});
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
    clearAuth();
    setAuthUser(null);
    setShowUserMenu(false);
    go("chat");
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
      {page !== "detail" && <header className="topbar">
        <div className="brand" onClick={() => go("home")}>
          <div className="brand-glyph"><img src="/favicon.png?v=20260830-duck-guide" alt="" /></div>
          <div>
            <div className="brand-name">途见 · AI 旅行规划</div>
            <div className="brand-sub">Travel Journal by Agents</div>
          </div>
        </div>
        <nav className="topnav">
          <button className={`topnav-link ${page === "home" ? "active" : ""}`}
            onClick={() => go("home")}>
            首页
          </button>
          <button className={`topnav-link ${page === "chat" ? "active" : ""}`}
            onClick={openChat}>
            旅行工作区
          </button>
          <button className={`topnav-link ${page === "profile" ? "active" : ""}`}
            onClick={() => { if (!authUser) { requestLogin("请先登录管理旅行画像"); return; } go("profile"); }}>
            我的画像
          </button>
          <button className={`topnav-link sweep-nav-link ${page === "sweep" ? "active" : ""}`} onClick={() => go("sweep")}>
            <UiIcon name="flask" size={15} />测试
          </button>
        </nav>

        <div className="theme-switcher" role="group" aria-label="切换主题">
          {THEME_OPTIONS.map(({ value, label }) => (
            <button
              key={value}
              className={`theme-seg${t.theme === value ? " active" : ""}`}
              title={label}
              aria-label={label}
              aria-pressed={t.theme === value}
              onClick={() => setTweak("theme", value)}
            >
              <UiIcon name={THEME_ICONS[value]} size={15} />
            </button>
          ))}
        </div>

        {authUser ? (
          <div className="user-chip" onClick={(e) => { e.stopPropagation(); setShowUserMenu(v => !v); }}>
            <span className="chip-name">{authUser}</span>
            <span className="avatar-dot">{initial}</span>
            {showUserMenu && (
              <div className="user-dropdown" onClick={e => e.stopPropagation()}>
                <button onClick={logout}>退出登录</button>
              </div>
            )}
          </div>
        ) : (
          <button className="user-login-btn" onClick={() => requestLogin("")}>登录 / 注册</button>
        )}
      </header>}

      {showAuthModal && (
        <AuthModal
          reason={authReason}
          onSuccess={onAuthSuccess}
          onClose={() => { setShowAuthModal(false); setAuthReason(""); setPendingAuthAction(null); }}
        />
      )}

      {page === "detail" && detailPlan && (
        <TripDetailPage
          plan={detailPlan}
          planId={detailPlanId}
          onRequestModify={onRequestModify}
          currentUsername={authUser}
          onBack={() => go("chat")}
          onPlanChange={(rawPlan, nextPlanId) => {
            setDetailPlan(adaptPlan(rawPlan, authUser));
            setDetailPlanId(nextPlanId);
          }}
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
          })}
        />
      )}
      {page === "chat" && (
        <ChatPage
          currentUsername={authUser}
          onRequestLogin={() => requestLogin("登录后继续你的旅行对话", NavigationState.chatTarget())}
          revisionTrigger={revisionTrigger}
          initialDraft={homePrompt}
          onInitialDraftConsumed={() => setHomePrompt("")}
          onRevisionConsumed={(nonce) => {
            setRevisionTrigger(current => current?.nonce === nonce ? null : current);
          }}
          onOpenPlan={(planId) => {
            getHistoryItem(planId).then(data => {
              if (data?.plan) onOpenHistoryPlan(data.plan, planId);
            });
          }}
        />
      )}
      {page === "profile" && (
        <ProfilePage currentUsername={authUser} />
      )}
      {page === "sweep" && (
        <SweepPreviewPage />
      )}

      {page !== "detail" && <TweaksPanel>
        <TweakSection label="整体方案" />
        <TweakSelect
          label="主题"
          value={t.theme}
          options={THEME_OPTIONS}
          onChange={(v) => setTweak("theme", v)}
        />
        <TweakSection label="虚拟形象" />
        <TweakToggle label="显示向导「途途」" value={t.mascot} onChange={(v) => setTweak("mascot", v)} />
      </TweaksPanel>}
    </div>
  );
}

ReactDOM.createRoot(document.getElementById("root")).render(<App />);
