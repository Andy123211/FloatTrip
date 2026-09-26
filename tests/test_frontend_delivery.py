from fastapi.testclient import TestClient

from app.main import app


def test_frontend_html_disables_cache_and_versions_all_local_assets():
    response = TestClient(app).get("/")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store, max-age=0"
    html = response.text
    assert '<link rel="icon" type="image/svg+xml" href="/assets/brand/horizon.svg?v=20260919-horizon" />' in html
    assert '<link rel="apple-touch-icon" href="/favicon.png?v=20260919-horizon" />' in html
    for asset in (
        "style.css", "api.js", "chat-state.js", "navigation-state.js",
        "brand.jsx", "components.jsx", "edit.jsx",
        "pages.jsx", "main.jsx",
    ):
        assert f'/{asset}?v=20260919-horizon' in html


def test_frontend_serves_the_touch_icon():
    response = TestClient(app).get("/favicon.png?v=20260919-horizon")

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.headers["cache-control"] == "no-cache, must-revalidate"
    assert response.content.startswith(b"\x89PNG\r\n\x1a\n")


def test_trip_redesign_serves_its_local_nanjing_cover():
    response = TestClient(app).get("/assets/trip-redesign/nanjing-cover.png")

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.content.startswith(b"\x89PNG\r\n\x1a\n")


def test_frontend_scripts_are_revalidated():
    response = TestClient(app).get("/pages.jsx?v=20260919-horizon")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-cache, must-revalidate"
    assert "function PlanningBriefCard" in response.text
    assert "RuntimeRunCard" not in response.text
    assert "PlanningProgressTimeline" in response.text
    assert "规划进行中" in response.text


def test_protected_api_401_expires_the_stale_browser_session():
    api = TestClient(app).get("/api.js?v=20260919-horizon").text

    assert "if (r.status === 401) clearAuth();" in api
    assert 'error.code = r.status === 401 ? "auth_expired"' in api


def test_conversation_ui_contains_persisted_accessible_itinerary_cards():
    response = TestClient(app).get("/pages.jsx?v=20260807-conversation-attention")

    assert response.status_code == 200
    assert "function MessageArtifacts" in response.text
    assert "saved-itinerary-card" in response.text
    assert "onOpen?.(card.itinerary_id)" in response.text
    assert "聊聊这份行程" in response.text
    assert "onReference(card)" in response.text
    assert "ItineraryMentionInput" in response.text


def test_chat_page_integrates_paginated_history_plans_and_legacy_route_redirect():
    client = TestClient(app)
    pages = client.get("/pages.jsx?v=20260919-horizon").text
    main = client.get("/main.jsx?v=20260919-horizon").text
    api = client.get("/api.js?v=20260919-horizon").text

    assert "function JourneyPlans" in pages
    assert "getHistoryPage(6, cursor)" in pages
    assert 'id="my-plans"' in pages
    assert "journey-plan-card" in pages
    assert "IntersectionObserver(entries" in pages
    assert "async function getHistoryPage" in api
    assert "legacyHistoryPath" in main
    assert 'history.replaceState({}, "", "/")' in main
    assert 'go("history")' not in main


def test_itinerary_revision_uses_chat_once_and_keeps_manual_retry_state():
    pages = TestClient(app).get("/pages.jsx?v=20260807-conversation-attention").text
    main = TestClient(app).get("/main.jsx?v=20260807-conversation-attention").text
    api = TestClient(app).get("/api.js?v=20260807-conversation-attention").text

    assert "consumedRevisionNoncesRef.current.has(nonce)" in pages
    assert "ChatState.itineraryMessage(content, target, relatedPlanId)" in pages
    assert 'setError(e.message || "修改请求发送失败，请重试")' in pages
    assert "setDraft(content)" in pages
    assert 'composerTarget?.mode === "revision" && (!conversationId || conversationArchived)' in pages
    assert "NavigationState.revisionTarget(planId, query)" in main
    assert "PlanPage" not in pages + main
    assert "streamPlan" not in api
    assert "confirmModification" not in api


def test_itinerary_detail_subscribes_to_async_tip_updates_without_remounting():
    pages = TestClient(app).get("/pages.jsx?v=20260807-conversation-attention").text
    components = TestClient(app).get("/components.jsx?v=20260807-conversation-attention").text
    api = TestClient(app).get("/api.js?v=20260807-conversation-attention").text

    assert "streamItineraryTips(planId" in pages
    assert "itinerary.tip_status_changed" in pages
    assert "setPlan(previous => ({ ...adapted" in pages
    assert "重新生成景点贴士" in pages
    assert "途见正在整理贴士…" in components
    assert "路线已更新，贴士尚未更新" in components
    assert "async function streamItineraryTips" in api
    assert "retryItineraryTips" in api


