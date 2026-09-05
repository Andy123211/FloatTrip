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
          ? "途途正在整理这处地点的旅行贴士…"
          : "已同步到当前路线，点开地图标记可查看详情。");
      const duration = itemDuration(item);
      return <React.Fragment key={`${item.name || item.type}-${index}`}>
        <article className={`rd-stop${selectedIndex === index ? " is-selected" : ""}`} onClick={() => onSelect(index)}>
          <time><strong>{item.start || (item.type === "lunch" ? "午餐" : item.type === "dinner" ? "晚餐" : "待定")}</strong><span>{index === 0 ? "出发" : "之后"}</span></time>
          <span className={`rd-stop-node${isMeal ? " is-meal" : ""}`}><UiIcon name={isMeal ? "utensils" : "location"} size={14} /></span>
          <div className="rd-stop-card">
            <span className={`rd-stop-icon ${isMeal ? "ochre" : index % 3 === 0 ? "terracotta" : index % 3 === 1 ? "sage" : "ink"}`}><UiIcon name={isMeal ? "utensils" : index % 2 ? "map" : "location"} size={17} /></span>
            <div className="rd-stop-copy">
              <div className="rd-stop-meta"><span>{isMeal ? "本地风味" : "人文漫游"}</span><small>建议停留 {duration}</small></div>
              <h4>{item.name || "待安排地点"}</h4><p>{note}</p>
              <em><UiIcon name="location" size={12} />{item.address || item.addr || (item.rating ? `评分 ${Number(item.rating).toFixed(1)}` : "已同步地图位置")}</em>
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

function RedesignMapFallback({ items, selectedIndex, onSelect }) {
  const points = [
    { left: 54, top: 52 },
    { left: 45, top: 60 },
    { left: 39, top: 68 },
    { left: 49, top: 43 },
    { left: 64, top: 31 },
  ];
  return <div className="rd-map-fallback" aria-hidden="true">
    <svg viewBox="0 0 560 680" preserveAspectRatio="none">
      <path className="water" d="M470 -20 C405 65 478 131 430 210 C388 278 445 353 405 426 C366 496 405 586 348 710 L590 710 L590 -20Z" />
      <path className="park" d="M402 84 C448 40 533 64 548 129 C559 180 529 240 474 230 C417 218 364 124 402 84Z" />
      <path className="park park-two" d="M58 50 C106 18 185 42 192 105 C198 158 151 198 95 180 C39 162 13 83 58 50Z" />
      <path className="road wide" d="M-35 604 C86 542 159 477 231 397 C301 319 354 243 397 149 C421 97 447 40 485 -20" />
      <path className="road" d="M-20 266 C96 273 181 300 263 340 C347 381 443 373 585 328" />
      <path className="road" d="M18 94 C115 163 196 189 277 200 C374 213 433 274 582 421" />
      <path className="road" d="M-30 474 C95 421 183 418 276 449 C372 481 462 542 592 533" />
      <path className="road thin" d="M134 -20 C164 116 116 223 148 355 C173 456 157 573 185 710" />
      <path className="route" d="M30 530 C117 477 170 430 222 384 C277 335 323 281 373 195" />
    </svg>
    <span className="rd-district rd-xuanwu">玄<br />武<br />湖</span>
    <span className="rd-district rd-xinjiekou">新街口</span>
    <span className="rd-district rd-qinhuai">秦淮河</span>
    {(items || []).slice(0, 5).map((item, index) => {
      const point = points[index] || points[points.length - 1];
      return <button key={`${item.name}-${index}`} className={selectedIndex === index ? "active" : ""} style={{ left: `${point.left}%`, top: `${point.top}%` }} onClick={() => onSelect(index)} tabIndex="-1"><i>{index + 1}</i>{index === 0 && <b>{item.name}</b>}</button>;
    })}
  </div>;
}

