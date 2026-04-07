/* ------------------------------------------------------------------ */
/*  SVG Diagrams — adapted from agentnodepackage.com for agentnode.net */
/* ------------------------------------------------------------------ */

export function VerificationPipelineDiagram() {
  const stages = [
    { label: "Install", pts: "15 pts", color: "#3b82f6", desc: "Clean venv" },
    { label: "Import", pts: "15 pts", color: "#6366f1", desc: "Entrypoints" },
    { label: "Smoke", pts: "25 pts", color: "#7c3aed", desc: "Schema inputs" },
    { label: "Tests", pts: "15 pts", color: "#9333ea", desc: "pytest suite" },
  ];
  return (
    <svg
      viewBox="0 0 700 280"
      className="mx-auto my-6 block w-full max-w-[700px]"
    >
      <defs>
        <filter id="vp-ds">
          <feDropShadow
            dx="0"
            dy="2"
            stdDeviation="3"
            floodColor="#000"
            floodOpacity="0.3"
          />
        </filter>
      </defs>
      {stages.map((s, i) => (
        <g key={i}>
          <rect
            x={20 + i * 155}
            y="20"
            width="135"
            height="72"
            rx="10"
            fill={s.color}
            filter="url(#vp-ds)"
            opacity="0.9"
          />
          <text
            x={87 + i * 155}
            y="46"
            textAnchor="middle"
            fill="#fff"
            fontSize="13"
            fontWeight="700"
          >
            {s.label}
          </text>
          <text
            x={87 + i * 155}
            y="64"
            textAnchor="middle"
            fill="rgba(255,255,255,0.7)"
            fontSize="10"
          >
            {s.desc}
          </text>
          <text
            x={87 + i * 155}
            y="82"
            textAnchor="middle"
            fill="rgba(255,255,255,0.5)"
            fontSize="10"
          >
            {s.pts}
          </text>
          {i < 3 && (
            <text
              x={155 + i * 155}
              y="56"
              fill="#4f46e5"
              fontSize="16"
              textAnchor="middle"
            >
              {"\u2192"}
            </text>
          )}
        </g>
      ))}
      <line
        x1="350"
        y1="92"
        x2="350"
        y2="120"
        stroke="#7c3aed"
        strokeWidth="2"
        strokeDasharray="4,3"
      />
      <rect
        x="120"
        y="125"
        width="460"
        height="70"
        rx="10"
        fill="rgba(124,58,237,0.08)"
        stroke="rgba(124,58,237,0.2)"
        strokeWidth="1"
      />
      <text
        x="350"
        y="143"
        textAnchor="middle"
        fill="#b89aff"
        fontSize="11"
        fontWeight="600"
      >
        QUALITY CHECKS (multi-run)
      </text>
      {[
        { l: "Reliability", p: "10" },
        { l: "Determinism", p: "5" },
        { l: "Contract", p: "10" },
        { l: "Warnings", p: "\u221210" },
      ].map((q, i) => (
        <g key={i}>
          <text
            x={160 + i * 110}
            y="170"
            textAnchor="middle"
            fill="#a8a0c0"
            fontSize="10"
          >
            {q.l}
          </text>
          <text
            x={160 + i * 110}
            y="184"
            textAnchor="middle"
            fill="#9b6dff"
            fontSize="10"
            fontWeight="600"
          >
            {q.p} pts
          </text>
        </g>
      ))}
      <line
        x1="350"
        y1="195"
        x2="350"
        y2="215"
        stroke="#7c3aed"
        strokeWidth="2"
        strokeDasharray="4,3"
      />
      {[
        { l: "Gold", r: "90\u2013100", c: "#fbbf24", x: 130 },
        { l: "Verified", r: "70\u201389", c: "#4ade80", x: 280 },
        { l: "Partial", r: "50\u201369", c: "#facc15", x: 420 },
        { l: "Unverified", r: "<50", c: "#6b7280", x: 555 },
      ].map((t, i) => (
        <g key={i}>
          <rect
            x={t.x}
            y="220"
            width="110"
            height="42"
            rx="8"
            fill="rgba(0,0,0,0.3)"
            stroke={t.c}
            strokeWidth="1.5"
          />
          <circle cx={t.x + 18} cy="241" r="5" fill={t.c} />
          <text
            x={t.x + 30}
            y="237"
            fill="#e2dff0"
            fontSize="12"
            fontWeight="600"
          >
            {t.l}
          </text>
          <text x={t.x + 30} y="252" fill="#8a82a8" fontSize="10">
            {t.r}
          </text>
        </g>
      ))}
    </svg>
  );
}

