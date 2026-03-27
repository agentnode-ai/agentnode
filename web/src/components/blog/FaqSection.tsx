interface FaqItem {
  question: string;
  answer: string;
}

/**
 * Extract FAQ structured data from blog post HTML content.
 * Posts embed FAQ as a JSON-LD script tag inside content_html.
 * Returns the cleaned HTML (script + heading removed) and parsed FAQ items.
 */
export function extractFaqFromHtml(html: string): {
  cleanHtml: string;
  faqItems: FaqItem[];
} {
  const faqScriptRegex =
    /<script type="application\/ld\+json">\s*(\{"@context"[^]*?"@type"\s*:\s*"FAQPage"[^]*?\})\s*<\/script>/;
  const match = html.match(faqScriptRegex);

  if (!match) return { cleanHtml: html, faqItems: [] };

  let faqItems: FaqItem[] = [];
  try {
    const data = JSON.parse(match[1]);
    faqItems = (data.mainEntity || []).map(
      (q: { name: string; acceptedAnswer?: { text?: string } }) => ({
        question: q.name,
        answer: q.acceptedAnswer?.text || "",
      }),
    );
  } catch {
    return { cleanHtml: html, faqItems: [] };
  }

  let cleanHtml = html.replace(match[0], "");
  // Remove the bare heading that precedes the script tag
  cleanHtml = cleanHtml.replace(
    /<h2>\s*Frequently Asked Questions\s*<\/h2>/,
    "",
  );

  return { cleanHtml, faqItems };
}

export default function FaqSection({ items }: { items: FaqItem[] }) {
  if (items.length === 0) return null;

  return (
    <section className="mt-12 border-t border-border pt-8">
      <h2 className="mb-6 text-2xl font-bold">Frequently Asked Questions</h2>
      <dl className="space-y-6">
        {items.map((item, i) => (
          <div key={i} className="rounded-lg border border-border bg-card/50 p-5">
            <dt className="mb-2 text-base font-semibold text-foreground">
              {item.question}
            </dt>
            <dd className="text-sm leading-relaxed text-muted">
              {item.answer}
            </dd>
          </div>
        ))}
      </dl>
    </section>
  );
}