function RedesignCopilot({ plan, planId, day, dayIdx, displayDateRange, currentUsername, onBack, onCollapse, onPlanResult }) {
  const [query, setQuery] = React.useState("");
  const [activeId, setActiveId] = React.useState(null);
  const [state, setState] = React.useState(() => ChatState.initialState());
  const [loading, setLoading] = React.useState(true);
  const [error, setError] = React.useState("");
  const [historyOpen, setHistoryOpen] = React.useState(false);
  const abortsRef = React.useRef({});
  const scrollRef = React.useRef(null);
  const runs = Object.values(state.runs || {}).sort((a, b) => String(a.created_at || "").localeCompare(String(b.created_at || "")));
  const blockingRun = [...runs].reverse().find(run => ["queued", "running", "waiting_user"].includes(run.status)) || null;
  const failedRun = [...runs].reverse().find(run => run.status === "failed") || null;
  const messages = Object.values(state.messages || {}).sort((a, b) => (a.sequence || 0) - (b.sequence || 0));

  const applyRunEvent = React.useCallback((runId, event) => {
    setState(previous => ChatState.applyEvent(previous, runId, event));
    if (event.payload?.kind === "run.status" && event.payload.status === "succeeded") {
      getRun(runId).then(run => { if (run?.result_itinerary_id) onPlanResult?.(run.result_itinerary_id); }).catch(() => {});
    }
  }, [onPlanResult]);

  const subscribeRun = React.useCallback(run => {
    if (!run?.id || abortsRef.current[run.id]) return;
    let activeAbort = null;
    streamRuntimeRun(run.id, 0, {
      onAbort: abort => { activeAbort = abort; abortsRef.current[run.id] = abort; },
      onEvent: event => applyRunEvent(run.id, event),
      onClose: () => { if (abortsRef.current[run.id] === activeAbort) delete abortsRef.current[run.id]; },
      onError: () => { if (abortsRef.current[run.id] === activeAbort) delete abortsRef.current[run.id]; },
    });
  }, [applyRunEvent]);

  React.useEffect(() => {
    Object.values(abortsRef.current).forEach(abort => abort());
    abortsRef.current = {};
    setState(ChatState.initialState()); setActiveId(null); setError(""); setLoading(true);
    if (!currentUsername || !planId) { setLoading(false); return undefined; }
    let alive = true;
    listConversations(planId).then(async conversations => {
      if (!alive) return;
      const conversation = conversations.find(item => item.status !== "archived") || conversations[0];
      if (!conversation) return;
      setActiveId(conversation.id);
      const [loadedMessages, loadedRuns] = await Promise.all([getConversationMessages(conversation.id), listRuns(conversation.id)]);
      let next = ChatState.initialState();
      loadedMessages.forEach(message => { next = ChatState.upsertMessage(next, message); });
      loadedRuns.forEach(run => { next.runs[run.id] = run; });
      setState(next);
      loadedRuns.filter(run => ["queued", "running", "waiting_user"].includes(run.status)).forEach(subscribeRun);
    }).catch(errorValue => { if (alive) setError(errorValue.message || "加载行程对话失败"); })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; Object.values(abortsRef.current).forEach(abort => abort()); abortsRef.current = {}; };
  }, [planId, currentUsername]); // eslint-disable-line react-hooks/exhaustive-deps

  React.useEffect(() => {
    if (messages.length || runs.length) scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [messages.length, runs.map(run => `${run.id}:${run.status}`).join("|")]);

  const send = async () => {
    const text = query.trim();
    if (!text || (blockingRun && blockingRun.status !== "waiting_user")) return;
    setQuery(""); setError("");
    try {
      if (blockingRun?.status === "waiting_user") {
        const interactionId = blockingRun.pending_interaction?.interaction_id || blockingRun.outstanding_interaction_id;
        if (!interactionId) throw new Error("当前任务还不能接收回复");
        const resumed = await resumeRuntimeRun(blockingRun.id, interactionId, text);
        setState(previous => {
          let next = previous;
          if (resumed.accepted_message) next = ChatState.upsertMessage(next, resumed.accepted_message);
          return { ...next, runs: { ...next.runs, [blockingRun.id]: { ...next.runs[blockingRun.id], ...resumed } } };
        });
        abortsRef.current[blockingRun.id]?.(); delete abortsRef.current[blockingRun.id]; subscribeRun({ ...blockingRun, ...resumed });
        return;
      }
      let conversationId = activeId;
      if (!conversationId) { const created = await createConversation(`${plan.destination || "旅行"} · 行程调整`); conversationId = created.id; setActiveId(created.id); }
      const result = await submitConversationMessage(conversationId, text, { related_itinerary_id: planId });
      setState(previous => {
        const next = ChatState.upsertMessage(previous, result.message);
        return { ...next, runs: { ...next.runs, [result.run.id]: result.run } };
      });
      subscribeRun(result.run);
    } catch (errorValue) { setQuery(text); setError(errorValue.message || "发送失败，请重试"); }
  };

  const controlRun = async (run, action) => {
    try {
      const updated = action === "cancel" ? await cancelRuntimeRun(run.id) : await retryRuntimeRun(run.id);
      setState(previous => ({ ...previous, runs: { ...previous.runs, [updated.id]: updated } }));
      if (action === "retry") subscribeRun(updated);
    } catch (errorValue) { setError(errorValue.message || "操作失败"); }
  };
  const prompts = [`Day ${dayIdx + 1} 行程轻松一点`, `优化 Day ${dayIdx + 1} 的路线`, day?.theme ? `围绕“${day.theme}”换一个景点` : "换一个更合适的景点"];

  return <section className="rd-chat-panel">
    <div className="rd-chat-head"><div className="rd-breadcrumb"><button onClick={onBack} aria-label="返回"><UiIcon name="arrow-right" size={17} /></button><span>我的行程</span><b>/</b><em>{plan.destination} · {plan.days.length} 日</em></div><div className="rd-chat-actions"><button onClick={() => setHistoryOpen(value => !value)} aria-expanded={historyOpen}><UiIcon name="menu" size={14} />历史对话</button><button className="rd-collapse" onClick={onCollapse}><UiIcon name="arrow-right" size={14} />收起详情</button><button aria-label="更多"><span>•••</span></button></div></div>
    {historyOpen && <aside className="rd-history-drawer"><header><div><small>YOUR CONVERSATIONS</small><h3>历史对话</h3></div><button onClick={() => setHistoryOpen(false)}><UiIcon name="close" size={16} /></button></header><p>继续之前的灵感，或查看路线如何一步步变成现在的样子。</p><small>今天</small><button className="current"><strong>{plan.destination} · {plan.days.length} 日游</strong><span>“第二天不要太赶，留一点时间拍照。”</span><em>进行中 · 4 次调整</em></button><small>最近 7 天</small><button><strong>杭州 · 周末漫游</strong><span>西湖边慢走，避开人多的热门路线。</span><em>已完成 · 3 次调整</em></button></aside>}
    <div className="rd-chat-hero"><div className="rd-kicker"><i />YOUR PERSONAL TRIP PLANNER</div><h1>开始计划<br /><em>下一段旅程。</em></h1><p>把目的地和旅行想法告诉我，<br />我会陪你一起把路线慢慢变清晰。</p><div><span><UiIcon name="clock" size={14} />{displayDateRange || plan.date_range || "日期待定"}</span><span><WeatherGlyph value={plan.weather?.[dayIdx]?.icon || plan.weather?.[dayIdx]?.text} size={14} />{plan.weather?.[dayIdx]?.hi || "—"}° / {plan.weather?.[dayIdx]?.text || "天气待定"}</span></div></div>
    <div className="rd-conversation" ref={scrollRef} role="log" aria-label="行程调整对话"><div className="rd-conversation-label"><span>CONVERSATION</span><em>AI 已了解右侧行程</em></div><div className="rd-message assistant"><span className="rd-ai-avatar"><UiIcon name="sparkle" size={15} /></span><div><small>旅行助手</small><p>你好，{plan.username}。我已经把 <b>{plan.destination}</b> 的行程整理好了。告诉我你的偏好，我会一边聊天，一边替你调整右侧路线。</p><em>已结合地图上下文</em></div></div>{messages.map(message => <div className={`rd-message ${message.role === "user" ? "user" : "assistant"}`} key={message.id}>{message.role !== "user" && <span className="rd-ai-avatar"><UiIcon name="sparkle" size={15} /></span>}<div>{message.role !== "user" && <small>旅行助手</small>}<p>{message.content}</p><em>{message.role === "user" ? "刚刚" : "已结合地图上下文"}</em></div></div>)}
      {blockingRun && <div className="rd-run-card"><header><span className="rd-spinner" /><b>{blockingRun.status === "waiting_user" ? "需要你的回复" : "正在调整当前行程"}</b></header><p>{blockingRun.stage_label || blockingRun.pending_interaction?.prompt || "正在结合路线、距离和你的要求生成修改方案。"}</p>{blockingRun.status !== "waiting_user" && <button onClick={() => controlRun(blockingRun, "cancel")}>停止</button>}</div>}
      {!blockingRun && failedRun && <div className="rd-run-card failed"><header><UiIcon name="alert" size={15} /><b>这次修改没有完成</b></header><p>{failedRun.error_public?.message || "可以保留输入并重新尝试。"}</p><button onClick={() => controlRun(failedRun, "retry")}>重新尝试</button></div>}
      {loading && <div className="rd-chat-status">正在恢复关联对话…</div>}{error && <div className="rd-chat-status error" role="alert">{error}</div>}
    </div>
    <div className="rd-suggestions">{prompts.map(prompt => <button key={prompt} onClick={() => setQuery(prompt)}>{prompt}</button>)}</div>
    <div className="rd-composer"><textarea value={query} onChange={event => setQuery(event.target.value)} disabled={!!blockingRun && blockingRun.status !== "waiting_user"} onKeyDown={event => { if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); send(); } }} placeholder={blockingRun?.status === "waiting_user" ? "回答上方问题，继续这次修改…" : blockingRun ? "行程调整中…" : "告诉我你想怎么调整这趟旅程…"} /><footer><span>Enter 发送 · Shift + Enter 换行</span><button onClick={blockingRun && blockingRun.status !== "waiting_user" ? () => controlRun(blockingRun, "cancel") : send} disabled={blockingRun?.status === "waiting_user" ? !query.trim() : !blockingRun && !query.trim()} aria-label="发送"><UiIcon name={blockingRun && blockingRun.status !== "waiting_user" ? "stop" : "arrow-up"} size={17} /></button></footer></div>
    <footer className="rd-chat-footer"><span><i />旅行助手在线</span><em>AI 会根据你的偏好实时更新右侧路线</em></footer>
  </section>;
}