export function TrustPyramidDiagram() {
  return (
    <svg
      viewBox="0 0 600 240"
      className="mx-auto my-6 block w-full max-w-[600px]"
    >
      <defs>
        <filter id="tp-ds">
          <feDropShadow
            dx="0"
            dy="1"
            stdDeviation="2"
            floodColor="#000"
            floodOpacity="0.25"
          />
        </filter>
      </defs>
      {[
        {
          label: "Curated",
          desc: "Manual code audit \u00B7 Highest assurance",
          y: 10,
          w: 180,
          color: "#f59e0b",
        },
        {
          label: "Trusted",
          desc: "Zero findings \u00B7 Tests pass \u00B7 Community usage",
          y: 65,
          w: 280,
          color: "#22c55e",
        },
        {
          label: "Verified",
          desc: "Identity confirmed \u00B7 2FA \u00B7 Bandit scan passed",
          y: 120,
          w: 400,
          color: "#3b82f6",
        },
        {
          label: "Unverified",
          desc: "Manifest valid \u00B7 Newly published",
          y: 175,
          w: 520,
          color: "#4b5563",
        },
      ].map((t, i) => (
        <g key={i}>
          <rect
            x={(600 - t.w) / 2}
            y={t.y}
            width={t.w}
            height="48"
            rx="8"
            fill={t.color}
            filter="url(#tp-ds)"
            opacity="0.85"
          />
          <text
            x="300"
            y={t.y + 22}
            textAnchor="middle"
            fill="#fff"
            fontSize="13"
            fontWeight="700"
          >
            {t.label}
          </text>
          <text
            x="300"
            y={t.y + 38}
            textAnchor="middle"
            fill="rgba(255,255,255,0.65)"
            fontSize="10"
          >
            {t.desc}
          </text>
        </g>
      ))}
      {[55, 110, 165].map((y, i) => (
        <text
          key={i}
          x="300"
          y={y + 6}
          textAnchor="middle"
          fill="#4f46e5"
          fontSize="10"
        >
          {"\u25B2"}
        </text>
      ))}
    </svg>
  );
}

export function LifecycleDiagram() {
  const steps = [
    { l: "Write", s: "code + manifest", c: "#3b82f6", i: "\u270E" },
    { l: "Validate", s: "agentnode validate", c: "#6366f1", i: "\u2713" },
    { l: "Publish", s: "sign + upload", c: "#7c3aed", i: "\u2191" },
    { l: "Verify", s: "sandbox 0\u2013100", c: "#f59e0b", i: "\u2605" },
    { l: "Discover", s: "search + resolve", c: "#22c55e", i: "\u25CE" },
    { l: "Install", s: "hash + lockfile", c: "#10b981", i: "\u2193" },
  ];
  return (
    <svg
      viewBox="0 0 700 90"
      className="mx-auto my-5 block w-full max-w-[700px]"
    >
      {steps.map((s, i) => (
        <g key={i}>
          <rect
            x={6 + i * 115}
            y="10"
            width="100"
            height="60"
            rx="8"
            fill={s.c}
            opacity="0.2"
            stroke={s.c}
            strokeWidth="1"
          />
          <text
            x={56 + i * 115}
            y="33"
            textAnchor="middle"
            fill={s.c}
            fontSize="16"
          >
            {s.i}
          </text>
          <text
            x={56 + i * 115}
            y="50"
            textAnchor="middle"
            fill="#e2dff0"
            fontSize="11"
            fontWeight="600"
          >
            {s.l}
          </text>
          <text
            x={56 + i * 115}
            y="63"
            textAnchor="middle"
            fill="#8a82a8"
            fontSize="9"
          >
            {s.s}
          </text>
          {i < 5 && (
            <text
              x={106 + i * 115}
              y="44"
              fill="#4f46e5"
              fontSize="13"
            >
              {"\u2192"}
            </text>
          )}
        </g>
      ))}
    </svg>
  );
}