def test_itinerary_detail_uses_the_redesign_workspace_with_live_core_features():
    client = TestClient(app)
    pages = client.get("/pages.jsx?v=20260919-horizon").text
    redesign = client.get("/detail-redesign.jsx?v=20260919-horizon").text
    main = client.get("/main.jsx?v=20260919-horizon").text
    components = client.get("/components.jsx?v=20260919-horizon").text
    api = client.get("/api.js?v=20260919-horizon").text
    css = client.get("/style.css?v=20260919-horizon").text
    html = client.get("/").text

    assert '<header className="topbar">' in main
    assert "function LegacyTripDetailPage" in pages
    assert '<script type="text/babel" src="/detail-redesign.jsx?v=20260919-horizon"></script>' in html
    assert "function TripDetailPage" in redesign
    assert 'className="rd-top-nav"' not in redesign
    assert 'className="rd-chat-panel"' in redesign
    assert 'className="rd-detail-panel"' in redesign
    assert 'className="rd-divider" role="separator"' in redesign
    assert 'onDoubleClick={() => setDetailWidth(54)}' in redesign
    assert 'className="rd-day-tabs sl-day-tabs"' in redesign
    assert "RedesignMapFallback" not in redesign
    assert '<MapPanel day={day} dayIdx={dayIdx} workbenchControls' in redesign
    assert '<ChatPage embedded {...props}' in redesign
    assert 'ChatState.itineraryMessage(content, target, relatedPlanId)' in pages
    assert 'listConversations(relatedPlanId)' in pages
    assert 'streamRuntimeRun(run.id, cursor' in pages
    assert 'resumeRuntimeRun(run.id, interactionId, content)' in pages
    assert 'retryRuntimeRun(run.id)' in pages
    assert 'cancelRuntimeRun(run.id)' in pages
    assert '<ProfilePage currentUsername={currentUsername}' in redesign
    assert '已了解 78%' not in redesign
    assert '分享链接已复制' not in redesign
    assert 'navigator.clipboard.writeText' in redesign
    assert 'workbenchControls' in components
    assert 'function controlAmap(container, action)' in api
    assert '.rd-app {' in css
    assert '.rd-workspace{' in css
    assert '.rd-detail-body{' in css
    assert '.rd-map-column>.map-frame' in css
    assert '@media(prefers-reduced-motion:reduce)' in css
    assert "optimizeDay(" in redesign
    assert "saveTimeline(" in redesign
    assert "savePlanMetadata(" in redesign


def test_horizon_identity_and_single_theme_are_delivered():
    client = TestClient(app)
    html = client.get("/").text
    main = client.get("/main.jsx").text
    pages = client.get("/pages.jsx").text
    svg = client.get("/assets/brand/horizon.svg")
    assert svg.status_code == 200
    assert "image/svg+xml" in svg.headers["content-type"]
    assert '<svg' in svg.text
    assert 'data-theme="horizon"' in html
    assert 'theme-switcher' not in main
    assert '/mascot.jsx' not in html
    assert '途途' not in pages
    assert '让下一段旅程，' in pages
    assert '1分钟创建' not in pages
    assert 'fonts.googleapis.com' not in html
    assert 'family=Nunito' not in html


def test_primary_user_controls_use_line_icons_instead_of_emoji():
    client = TestClient(app)
    main = client.get("/main.jsx?v=20260919-horizon").text
    pages = client.get("/pages.jsx?v=20260919-horizon").text
    components = client.get("/components.jsx?v=20260919-horizon").text
    edit = client.get("/edit.jsx?v=20260919-horizon").text

    assert '<UiIcon name="menu"' in pages
    assert '<UiIcon name="compress"' in pages
    assert '<UiIcon name="archive"' in pages
    assert '<UiIcon name="undo"' in edit
    assert '<UiIcon name="redo"' in edit
    assert '<UiIcon name="location"' in components
    for emoji in ("💡", "🍽", "⭐", "📍", "💰", "📞", "✕", "✅", "❌", "🧪", "🍜", "☰"):
        assert emoji not in main + pages + components + edit
