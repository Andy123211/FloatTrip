// mascot.jsx — 小鸭旅行向导「途途」
// 用法保持不变: <Mascot size={160} pose="wave" />
// pose: idle | wave | walk | point | cheer | think

function Mascot({ size = 140, pose = "idle", flip = false, style = {} }) {
  const id = React.useId().replace(/:/g, "");
  const showMap = !["wave", "cheer", "walk"].includes(pose);
  return (
    <div
      className={`mascot mascot-${pose}`}
      style={{ width: size, height: size * 1.18, flexShrink: 0, ...style, transform: flip ? "scaleX(-1)" : undefined }}
      aria-label="小鸭旅行向导途途"
      role="img"
    >
      <style>{`
        .mascot svg { width: 100%; height: 100%; display: block; overflow: visible; }
        .mascot .m-body-grp { transform-origin: 60px 120px; }
        .mascot .m-arm-r { transform-origin: 81px 89px; }
        .mascot .m-arm-l { transform-origin: 39px 89px; }
        .mascot .m-leg-l { transform-origin: 48px 121px; }
        .mascot .m-leg-r { transform-origin: 72px 121px; }
        .mascot .m-eyes { transform-origin: 60px 53px; animation: m-blink 4.2s infinite; }
        .mascot .m-flag { transform-origin: 87px 76px; }
        .mascot .m-shadow { transform-origin: 60px 142px; }
        @keyframes m-blink { 0%, 94%, 100% { transform: scaleY(1); } 96.5% { transform: scaleY(.12); } }

        @media (prefers-reduced-motion: no-preference) {
          .mascot-idle .m-body-grp, .mascot-wave .m-body-grp, .mascot-point .m-body-grp, .mascot-think .m-body-grp { animation: m-bob 3.2s ease-in-out infinite; }
          @keyframes m-bob { 0%,100% { transform: translateY(0); } 50% { transform: translateY(-3px); } }
          .mascot-wave .m-arm-r { animation: m-wave 1.6s ease-in-out infinite; }
          @keyframes m-wave { 0%,100% { transform: rotate(-64deg); } 50% { transform: rotate(-28deg); } }
          .mascot-walk .m-body-grp { animation: m-trot .55s ease-in-out infinite; }
          @keyframes m-trot { 0%,100% { transform: translateY(0) rotate(1.2deg); } 50% { transform: translateY(-4px) rotate(-1.2deg); } }
          .mascot-walk .m-leg-l { animation: m-step .55s ease-in-out infinite; }
          .mascot-walk .m-leg-r { animation: m-step .55s ease-in-out infinite reverse; }
          @keyframes m-step { 0%,100% { transform: rotate(20deg); } 50% { transform: rotate(-20deg); } }
          .mascot-walk .m-arm-l { animation: m-swing .55s ease-in-out infinite; }
          @keyframes m-swing { 0%,100% { transform: rotate(-14deg); } 50% { transform: rotate(14deg); } }
          .mascot-cheer .m-arm-r { animation: m-cheer-r 1s ease-in-out infinite; }
          .mascot-cheer .m-arm-l { animation: m-cheer-l 1s ease-in-out infinite; }
          @keyframes m-cheer-r { 0%,100% { transform: rotate(-50deg); } 50% { transform: rotate(-69deg); } }
          @keyframes m-cheer-l { 0%,100% { transform: rotate(50deg); } 50% { transform: rotate(69deg); } }
          .mascot-cheer .m-body-grp { animation: m-hop .9s ease-in-out infinite; }
          @keyframes m-hop { 0%,100% { transform: translateY(0); } 40% { transform: translateY(-7px); } }
          .mascot-think .m-flag { animation: m-tilt 2.6s ease-in-out infinite; }
          @keyframes m-tilt { 0%,100% { transform: rotate(-4deg); } 50% { transform: rotate(5deg); } }
        }
      `}</style>
      <svg viewBox="0 0 120 150" aria-hidden="true">
        <defs>
          <linearGradient id={`duckBody-${id}`} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0" stopColor="var(--mascot-hat)" />
            <stop offset="1" stopColor="var(--mascot-skin)" />
          </linearGradient>
          <linearGradient id={`duckMap-${id}`} x1="0" y1="0" x2="1" y2="1">
            <stop offset="0" stopColor="#F1F6D8" />
            <stop offset="1" stopColor="var(--mascot-coat)" />
          </linearGradient>
        </defs>

        <ellipse className="m-shadow" cx="60" cy="141" rx="29" ry="5.5" fill="#6B4E3D" opacity=".12" />
        <g className="m-body-grp" stroke="#6B4E3D" strokeWidth="3.2" strokeLinecap="round" strokeLinejoin="round">
          <path d="M82 84q15 0 15 13v22q0 9-11 9H78V92q0-8 4-8Z" fill="#C68B4D" />
          <path d="M85 89q7 5 8 13M84 111h10" fill="none" opacity=".72" />

          <g className="m-leg-l">
            <path d="M48 118v12" fill="none" />
            <path d="M48 129q-11 0-12 7 1 5 14 4 8-1 7-6-1-4-9-5Z" fill="#F2A84F" />
          </g>
          <g className="m-leg-r">
            <path d="M72 118v12" fill="none" />
            <path d="M72 129q11 0 12 7-1 5-14 4-8-1-7-6 1-4 9-5Z" fill="#F2A84F" />
          </g>

          <path d="M35 91q2-22 25-22t25 22l4 24q1 16-29 16t-29-16Z" fill={`url(#duckBody-${id})`} />
          <path d="M43 91q5-12 17-12t17 12l3 24q0 9-20 9t-20-9Z" fill="#FFFDF8" strokeWidth="2.6" />

          <g className="m-arm-l" style={pose === "cheer" ? undefined : { transform: "rotate(5deg)" }}>
            <path d="M39 88q-14 7-13 24 1 11 10 12 9 0 11-12l1-18Z" fill="var(--mascot-skin)" />
          </g>

          <path d="M31 48q0-20 16-29 1-12 8-13 6 0 7 9 3-8 10-8 8 1 7 15 12 10 12 27 0 29-31 37-29-6-29-35Z" fill={`url(#duckBody-${id})`} />
          <path d="M31 57q10 2 14-7 3 14 15 14t15-14q4 9 14 7-2 23-29 29-26-5-29-29Z" fill="#FFFDF8" stroke="none" />

          <g className="m-eyes" fill="#5A3D2E" stroke="none">
            {pose === "cheer" ? (
              <g fill="none" stroke="#5A3D2E" strokeWidth="3"><path d="m40 52 5-4 5 4"/><path d="m70 52 5-4 5 4"/></g>
            ) : (
              <>
                <ellipse cx="44" cy="51" rx="6.6" ry="7.8" />
                <ellipse cx="76" cy="51" rx="6.6" ry="7.8" />
                <circle cx="46" cy="48" r="2" fill="#FFFDF8" />
                <circle cx="78" cy="48" r="2" fill="#FFFDF8" />
              </>
            )}
          </g>

          <path d="M48 63q12-10 24 0 9 3 8 8-2 6-12 5-8 5-16 0-10 1-12-5-1-5 8-8Z" fill="#A87860" />
          <circle cx="55" cy="66" r="1.2" fill="#6B4E3D" stroke="none" />
          <circle cx="65" cy="66" r="1.2" fill="#6B4E3D" stroke="none" />

          <path d="M69 21q2-12 10-12 9 2 8 16" fill="#C9964D" />
          <path d="M64 25q12-7 27 1-2 5-13 5-10 0-14-6Z" fill="#E2B260" />
          <path d="m75 13 9 5" fill="none" strokeWidth="2.2" />

          <g
            className="m-arm-r"
            style={pose === "point" ? { transform: "rotate(-35deg)" } : pose === "walk" ? { transform: "rotate(13deg)" } : pose === "wave" || pose === "cheer" ? undefined : { transform: "rotate(-3deg)" }}
          >
            <path d="M81 88q14 7 13 24-1 11-10 12-9 0-11-12l-1-18Z" fill="var(--mascot-skin)" />
            {pose === "point" && (
              <g className="m-flag">
                <path d="M91 108V76" fill="none" strokeWidth="2.6" />
                <path d="M91 77q8-10 16 0-1 8-8 16-7-8-8-16Z" fill="#FFD166" />
                <circle cx="99" cy="77" r="2.2" fill="#FFFDF8" />
              </g>
            )}
          </g>

          {showMap && (
            <g>
              <path d="m38 101 14-4 10 5 14-6 7 5-3 25-14 5-11-6-14 5Z" fill="#FFFDF8" />
              <path d="m42 102 10-2 10 5 14-6 4 3-3 20-11 5-11-6-12 4Z" fill={`url(#duckMap-${id})`} stroke="none" />
              <path d="m52 100 3 21m7-16 4 22" fill="none" stroke="#FFFDF8" strokeWidth="3" />
            </g>
          )}

          {pose === "think" && (
            <g className="m-flag" fill="#FFE39A" strokeWidth="2.4">
              <circle cx="94" cy="52" r="3" />
              <path d="M97 40q0-8 7-8 7 0 7 7 0 4-5 7-2 1-2 4" fill="none" />
              <circle cx="104" cy="56" r="1" fill="#6B4E3D" stroke="none" />
            </g>
          )}
        </g>
      </svg>
    </div>
  );
}

Object.assign(window, { Mascot });
