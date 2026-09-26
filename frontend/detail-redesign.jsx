// detail-redesign.jsx — 行程详情 redesign（真实行程 / 高德地图 / AI 会话）

function RedesignTimeline({ items, selectedIndex, onSelect, onNav, activeNavKey, tipStatus }) {
  return <div className="rd-timeline">
    <i className="rd-timeline-line" aria-hidden="true" />
    {(items || []).map((item, index) => {
      const next = items[index + 1];
      const navKey = next ? `redesign:${index}` : null;
      const isMeal = item.type !== "attraction";
      const note = item.note || item.reason || item.address || item.addr
        || (["queued", "running"].includes(tipStatus)
          ? "途见正在整理这处地点的旅行贴士…"
          : "已同步到当前路线，点开地图标记可查看详情。");
      const duration = itemDuration(item);
      const warning = `${note}。${item.open || ""}`.split(/(?<=[。！？；])/).filter(sentence => /闭馆|关闭|暂停|不可|禁止|危险|冲突|未预约|停止入场/.test(sentence)).join("");
      const summary = String(note).split(/[。！？\n]/)[0];
      return <React.Fragment key={`${item.name || item.type}-${index}`}>
        <article className={`rd-stop${selectedIndex === index ? " is-selected" : ""}`} tabIndex="0" role="button" aria-label={`查看${item.name || "地点"}`} onKeyDown={event => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); onSelect(index); } }} onClick={() => onSelect(index)}>
          <time><strong>{item.start || (item.type === "lunch" ? "午餐" : item.type === "dinner" ? "晚餐" : "待定")}</strong><span>{index === 0 ? "出发" : "之后"}</span></time>
          <span className={`rd-stop-node${isMeal ? " is-meal" : ""}`}><UiIcon name={isMeal ? "utensils" : "location"} size={14} /></span>
          <div className="rd-stop-card">
            <span className={`rd-stop-icon ${isMeal ? "ochre" : index % 3 === 0 ? "terracotta" : index % 3 === 1 ? "sage" : "ink"}`}><UiIcon name={isMeal ? "utensils" : index % 2 ? "map" : "location"} size={17} /></span>
            <div className="rd-stop-copy">
              <div className="rd-stop-meta"><span>{isMeal ? "本地风味" : "人文漫游"}</span><small>建议停留 {duration}</small></div>
              <h4>{item.name || "待安排地点"}</h4><p className="sl-stop-summary">{summary}</p>{warning && <p className="sl-stop-warning"><UiIcon name="alert" size={12} />{warning}</p>}

            </div><UiIcon className="rd-stop-chevron" name="arrow-right" size={16} />
          </div>
        </article>
        {next && <div className="rd-transfer"><button className={activeNavKey === navKey ? "active" : ""} disabled={!item.location || !next.location} onClick={() => {
          if (!item.location || !next.location) return;
          onNav(activeNavKey === navKey ? null : navKey, { from: item.location, to: next.location });
        }}><UiIcon name="navigation" size={13} />前往下一站 <span>{next.dist ? `${next.dist} km` : "查看路线"}</span></button></div>}
      </React.Fragment>;
    })}
    <div className="rd-route-end"><span><UiIcon name="check" size={14} /></span><div><strong>今天的最后一站</strong><small>把夜晚留给你自己</small></div></div>
  </div>;
}

function RedesignCopilot(props) {
  return <section className="rd-chat-panel"><ChatPage embedded {...props} /></section>;
}

function StudioPlacePhoto({ name, photo }) {
  const [status, setStatus] = React.useState(photo ? "loading" : "empty");
  const hasImage = !!photo && status !== "error";
  return <figure className={`sl-place-photo${hasImage ? " has-image" : " is-empty"}`}>
    {hasImage && <img src={photo} alt={`${name || "景点"}实景照片`} width="640" height="400" decoding="async" onLoad={() => setStatus("ready")} onError={() => setStatus("error")} />}
    {status !== "ready" && <figcaption role={status === "loading" ? "status" : undefined}>
      <UiIcon name="map" size={22} />
      <span>{status === "loading" ? "照片加载中…" : status === "error" ? "照片暂时无法加载" : "暂无景点照片"}</span>
    </figcaption>}
  </figure>;
}

