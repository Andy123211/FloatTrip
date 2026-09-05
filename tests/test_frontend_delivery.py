from fastapi.testclient import TestClient

from app.main import app


def test_frontend_html_disables_cache_and_versions_all_local_assets():
    response = TestClient(app).get("/")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store, max-age=0"
    html = response.text
    assert '<link rel="icon" type="image/png" href="/favicon.png?v=20260830-duck-guide" />' in html
    assert '<link rel="apple-touch-icon" href="/favicon.png?v=20260830-duck-guide" />' in html
    for asset in (
        "style.css", "api.js", "chat-state.js", "navigation-state.js",
        "tweaks-panel.jsx", "mascot.jsx", "components.jsx", "edit.jsx",
        "pages.jsx", "main.jsx",
    ):
        assert f'/{asset}?v=20260902-blurry-blob' in html


def test_frontend_serves_the_duck_guide_icon():
    response = TestClient(app).get("/favicon.png?v=20260830-duck-guide")

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
    response = TestClient(app).get("/pages.jsx?v=20260830-duck-brand")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-cache, must-revalidate"
    assert "function PlanningBriefCard" in response.text
    assert "RuntimeRunCard" not in response.text
    assert "PlanningProgressTimeline" in response.text
    assert "规划进行中" in response.text


def test_protected_api_401_expires_the_stale_browser_session():
    api = TestClient(app).get("/api.js?v=20260830-duck-brand").text

    assert "if (r.status === 401) clearAuth();" in api
    assert 'error.code = r.status === 401 ? "auth_expired"' in api


def test_conversation_ui_contains_persisted_accessible_itinerary_cards():
    response = TestClient(app).get("/pages.jsx?v=20260807-conversation-attention")

    assert response.status_code == 200
    assert "function MessageArtifacts" in response.text
    assert "saved-itinerary-card" in response.text
    assert "onOpen?.(card.itinerary_id)" in response.text
    assert "查看完整方案" in response.text


def test_chat_page_integrates_paginated_history_plans_and_legacy_route_redirect():
    client = TestClient(app)
    pages = client.get("/pages.jsx?v=20260830-duck-brand").text
    main = client.get("/main.jsx?v=20260830-duck-brand").text
    api = client.get("/api.js?v=20260830-duck-brand").text

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
    assert "related_itinerary_id: target.itineraryId" in pages
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
    assert "途途正在整理贴士…" in components
    assert "路线已更新，贴士尚未更新" in components
    assert "async function streamItineraryTips" in api
    assert "retryItineraryTips" in api


def test_itinerary_detail_uses_the_redesign_workspace_with_live_core_features():
    client = TestClient(app)
    pages = client.get("/pages.jsx?v=20260830-duck-brand").text
    redesign = client.get("/detail-redesign.jsx?v=20260902-redesign").text
    main = client.get("/main.jsx?v=20260830-duck-brand").text
    components = client.get("/components.jsx?v=20260830-duck-brand").text
    api = client.get("/api.js?v=20260830-duck-brand").text
    css = client.get("/style.css?v=20260830-duck-brand").text
    html = client.get("/").text

    assert '{page !== "detail" && <header className="topbar">' in main
    assert "function LegacyTripDetailPage" in pages
    assert '<script type="text/babel" src="/detail-redesign.jsx?v=20260902-redesign"></script>' in html
    assert "function TripDetailPage" in redesign
    assert 'className="rd-top-nav"' in redesign
    assert 'className="rd-chat-panel"' in redesign
    assert 'className="rd-detail-panel"' in redesign
    assert 'className="rd-divider" role="separator"' in redesign
    assert 'onDoubleClick={() => setDetailWidth(54)}' in redesign
    assert 'className="rd-day-tabs"' in redesign
    assert "function RedesignMapFallback" in redesign
    assert 'className="rd-map-demo-controls"' in redesign
    assert 'className="rd-map-zoom"' in redesign
    assert '<MapPanel day={day} dayIdx={dayIdx}' in redesign
    assert 'submitConversationMessage(conversationId, text, { related_itinerary_id: planId })' in redesign
    assert 'listConversations(planId)' in redesign
    assert 'streamRuntimeRun(run.id, 0' in redesign
    assert 'resumeRuntimeRun(blockingRun.id, interactionId, text)' in redesign
    assert 'retryRuntimeRun(run.id)' in redesign
    assert 'cancelRuntimeRun(run.id)' in redesign
    assert 'workbenchControls' in components
    assert 'function controlAmap(container, action)' in api
    assert '.rd-app {' in css
    assert '.rd-workspace{' in css
    assert '.rd-detail-body{' in css
    assert '.rd-map-column>.map-frame' in css
    assert '.rd-map-fallback{' in css
    assert '.rd-map-zoom{' in css
    assert '@media(prefers-reduced-motion:reduce)' in css
    assert "optimizeDay(" not in redesign
    assert "saveTimeline(" not in redesign
    assert "savePlanMetadata(" not in redesign


def test_duck_brand_tokens_themes_fonts_and_internal_icons_are_locked():
    client = TestClient(app)
    css = client.get("/style.css?v=20260830-duck-brand").text
    html = client.get("/").text
    main = client.get("/main.jsx?v=20260830-duck-brand").text
    components = client.get("/components.jsx?v=20260830-duck-brand").text
    mascot = client.get("/mascot.jsx?v=20260830-duck-brand").text

    for color in ("#FFE39A", "#FFD166", "#F6F0E6", "#E7F0C5", "#8FB26E", "#6B4E3D"):
        assert color in css
    for theme_id in ("morning", "celadon", "night", "sky"):
        assert f'[data-theme="{theme_id}"]' in css
        assert f'value: "{theme_id}"' in main
    for label in ("晨光 · 蜂蜜黄", "青野 · 嫩芽绿", "夜旅 · 可可棕", "晴日 · 奶油白"):
        assert label in main

    assert "family=Nunito" in html
    assert '"Yuanti SC", "Noto Sans SC", "PingFang SC"' in css
    assert "function UiIcon({ name, size = 18, strokeWidth = 2, label = null" in components
    assert "function WeatherGlyph" in components
    assert '<img src="/favicon.png?v=20260830-duck-guide" alt="" />' in main

    assert 'function Mascot({ size = 140, pose = "idle", flip = false, style = {} })' in mascot
    for pose in ("idle", "wave", "walk", "point", "cheer", "think"):
        assert pose in mascot

    for retired_color in ("#b5491f", "#3f5d3a", "#fdfaf2", "#9c352f", "#34c759"):
        assert retired_color not in css.lower()


def test_primary_user_controls_use_line_icons_instead_of_emoji():
    client = TestClient(app)
    main = client.get("/main.jsx?v=20260830-duck-brand").text
    pages = client.get("/pages.jsx?v=20260830-duck-brand").text
    components = client.get("/components.jsx?v=20260830-duck-brand").text
    edit = client.get("/edit.jsx?v=20260830-duck-brand").text

    assert '<UiIcon name="menu"' in pages
    assert '<UiIcon name="compress"' in pages
    assert '<UiIcon name="archive"' in pages
    assert '<UiIcon name="undo"' in edit
    assert '<UiIcon name="redo"' in edit
    assert '<UiIcon name="location"' in components
    assert '<UiIcon name={THEME_ICONS[value]}' in main
    for emoji in ("💡", "🍽", "⭐", "📍", "💰", "📞", "✕", "✅", "❌", "🧪", "🍜", "☰"):
        assert emoji not in main + pages + components + edit
