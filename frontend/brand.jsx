// FloatTrip — a horizon and a continuous journey, shared across all surfaces.
function BrandMark({ size = 40, style = {}, className = "", decorative = false }) {
  return <svg className={`brand-mark ${className}`} width={size} height={size} style={style} viewBox="0 0 64 64" fill="none" role={decorative ? undefined : "img"} aria-hidden={decorative || undefined} aria-label={decorative ? undefined : "途见 FloatTrip"}>
    <rect width="64" height="64" rx="17" fill="#151A20" />
    <path d="M12 32h40M22 27a10 10 0 0 1 20 0" stroke="#DCEEFF" strokeWidth="2.5" strokeLinecap="round" />
    <path d="M36 37c-18 4-19 12-5 15" stroke="#9DC8F0" strokeWidth="2.5" strokeLinecap="round" />
    <circle cx="36" cy="37" r="2.5" fill="#DCEEFF" />
  </svg>;
}

// Modal focus stays inside the active dialog and returns to its trigger.
function useDialogFocus(ref, onClose, initialFocus = null) {
  const closeRef = React.useRef(onClose);
  closeRef.current = onClose;
  React.useEffect(() => {
    const previous = document.activeElement;
    const overflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const focusable = () => [...(ref.current?.querySelectorAll('button, input, select, textarea, a[href], [tabindex="0"]') || [])].filter(item => !item.disabled && item.getClientRects().length);
    ((initialFocus && ref.current?.querySelector(initialFocus)) || focusable()[0] || ref.current)?.focus({ preventScroll: true });
    const keydown = event => {
      if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); closeRef.current?.(); }
      if (event.key !== "Tab") return;
      const items = focusable(), first = items[0], last = items[items.length - 1];
      if (!first) { event.preventDefault(); ref.current?.focus(); }
      else if (event.shiftKey && (document.activeElement === first || document.activeElement === ref.current)) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && (document.activeElement === last || document.activeElement === ref.current)) { event.preventDefault(); first.focus(); }
    };
    document.addEventListener("keydown", keydown);
    return () => { document.body.style.overflow = overflow; document.removeEventListener("keydown", keydown); if (previous?.isConnected) previous.focus(); };
  }, []);
}