function ProfileModal({ currentUsername, onClose, onLogout }) {
  const dialogRef = React.useRef(null);
  useDialogFocus(dialogRef, onClose);
  return <div className="rd-profile-backdrop" onMouseDown={event => { if (event.target === event.currentTarget) onClose(); }}><section ref={dialogRef} tabIndex="-1" className="studio-profile-modal" role="dialog" aria-modal="true" aria-label="我的旅行画像"><button className="studio-profile-close" onClick={onClose} aria-label="关闭用户画像"><UiIcon name="close" size={20} /></button><ProfilePage currentUsername={currentUsername} />{onLogout && <footer className="profile-account-actions"><button onClick={onLogout}>退出登录</button></footer>}</section></div>;
}

const StudioPlanEditor = React.forwardRef(function StudioPlanEditor({ plan, planId, dayIdx, currentUsername, onSaved, onClose, toolbarHost, onSearch, onPreview }, ref) {
  const [draft, setDraft] = React.useState(() => structuredClone(plan._raw.days));
  const [past, setPast] = React.useState([]);
  const [future, setFuture] = React.useState([]);
  const [saving, setSaving] = React.useState(false);
  const [error, setError] = React.useState("");
  const [version, setVersion] = React.useState(0);
  const originalDraftRef = React.useRef(JSON.stringify(plan._raw.days));
  const dirty = JSON.stringify(draft) !== originalDraftRef.current;
  const canLeave = () => !saving && (!dirty || window.confirm("放弃未保存的行程修改？"));
  const apply = (mutate, targetDay = dayIdx) => {
    const next = structuredClone(draft); mutate(next[targetDay].timeline);
    next.forEach(day => recalcDayDists(day.timeline));
    setPast([...past, draft]); setFuture([]); setDraft(next); setVersion(version + 1);
  };
  React.useImperativeHandle(ref, () => ({ canLeave, pick(poi, target) {
    if (saving) return;
    apply(items => {
      const old = target.index === null ? {} : items[target.index];
      const next = { ...old, ...poi, type: target.type, tip: null, reason: null, no_restaurant: false };
      if (target.index === null) items.push(next); else items[target.index] = next;
    }, target.dayIdx);
  } }));
  React.useEffect(() => { onPreview(adaptPlan({ ...plan._raw, days: draft }, currentUsername)); }, [draft]);
  React.useEffect(() => {
    const guard = event => { if (dirty) { event.preventDefault(); event.returnValue = ""; } };
    window.addEventListener("beforeunload", guard);
    return () => window.removeEventListener("beforeunload", guard);
  }, [dirty]);
  const save = async () => {
    if (saving) return;
    setSaving(true); setError("");
    try { const result = await saveTimeline(planId, draft.map((day, index) => ({ day: day.day || index + 1, timeline: day.timeline }))); onSaved(result.plan); onClose(); }
    catch (err) { setError(err.message || "保存失败，请重试"); }
    finally { setSaving(false); }
  };
  return <div className="studio-editor">
    {toolbarHost && ReactDOM.createPortal(<div className="sl-edit-actions" aria-label="行程编辑工具栏">
      <button disabled={saving} onClick={() => { if (canLeave()) onClose(); }}>取消</button>
      <button aria-label="撤销修改" disabled={!past.length || saving} onClick={() => { setFuture([...future, draft]); setDraft(past[past.length - 1]); setPast(past.slice(0, -1)); setVersion(version + 1); }}><UiIcon name="undo" size={15} /><span>撤销</span></button>
      <button aria-label="重做修改" disabled={!future.length || saving} onClick={() => { setPast([...past, draft]); setDraft(future[future.length - 1]); setFuture(future.slice(0, -1)); setVersion(version + 1); }}><UiIcon name="redo" size={15} /><span>重做</span></button>
      <button className="primary" disabled={saving} onClick={save} aria-label="完成编辑">{saving ? "保存中…" : error ? "重试保存" : "保存"}</button>
    </div>, toolbarHost)}
    <p className="sl-edit-hint">拖动调整顺序 · 点击时间修改{dirty ? " · 有未保存修改" : ""}</p>
    {error && <p className="sl-error" role="alert">{error}</p>}
    <fieldset disabled={saving} style={{ border: 0, padding: 0, minWidth: 0 }}><EditableTimeline rawTimeline={draft[dayIdx].timeline} ver={`${dayIdx}-${version}`}
      onReorder={(from, to) => apply(items => { const moved = reorderKeepTimes(items, from, to); items.splice(0, items.length, ...moved); })}
      onDelete={index => apply(items => items.splice(index, 1))}
      onTimeChange={(index, start, end) => apply(items => Object.assign(items[index], { start_time: start, end_time: end }))}
      onReplace={index => onSearch({ dayIdx, index, type: draft[dayIdx].timeline[index].type })}
      onAdd={type => onSearch({ dayIdx, index: null, type })} /></fieldset>
  </div>;
});

