function DemoValidation({ plan }) {
  const [budget, setBudget] = React.useState(null);
  React.useEffect(() => {
    let alive = true;
    const read = () => fetch('/api/demo/status').then(r => r.json()).then(d => { if (alive) setBudget(d.budget); }).catch(() => {});
    read(); const timer = setInterval(read, 15000);
    return () => { alive = false; clearInterval(timer); };
  }, []);
  const status = plan.validation_summary?.status;
  const checks = plan.pending_checks || [];
  const labels = { opening: '开放时间', traffic: '交通', transport: '交通', accessibility: '无障碍条件', reservation: '预约' };
  const reasons = { regular_rules_with_unverified_exceptions: '常规时段已检查，节假日及临时调整需核实', missing_opening_source_or_visit_date: '暂无可核实的开放时间', date_exception_requires_verification: '特殊日期安排需核实' };
  return <aside className="demo-validation" aria-label="行程验收与待核实事项">
    <strong>{status === 'failed' ? '行程未通过检查' : status === 'supported_checks_passed' ? '已通过所支持的检查' : '行程含待核实事项'}</strong>
    <p>地图连线为景点顺序示意，不代表真实交通路径。{budget && ` 演示联网剩余 ${budget.remaining}/50 次。`}</p>
    {!!checks.length && <details><summary>查看 {checks.length} 项出行前需核实的信息</summary><ul>{checks.map((item, i) => <li key={i}>{item.day ? `第 ${item.day} 天 · ` : ''}{item.name || item.from || ''}：{labels[item.kind] || '行程信息'} — {reasons[item.reason] || '现有证据不足，请在出行前确认。'}</li>)}</ul></details>}
  </aside>;
}