function TripDetailPage({ plan: planProp, planId: planIdProp, onRequestModify, currentUsername, onBack, onPlanChange }) {
  const [plan, setPlan] = React.useState(planProp);
  const [planId, setPlanId] = React.useState(planIdProp);
  const [dayIdx, setDayIdx] = React.useState(0);
  const [selectedIndex, setSelectedIndex] = React.useState(0);
  const [activeNavKey, setActiveNavKey] = React.useState(null);
  const [activeNavPair, setActiveNavPair] = React.useState(null);
  const [detailWidth, setDetailWidth] = React.useState(54);
  const [resizing, setResizing] = React.useState(false);
  const [chatOnly, setChatOnly] = React.useState(false);
  const [detailOnly, setDetailOnly] = React.useState(false);
  const [profileOpen, setProfileOpen] = React.useState(false);
  const [notice, setNotice] = React.useState("");

  React.useEffect(() => {
    setPlan(planProp); setPlanId(planIdProp); setDayIdx(0); setSelectedIndex(0);
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

  if (!plan?.days?.length) return null;
  const day = plan.days[dayIdx] || plan.days[0];
  const selected = day.items?.[selectedIndex] || day.items?.[0] || null;
  const weather = plan.weather?.[dayIdx] || plan.weather?.[0];
  const candidateSource = (plan.candidate_spots?.length ? plan.candidate_spots : day.items || []).slice(0, 3);
  const candidateFallbacks = ["秦淮河畔", "老门东", "城市园林"];
  const candidates = [...candidateSource];
  while (candidates.length < 3) candidates.push({ name: candidateFallbacks[candidates.length] });
  const badgeFallbacks = ["秦淮河", "梧桐树", "人文漫游"];
  const coverBadges = [...(plan.badges || []).slice(0, 3)];
  while (coverBadges.length < 3) coverBadges.push(badgeFallbacks[coverBadges.length]);
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
    if (!resultPlanId) return;
    try {
      const data = await getHistoryItem(resultPlanId);
      if (!data?.plan) return;
      const adapted = adaptPlan(data.plan, currentUsername);
      setPlan(adapted); setPlanId(resultPlanId); setDayIdx(0); setSelectedIndex(0);
      onPlanChange?.(data.plan, resultPlanId);
    } catch {}
  };
  const changeDay = index => {
    setDayIdx(index); setSelectedIndex(0); setActiveNavKey(null); setActiveNavPair(null);
  };

  return <div className="page page-fade trip-detail-page rd-page">
    <main className="rd-app">
      <header className="rd-top-nav">
        <div className="rd-brand"><span className="rd-brand-mark"><i /></span><strong>途见 · AI 旅行规划</strong></div>
        <div className="rd-studio"><UiIcon name="sparkle" size={14} />AI TRIP STUDIO <span>·</span> {plan.destination} / {(plan._raw?.start_date || "").slice(0, 4) || "2026"}</div>
        <div className="rd-nav-actions"><button onClick={() => showNotice("行程已保存")} aria-label="保存行程"><UiIcon name="check" size={16} /></button><button className="rd-avatar" onClick={() => setProfileOpen(true)}>{String(plan.username || currentUsername || "旅").slice(0, 1)}</button></div>
      </header>

      <section className={`rd-workspace${chatOnly ? " chat-only" : ""}${detailOnly ? " detail-only" : ""}`} style={{ "--rd-detail-width": `${detailWidth}%` }}>
        <RedesignCopilot plan={plan} planId={planId} day={day} dayIdx={dayIdx} displayDateRange={displayDateRange} currentUsername={currentUsername} onBack={onBack} onCollapse={() => { setChatOnly(true); setDetailOnly(false); }} onPlanResult={openConversationResult} />
        <div className="rd-divider" role="separator" tabIndex="0" aria-label="调整对话和行程详情宽度" aria-orientation="vertical" aria-valuemin="40" aria-valuemax="70" aria-valuenow={Math.round(detailWidth)} onPointerDown={event => { event.preventDefault(); setResizing(true); }} onDoubleClick={() => setDetailWidth(54)} onKeyDown={event => {
          if (event.key === "Home") setDetailWidth(54);
          if (event.key === "ArrowLeft") setDetailWidth(value => Math.min(70, value + 2));
          if (event.key === "ArrowRight") setDetailWidth(value => Math.max(40, value - 2));
        }}><span /></div>

        <section className="rd-detail-panel">
          <div className="rd-detail-header">
            <div className="rd-cover" style={{ backgroundImage: "linear-gradient(100deg, rgba(49,38,31,.82), rgba(74,58,46,.22)), url('/assets/trip-redesign/nanjing-cover.png')" }}><div><small>ITINERARY · 行程总览</small><h2>{plan.title}</h2><p>{coverBadges.map((badge, index) => <span key={`${badge}-${index}`}>{badge}</span>)}</p></div></div>
            <div className="rd-weather-strip">{plan.days.map((item, index) => {
              const itemWeather = plan.weather?.[index] || weather;
              const weekday = String(item.date || "").match(/周[日一二三四五六]/)?.[0];
              return <div key={index}><WeatherGlyph value={itemWeather?.icon || itemWeather?.text} size={18} /><strong>Day {index + 1}{weekday ? ` · ${weekday}` : ""}</strong><small>{itemWeather?.text || "天气待定"} <b>{itemWeather?.hi || "—"}°</b> / {itemWeather?.lo || "—"}°</small></div>;
            })}</div>
            <div className="rd-detail-title"><div><small>TRIP DETAIL / {plan.destination}</small><h2>路线详情</h2></div><div><button onClick={() => { setDetailOnly(value => !value); setChatOnly(false); }}><UiIcon name="arrow-right" size={14} />收起对话</button><button className="primary" onClick={() => showNotice("分享链接已复制")}>分享行程 <UiIcon name="share" size={14} /></button></div></div>
            <nav className="rd-day-tabs" role="tablist">{plan.days.map((item, index) => <button key={index} role="tab" aria-selected={dayIdx === index} className={dayIdx === index ? "active" : ""} onClick={() => changeDay(index)}><span>{String(index + 1).padStart(2, "0")}</span><div><strong>Day {index + 1}</strong><small>{item.date}</small></div><i /></button>)}</nav>
          </div>

          <div className="rd-detail-body">
            <section className="rd-route-column"><header><div><small>老城人文线 · {String(dayIdx + 1).padStart(2, "0")}</small><h3>{day.theme || `Day ${dayIdx + 1} 的旅行安排`}</h3></div><span><strong>{day.items.length ? 1 : 0}/{day.items.length}</strong>已完成</span></header>
              <div className="rd-candidate-title"><span><UiIcon name="sparkle" size={13} />为你挑选的地点</span><button onClick={() => showNotice("候选地点已更新")}>换一批</button></div>
              <div className="rd-candidates">{candidates.map((candidate, index) => {
                const name = candidate.name || candidate.title || `候选地点 ${index + 1}`;
                const fallbackPhotos = [
                  "/assets/trip-redesign/nanjing-cover.png",
                  "/assets/trip-redesign/nanjing-cover.png",
                  "/assets/trip-redesign/nanjing-cover.png",
                ];
                const photo = candidate.photo || candidate.image || fallbackPhotos[index];
                return <article key={`${name}-${index}`} style={{ backgroundImage: `linear-gradient(0deg, rgba(25,31,27,.72), transparent 70%), url('${photo}')` }}><strong>{name}</strong><small>{candidate.rating ? `${candidate.rating} ★` : index % 2 ? "街区 · 4.7 ★" : "人文 · 4.8 ★"}</small></article>;
              })}</div>
              <RedesignTimeline items={day.items} selectedIndex={selectedIndex} onSelect={setSelectedIndex} activeNavKey={activeNavKey} tipStatus={plan.tip_status} onNav={(key, pair) => { setActiveNavKey(key); setActiveNavPair(key ? pair : null); }} />
            </section>

            <section className="rd-map-column" aria-label={`Day ${dayIdx + 1} 路线地图`}>
              <RedesignMapFallback items={day.items} selectedIndex={selectedIndex} onSelect={setSelectedIndex} />
              <MapPanel day={day} dayIdx={dayIdx} navPair={activeNavPair} onNavClear={() => { setActiveNavKey(null); setActiveNavPair(null); }} />
              <div className="rd-map-demo-controls" aria-label="地图显示选项"><button className="active" aria-label="路线"><UiIcon name="navigation" size={17} /></button><button aria-label="图层"><UiIcon name="map" size={17} /></button><button aria-label="全屏"><UiIcon name="arrow-right" size={17} /></button></div>
              <div className="rd-map-zoom" aria-label="地图缩放"><button aria-label="放大"><UiIcon name="plus" size={14} /></button><span>12</span><button aria-label="缩小"><UiIcon name="minus" size={14} /></button></div>
              {selected && <div className="rd-map-card"><small>NOW VIEWING</small><strong>{selected.name}</strong><span>{selected.address || selected.addr || (selected.type === "attraction" ? "已同步地图位置" : "本地餐饮")} · {itemDuration(selected)}</span><div><em><UiIcon name="navigation" size={13} />路线实时联动</em><em><WeatherGlyph value={weather?.icon || weather?.text} size={13} />{weather?.hi || "—"}° {weather?.text || ""}</em></div></div>}
            </section>
          </div>
        </section>
        {chatOnly && <button className="rd-reopen detail" onClick={() => setChatOnly(false)}>显示行程详情</button>}{detailOnly && <button className="rd-reopen chat" onClick={() => setDetailOnly(false)}>显示 AI 对话</button>}
      </section>

      {profileOpen && <div className="rd-profile-backdrop" onMouseDown={() => setProfileOpen(false)}><section className="rd-profile" role="dialog" aria-modal="true" onMouseDown={event => event.stopPropagation()}><header><div><small>MY PROFILE · 我的画像</small><h2>{plan.username}的旅行画像</h2></div><button onClick={() => setProfileOpen(false)}><UiIcon name="close" size={17} /></button></header><div className="rd-profile-id"><span>{String(plan.username || "旅").slice(0, 1)}</span><div><strong>慢旅行者</strong><p>AI 已根据你的对话，整理出这份专属偏好。</p></div><em>已了解 78%</em></div><h4>旅行偏好</h4><div className="rd-profile-tags">{(plan.badges?.length ? plan.badges : ["人文历史", "城市漫步", "本地餐厅", "避开人潮"]).map(tag => <span key={tag}>{tag}</span>)}</div><div className="rd-profile-note"><UiIcon name="sparkle" size={16} /><p>之后的规划会优先推荐有故事的街区、安静的拍照点和值得专程去吃的小店。</p></div></section></div>}
      {notice && <div className="rd-toast" role="status"><UiIcon name="check" size={14} />{notice}</div>}
    </main>
  </div>;
}

Object.assign(window, { TripDetailPage });