function StudioPoiPicker({ city, target, candidates, onPick }) {
  const [query, setQuery] = React.useState("");
  const [results, setResults] = React.useState(null);
  const [busy, setBusy] = React.useState(false);
  const [error, setError] = React.useState("");
  const search = async event => {
    event.preventDefault(); if (!query.trim() || busy) return;
    setBusy(true); setError("");
    try { setResults(await searchPoi(city, query.trim(), target.type === "attraction" ? "attraction" : "restaurant")); }
    catch (err) { setError(err.message || "搜索失败，请重试"); }
    finally { setBusy(false); }
  };
  const spots = results || (target.type === "attraction" ? candidates : []);
  return <div className="sl-poi-picker"><form onSubmit={search}><input aria-label="搜索地点" placeholder="景点名称或餐厅菜系" value={query} onChange={event => setQuery(event.target.value)} /><button disabled={busy || !query.trim()}>{busy ? "搜索中…" : "搜索"}</button></form>
    <p>选择后加入 Day {target.dayIdx + 1} 的编辑草稿，保存后生效。</p>
    {error && <p className="sl-error" role="alert">{error}</p>}
    {!spots.length && <p>{results ? "没有找到地点，换个关键词试试。" : "输入关键词，搜索真实地点。"}</p>}
    {spots.map((poi, index) => <button className="sl-poi-result" key={index} onClick={() => onPick(poi)}><strong>{poi.name}</strong><span>{poi.address || ""}{poi.rating ? ` · ${poi.rating} 分` : ""}</span><em>{target.index === null ? "添加到行程" : "替换此地点"}</em></button>)}
  </div>;
}

function StudioNearby({ item }) {
  const [kind, setKind] = React.useState("风景名胜");
  const [radius, setRadius] = React.useState(1500);
  const [results, setResults] = React.useState([]);
  const [error, setError] = React.useState("");
  const [busy, setBusy] = React.useState(false);
  const [retry, setRetry] = React.useState(0);
  React.useEffect(() => {
    let alive = true; setBusy(true); setError("");
    searchNearby(item.location.lat, item.location.lng, kind, radius).then(data => { if (alive) setResults(data); }).catch(err => { if (alive) setError(err.message || "搜索失败"); }).finally(() => { if (alive) setBusy(false); });
    return () => { alive = false; };
  }, [item.location.lat, item.location.lng, kind, radius, retry]);
  return <div className="sl-nearby"><label>类型<select value={kind} onChange={e => setKind(e.target.value)}><option>风景名胜</option><option>餐饮服务</option></select></label><label>范围<select value={radius} onChange={e => setRadius(Number(e.target.value))}>{[500,1000,1500,3000].map(r => <option value={r} key={r}>{r} 米</option>)}</select></label>
    {busy ? <p role="status">正在查找周边…</p> : error ? <p role="alert">{error}<button onClick={() => setRetry(value => value + 1)}>重试</button></p> : !results.length ? <p>附近暂无结果，试试扩大范围。</p> : results.map((poi, index) => <article key={index}><strong>{poi.name}</strong><p>{poi.address}{poi.distance != null ? ` · ${poi.distance} 米` : ""}</p></article>)}
  </div>;
}

