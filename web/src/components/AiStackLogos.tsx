/**
 * Logo heights are calibrated for visual uniformity on dark backgrounds.
 * All SVGs are white-on-transparent, monochrome. Heights tuned per aspect
 * ratio so logos have similar visual weight.
 *
 * Plain <img> tags used instead of next/image — SVGs don't benefit from
 * image optimization and next/image blocks SVGs without extra config.
 *
 * Three integration tiers:
 *   1. Native Runtime — providers with built-in run() loop
 *   2. Framework Adapters — published adapter packages
 *   3. Compatible — ANP is standard Python, works everywhere
 */

type Logo = { name: string; src: string; height: number };

const nativeRuntime: Logo[] = [
  { name: "ChatGPT", src: "/logos/chatgpt.svg", height: 26 },
  { name: "Claude", src: "/logos/claude.svg", height: 22 },
  { name: "Gemini", src: "/logos/gemini.svg", height: 18 },
];

const frameworkAdapters: Logo[] = [
  { name: "LangChain", src: "/logos/langchain.svg", height: 22 },
  { name: "CrewAI", src: "/logos/crewai.png", height: 20 },
  { name: "MCP", src: "/logos/mcp.svg", height: 20 },
];

const compatible: Logo[] = [
  { name: "Mistral", src: "/logos/mistral.svg", height: 20 },
  { name: "Ollama", src: "/logos/ollama.svg", height: 26 },
  { name: "DeepSeek", src: "/logos/deepseek.svg", height: 20 },
  { name: "Qwen", src: "/logos/qwen.svg", height: 22 },
];

function LogoRow({ logos }: { logos: Logo[] }) {
  return (
    <div className="flex flex-wrap items-center justify-center gap-x-10 gap-y-6 sm:gap-x-14">
      {logos.map((logo) => (
        <div
          key={logo.name}
          className="flex items-center opacity-50 transition-opacity duration-200 hover:opacity-90"
          title={logo.name}
        >
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            src={logo.src}
            alt={logo.name}
            height={logo.height}
            className="max-w-[120px] sm:max-w-[140px]"
            style={{ height: logo.height, width: "auto" }}
          />
        </div>
      ))}
    </div>
  );
}

export default function AiStackLogos() {
  return (
    <div className="space-y-10">
      {/* Native Runtime Integration */}
      <div>
        <p className="mb-4 text-center text-xs font-medium uppercase tracking-wider text-primary">
          Native Runtime Integration
        </p>
        <LogoRow logos={nativeRuntime} />
      </div>

      {/* Framework Adapters */}
      <div>
        <p className="mb-4 text-center text-xs font-medium uppercase tracking-wider text-muted">
          Framework Adapters
        </p>
        <LogoRow logos={frameworkAdapters} />
      </div>

      {/* Compatible */}
      <div>
        <p className="mb-4 text-center text-xs font-medium uppercase tracking-wider text-muted">
          Compatible (standard Python)
        </p>
        <LogoRow logos={compatible} />
      </div>
    </div>
  );
}
