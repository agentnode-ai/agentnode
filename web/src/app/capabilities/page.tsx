import CapabilityFilter from "./CapabilityFilter";
import { BACKEND_URL } from "@/lib/constants";

interface Capability {
  id: string;
  display_name: string;
  description: string | null;
  category: string | null;
  package_count?: number;
}

interface CapabilitiesResponse {
  capabilities: Capability[];
  total: number;
}

async function fetchCapabilities(): Promise<Capability[]> {
  try {
    const res = await fetch(`${BACKEND_URL}/v1/capabilities`, {
      next: { revalidate: 60 },
    });
    if (!res.ok) return [];
    const data: CapabilitiesResponse = await res.json();
    return data.capabilities;
  } catch {
    return [];
  }
}

function groupByCategory(capabilities: Capability[]) {
  const grouped: Record<string, Capability[]> = {};
  for (const cap of capabilities) {
    const category = cap.category ?? "other";
    if (!grouped[category]) grouped[category] = [];
    grouped[category].push(cap);
  }
  const sortedCategories = Object.keys(grouped).sort();
  return sortedCategories.map((category) => ({
    slug: category,
    name: category.replace(/-/g, " "),
    capabilities: grouped[category],
  }));
}

export default async function CapabilitiesPage() {
  const capabilities = await fetchCapabilities();
  const groupedCategories = groupByCategory(capabilities);

  return (
    <div className="mx-auto max-w-6xl px-6 py-12">
      {/* Page header */}
      <div className="mb-10">
        <h1 className="mb-3 text-3xl font-bold text-foreground">
          Capabilities
        </h1>
        <p className="text-sm text-muted">
          Browse all capabilities in the AgentNode taxonomy. Click a capability
          to find packages that provide it.
        </p>
      </div>

      {capabilities.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-20 text-center">
          <p className="text-lg font-medium text-foreground">
            No capabilities found
          </p>
          <p className="mt-2 text-sm text-muted">
            Could not load capabilities. Please try again later.
          </p>
        </div>
      ) : (
        <CapabilityFilter
          capabilities={capabilities}
          groupedCategories={groupedCategories}
        />
      )}
    </div>
  );
}