function StudioPlanNotes({ plan, planId, onNotice }) {
  const [hotel, setHotel] = React.useState(plan.hotel || "");
  const [notes, setNotes] = React.useState(plan.notes || "");
  const [saving, setSaving] = React.useState(false);
  return <section className="studio-notes"><h4>出行提醒</h4>{plan.tips?.length ? <ul>{plan.tips.map((tip, index) => <li key={index}>{tip}</li>)}</ul> : <p>暂无额外出行提醒</p>}
    <label>住宿<input value={hotel} onChange={event => setHotel(event.target.value)} placeholder="酒店名称、地址或入住安排" /></label>
    <label>旅行备注<textarea value={notes} onChange={event => setNotes(event.target.value)} placeholder="记录这趟旅行的小事" /></label>
    <button disabled={saving} onClick={async () => { setSaving(true); try { await savePlanMetadata(planId, { hotel, notes }); onNotice("住宿和备注已保存"); } catch (err) { onNotice(err.message || "保存失败"); } finally { setSaving(false); } }}>{saving ? "保存中…" : "保存住宿与备注"}</button>
  </section>;
}

function TripDetailPage({ plan: planProp, planId: planIdProp, onRequestModify, currentUsername, onBack, onPlanChange, initialDraft, onInitialDraftConsumed, revisionTrigger, onRevisionConsumed, onRequestLogin, onClearPlan, navigationGuardRef, profileOpen = false }) {
  const emptyPlan = { days: [], weather: [], badges: [], destination: "下一段旅程", username: currentUsername };
  const [plan, setPlan] = React.useState(planProp || emptyPlan);
  const [planId, setPlanId] = React.useState(planIdProp);
  const [dayIdx, setDayIdx] = React.useState(0);
  const [selectedIndex, setSelectedIndex] = React.useState(0);
  const [activeNavKey, setActiveNavKey] = React.useState(null);
  const [activeNavPair, setActiveNavPair] = React.useState(null);
  const [mobilePane, setMobilePane] = React.useState(planIdProp ? "itinerary" : "chat");
  const [mobileDetail, setMobileDetail] = React.useState("route");
  const [detailWidth, setDetailWidth] = React.useState(54);
  const [resizing, setResizing] = React.useState(false);
  const [chatOnly, setChatOnly] = React.useState(false);
  const [detailOnly, setDetailOnly] = React.useState(false);
  const [drawer, setDrawer] = React.useState(null);
  const [overviewOpen, setOverviewOpen] = React.useState(false);
  const [menu, setMenu] = React.useState(null);
  const [toolbarHost, setToolbarHost] = React.useState(null);
  const [draftPlan, setDraftPlan] = React.useState(null);
  const [tipBusy, setTipBusy] = React.useState(false);
  const [tipError, setTipError] = React.useState("");
  const [actionError, setActionError] = React.useState("");
  const [retryRevert, setRetryRevert] = React.useState(false);
  const editorRef = React.useRef(null);
  const drawerRef = React.useRef(null);
  const drawerTriggerRef = React.useRef(null);
  const [notice, setNotice] = React.useState("");
  const [editing, setEditing] = React.useState(false);
  const [optimizing, setOptimizing] = React.useState(false);
  const [originalDays, setOriginalDays] = React.useState({});

  React.useEffect(() => {
    setPlan(planProp || emptyPlan); setPlanId(planIdProp);
    if (planIdProp !== planId) { setDayIdx(0); setSelectedIndex(0); setEditing(false); setDraftPlan(null); setDrawer(null); setOriginalDays({}); }
  }, [planProp, planIdProp]);

  React.useEffect(() => {
    if (!resizing) return undefined;
    const move = event => setDetailWidth(Math.min(70, Math.max(40, ((window.innerWidth - event.clientX) / window.innerWidth) * 100)));
    const stop = () => setResizing(false);
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", stop, { once: true });
    return () => { window.removeEventListener("pointermove", move); window.removeEventListener("pointerup", stop); };
  }, [resizing]);

  React.useEffect(() => {
    if (!planId) return undefined;
    let abort = null; let alive = true;
    streamItineraryTips(planId, {
      onAbort: fn => { abort = fn; },
      onEvent: event => {
        if (event.payload?.kind !== "itinerary.tip_status_changed") return;
        getHistoryItem(planId).then(data => {
          if (alive && data?.plan) setPlan(adaptPlan(data.plan, currentUsername));
        }).catch(() => {});
      },
    });
    return () => { alive = false; abort?.(); };
  }, [planId, currentUsername]);

  const hasPlan = !!plan?.days?.length;
  const viewPlan = editing && draftPlan ? draftPlan : plan;
  const day = viewPlan.days[dayIdx] || viewPlan.days[0] || { items: [], mapPoints: [] };
  const selected = day.items?.[selectedIndex] || day.items?.[0] || null;
  const weather = plan.weather?.[dayIdx] || plan.weather?.[0];
  const candidates = plan.candidate_spots || [];
  const coverBadges = (plan.badges || []).slice(0, 3);
  const displayDateRange = (() => {
    const start = plan._raw?.start_date;
    const end = plan._raw?.end_date;
    if (!start || !end) return plan.date_range || "日期待定";
    const [, sm, sd] = start.split("-");
    const [, em, ed] = end.split("-");
    return `${Number(sm)} 月 ${Number(sd)} 日 — ${Number(em)} 月 ${Number(ed)} 日`;
  })();
  const showNotice = text => { setNotice(text); window.setTimeout(() => setNotice(""), 2200); };
  const openConversationResult = async resultPlanId => {
    if (!resultPlanId || (editing && !editorRef.current?.canLeave())) return;
    try {
      const data = await getHistoryItem(resultPlanId);
      if (!data?.plan) return;
      const adapted = adaptPlan(data.plan, currentUsername);
      setEditing(false); setDraftPlan(null); setDrawer(null);
      setPlan(adapted); setPlanId(resultPlanId); setMobilePane("itinerary");
      if (resultPlanId !== planId) { setDayIdx(0); setSelectedIndex(0); }
      onPlanChange?.(data.plan, resultPlanId);
    } catch (error) { showNotice(error.message || "行程加载失败，请重试"); }
  };
  const changeDay = index => {
    setDayIdx(index); setSelectedIndex(0); setActiveNavKey(null); setActiveNavPair(null); setMenu(null);
    if (drawer?.type === "search") setDrawer(null);
  };

  const closeDrawer = React.useCallback(() => { setDrawer(null); drawerTriggerRef.current?.focus(); }, []);
  const openDrawer = value => { drawerTriggerRef.current = document.activeElement; setDrawer(value); setMenu(null); };
  const finishEdit = () => { setEditing(false); setDraftPlan(null); setDrawer(null); };
  const beginEdit = () => { setMobileDetail("route"); setEditing(true); setMenu(null); setDrawer(null); };
  const prepareNavigation = () => { if (editing && !editorRef.current?.canLeave()) return false; finishEdit(); return true; };
  React.useEffect(() => {
    if (!navigationGuardRef) return;
    navigationGuardRef.current = prepareNavigation;
    return () => { navigationGuardRef.current = null; };
  });
  const leaveWorkspace = callback => { if (prepareNavigation()) callback?.(); };
  React.useEffect(() => {
    if (drawer) {
      drawerRef.current?.focus({ preventScroll: true });

    }
  }, [drawer?.type]);
  React.useEffect(() => {
    const escape = event => { if (event.key === "Escape" && !profileOpen) { if (drawer) closeDrawer(); setMenu(null); } };
    const outside = event => { if (!event.target.closest(".sl-menu-wrap")) setMenu(null); };
    document.addEventListener("keydown", escape); document.addEventListener("pointerdown", outside);
    return () => { document.removeEventListener("keydown", escape); document.removeEventListener("pointerdown", outside); };
  }, [drawer, profileOpen, closeDrawer]);
  const retryTips = async () => {
    setTipBusy(true); setTipError("");
    try { await retryItineraryTips(planId); setPlan(previous => ({ ...previous, tip_status: "queued" })); showNotice("正在重新整理景点贴士"); }
    catch (error) { setTipError(error.message || "生成失败，请重试"); }
    finally { setTipBusy(false); }
  };
  const optimize = async (revert = false) => {
    setOptimizing(true); setActionError(""); setRetryRevert(revert); setMenu(null);
    const index = dayIdx;
    try {
      if (revert) await revertDay(planId, index + 1, originalDays[index]);
      else {
        const result = await optimizeDay(planId, index + 1);
        if (result.improved) setOriginalDays(previous => ({ ...previous, [index]: structuredClone(plan._raw.days[index].timeline) }));
        showNotice(result.improved ? `路线已优化：${result.original_km} → ${result.optimized_km} km` : "当前路线无需优化");
      }
      const data = await getHistoryItem(planId); setPlan(adaptPlan(data.plan, currentUsername)); onPlanChange?.(data.plan, planId);
      if (revert) { setOriginalDays(previous => { const next = { ...previous }; delete next[index]; return next; }); showNotice("已恢复优化前路线"); }
    } catch (error) { setActionError(error.message || "路线操作失败"); }
    finally { setOptimizing(false); }
  };
  const share = async () => {
    try { await navigator.clipboard.writeText(`${location.origin}/?view_plan_id=${encodeURIComponent(planId)}`); showNotice("行程链接已复制，登录当前账号后可打开"); }
    catch { showNotice("复制失败，请检查浏览器剪贴板权限"); }
  };
  const drawerTitle = drawer?.type === "notes" ? "旅行资料" : drawer?.type === "search" ? "替换 / 添加地点" : drawer?.type === "nearby" ? `${selected?.name || "地点"}周边` : selected?.name || "地点详情";

  return <div className="page page-fade trip-detail-page rd-page">
    <main className="rd-app" data-mobile-pane={mobilePane} data-mobile-detail={mobileDetail}>
      <nav className="mobile-workspace-tabs" aria-label="工作区视图">{[{id:"chat",label:"对话",icon:"chat"},{id:"itinerary",label:"行程",icon:"map"}].map(item => <button key={item.id} aria-pressed={mobilePane === item.id} onClick={() => { setMobilePane(item.id); setChatOnly(false); setDetailOnly(false); setDrawer(null); }}><UiIcon name={item.icon} size={17} />{item.label}</button>)}</nav>
      <section className={`rd-workspace${chatOnly ? " chat-only" : ""}${detailOnly ? " detail-only" : ""}`} style={{ "--rd-detail-width": `${detailWidth}%` }}>
        <RedesignCopilot onBeforeConversationChange={prepareNavigation} inputDisabled={editing} currentUsername={currentUsername} relatedPlanId={planId} onBack={() => leaveWorkspace(onBack)} onToggleDetail={() => { if (window.matchMedia("(max-width: 900px)").matches) { setMobilePane("itinerary"); } else { setChatOnly(value => !value); setDetailOnly(false); } }} onOpenPlan={openConversationResult} onPlanResult={openConversationResult} onClearPlan={() => leaveWorkspace(onClearPlan)} initialDraft={initialDraft} onInitialDraftConsumed={onInitialDraftConsumed} revisionTrigger={revisionTrigger} onRevisionConsumed={onRevisionConsumed} onRequestLogin={onRequestLogin} />
        <div className="rd-divider" role="separator" tabIndex="0" aria-label="调整对话和行程详情宽度" aria-orientation="vertical" aria-valuemin="40" aria-valuemax="70" aria-valuenow={Math.round(detailWidth)} onPointerDown={event => { event.preventDefault(); setResizing(true); }} onDoubleClick={() => setDetailWidth(54)} onKeyDown={event => {
          if (event.key === "Home") setDetailWidth(54);
          if (event.key === "ArrowLeft") setDetailWidth(value => Math.min(70, value + 2));
          if (event.key === "ArrowRight") setDetailWidth(value => Math.max(40, value - 2));
        }}><span /></div>

        <section className="rd-detail-panel">
          {!hasPlan ? <div className="studio-detail-empty"><BrandMark size={64} decorative /><h2>把想去的地方，<br />慢慢变成一段旅程。</h2><p>在左侧和途见聊聊。确认旅行需求后，<br />完整路线、每日安排与地图会在这里展开。</p></div> : <>
          <div className="rd-detail-header sl-detail-header">
            <div className="sl-heading-row"><div className="sl-heading-copy"><h2>{plan.title}</h2></div>
              <div className="sl-toolbar" ref={setToolbarHost}>{!editing && <><button disabled={optimizing} aria-label="编辑行程" onClick={beginEdit}><UiIcon name="edit" size={15} />编辑</button><button className="primary" onClick={share}><UiIcon name="share" size={15} />分享</button><div className="sl-menu-wrap"><button aria-label="更多行程操作" aria-expanded={menu === "trip"} onClick={() => setMenu(menu === "trip" ? null : "trip")}><UiIcon name="menu" size={17} /></button>{menu === "trip" && <div className="sl-menu"><button onClick={() => openDrawer({ type: "notes" })}>旅行资料</button><button onClick={() => { setDetailOnly(value => !value); setChatOnly(false); setMenu(null); }}>{detailOnly ? "显示 AI 对话" : "收起 AI 对话"}</button></div>}</div></>}</div>
            </div>
            <div className="sl-heading-links"><button aria-expanded={overviewOpen} onClick={() => setOverviewOpen(value => !value)}>行程概览 <UiIcon name={overviewOpen ? "chevron-up" : "chevron-down"} size={12} /></button><button onClick={() => openDrawer({ type: "notes" })}>旅行资料</button></div>
            {overviewOpen && <div className="sl-overview"><div className="rd-cover" style={{ backgroundImage: `linear-gradient(100deg, rgba(21,26,32,.82), rgba(21,26,32,.22))${plan.days.flatMap(item => item.items).find(item => item.photo)?.photo ? `, url("${plan.days.flatMap(item => item.items).find(item => item.photo).photo}")` : ""}` }}><div><small>ITINERARY · 行程总览</small><p>{displayDateRange}</p><p>{coverBadges.map((badge, index) => <span key={index}>{badge}</span>)}</p></div></div></div>}
            <nav className="rd-day-tabs sl-day-tabs" role="tablist" aria-label="行程日期">{plan.days.map((item, index) => {
              const forecast = plan.weather?.[index];
              return <button key={index} role="tab" aria-selected={dayIdx === index} disabled={optimizing} className={dayIdx === index ? "active" : ""} onClick={() => changeDay(index)}><div><strong>Day {index + 1} <span>{item.date}</span></strong><small><WeatherGlyph value={forecast?.icon || forecast?.text} size={14} />{forecast?.text || "天气待定"}{forecast?.hi != null ? ` ${forecast.hi}° / ${forecast.lo ?? "—"}°` : ""}</small></div><i /></button>;
            })}</nav>
          </div>

          <nav className="mobile-detail-tabs" aria-label="行程视图">{[{id:"route",label:"路线"},{id:"map",label:"地图"}].map(item => <button key={item.id} aria-pressed={mobileDetail === item.id} onClick={() => setMobileDetail(item.id)}>{item.label}</button>)}</nav>
          <div className={`rd-detail-body sl-detail-body${drawer ? " has-drawer" : ""}`}>
            <section className="rd-route-column"><header><div><small>第 {dayIdx + 1} 天 · {day.items.length} 个安排</small><h3>{day.theme || `Day ${dayIdx + 1} 的旅行安排`}</h3></div>{!editing && <div className="sl-menu-wrap"><button className="sl-day-more" aria-label="当天路线操作" aria-expanded={menu === "day"} disabled={optimizing} onClick={() => setMenu(menu === "day" ? null : "day")}><UiIcon name="menu" size={16} /></button>{menu === "day" && <div className="sl-menu"><button onClick={() => optimize()}>优化当天路线</button>{originalDays[dayIdx] && <button onClick={() => optimize(true)}>撤回路线优化</button>}<button onClick={() => { beginEdit(); openDrawer({ type: "search", target: { dayIdx, index: null, type: "attraction" } }); }}>添加地点</button></div>}</div>}</header>
              {optimizing && <p role="status" className="sl-edit-hint">正在更新当天路线…</p>}
              {actionError && <p role="alert" className="sl-error">{actionError}<button onClick={() => optimize(retryRevert)}>重试</button></p>}
              {editing ? <StudioPlanEditor key={planId} ref={editorRef} plan={plan} planId={planId} dayIdx={dayIdx} currentUsername={currentUsername} toolbarHost={toolbarHost} onSearch={target => openDrawer({ type: "search", target })} onPreview={setDraftPlan} onClose={finishEdit} onSaved={raw => { setPlan(adaptPlan(raw, currentUsername)); onPlanChange?.(raw, planId); }} /> : <RedesignTimeline items={day.items} selectedIndex={selectedIndex} onSelect={index => { setSelectedIndex(index); openDrawer({ type: "place" }); }} activeNavKey={activeNavKey} tipStatus={plan.tip_status} onNav={(key, pair) => { setActiveNavKey(key); setActiveNavPair(key ? pair : null); }} />}
            </section>

            <section className="rd-map-column" aria-label={`Day ${dayIdx + 1} 路线地图`}>
              <MapPanel day={day} dayIdx={dayIdx} workbenchControls selectedLocation={selected?.location} navPair={activeNavPair} onNavClear={() => { setActiveNavKey(null); setActiveNavPair(null); }} />
              {selected && !drawer && <button className="sl-map-summary" onClick={() => openDrawer({ type: "place" })}><UiIcon name="location" size={15} /><strong>{selected.name}</strong><span>{itemDuration(selected)}</span><UiIcon name="chevron-up" size={15} /></button>}
              <aside hidden={!drawer} ref={drawerRef} tabIndex="-1" className="sl-drawer" role="region" aria-label={drawerTitle}>
                <header><div><h3>{drawerTitle}</h3></div><button aria-label="关闭详情抽屉" onClick={closeDrawer}><UiIcon name="close" size={18} /></button></header>
                <div className="sl-drawer-body">
                  <div hidden={drawer?.type !== "notes"}><StudioPlanNotes key={planId} plan={plan} planId={planId} onNotice={showNotice} /></div>
                  {drawer?.type === "place" && selected && <div className="sl-place-details">
                    <StudioPlacePhoto key={`${selected.name}:${selected.photo || ""}`} name={selected.name} photo={selected.photo} />
                    <p className="sl-place-duration">{itemDuration(selected)}{selected.rating != null ? ` · ${selected.rating} 分` : ""}</p>
                    <dl><dt>开放时间</dt><dd>{selected.open || "暂无开放时间，请以景区公告为准"}</dd><dt>地址</dt><dd>{selected.address || selected.addr || "暂无地址"}</dd>{selected.cost && <><dt>参考费用</dt><dd>{formatPoiCost(selected.cost)}</dd></>}{selected.tel && <><dt>联系电话</dt><dd>{selected.tel}</dd></>}</dl>
                    <section className="sl-tips"><h4>游玩贴士</h4><p>{selected.note || selected.reason || "暂无详细贴士"}</p>{["queued", "running"].includes(plan.tip_status) && <p role="status">正在整理最新贴士…</p>}{(tipError || ["failed", "unavailable"].includes(plan.tip_status)) && <p className="sl-error" role="alert">{tipError || "贴士尚未更新，可重新生成"}</p>}<button disabled={editing || tipBusy || ["queued", "running"].includes(plan.tip_status)} onClick={retryTips}>{tipBusy ? "提交中…" : tipError || plan.tip_status === "failed" ? "重试生成贴士" : "重新生成景点贴士"}</button></section>
                    <div className="sl-place-actions">{selected.location && <button onClick={() => openDrawer({ type: "nearby" })}>查看周边</button>}<button disabled={editing || optimizing} onClick={() => { beginEdit(); openDrawer({ type: "search", target: { dayIdx, index: selectedIndex, type: selected.type } }); }}>替换地点</button></div>
                  </div>}
                  {drawer?.type === "nearby" && selected?.location && <><button onClick={() => openDrawer({ type: "place" })}>返回地点详情</button><StudioNearby item={selected} /></>}
                  {drawer?.type === "search" && <StudioPoiPicker key={`${drawer.target.dayIdx}:${drawer.target.index}:${drawer.target.type}`} city={plan.destination} target={drawer.target} candidates={candidates} onPick={poi => { editorRef.current?.pick(poi, drawer.target); closeDrawer(); }} />}
                </div>
              </aside>
            </section>
          </div>
          </>}
        </section>
        {chatOnly && <button className="rd-reopen detail" onClick={() => setChatOnly(false)}>显示行程详情</button>}{detailOnly && <button className="rd-reopen chat" onClick={() => setDetailOnly(false)}>显示 AI 对话</button>}
      </section>

      {notice && <div className="rd-toast" role="status"><UiIcon name="check" size={14} />{notice}</div>}
    </main>
  </div>;
}

Object.assign(window, { TripDetailPage });
