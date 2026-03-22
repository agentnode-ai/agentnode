"""Initialize Meilisearch index with correct settings."""
import httpx

from app.config import settings

INDEX_NAME = "packages"

INDEX_SETTINGS = {
    "searchableAttributes": [
        "name", "summary", "description", "capability_ids", "tags", "publisher_name",
    ],
    "filterableAttributes": [
        "package_type", "capability_ids", "frameworks", "runtime", "trust_level",
        "is_deprecated", "publisher_slug", "verification_tier",
    ],
    "sortableAttributes": ["download_count", "published_at", "verification_score"],
    "rankingRules": [
        "words", "typo", "proximity", "attribute", "sort", "exactness",
        "verification_score:desc", "download_count:desc",
    ],
}


def init_index():
    headers = {"Authorization": f"Bearer {settings.MEILISEARCH_KEY}"}
    base = settings.MEILISEARCH_URL

    # Create index
    resp = httpx.post(
        f"{base}/indexes",
        json={"uid": INDEX_NAME, "primaryKey": "slug"},
        headers=headers,
    )
    if resp.status_code in (200, 201, 202):
        print(f"Index '{INDEX_NAME}' created.")
    elif resp.status_code == 409:
        print(f"Index '{INDEX_NAME}' already exists.")
    else:
        print(f"Index creation response: {resp.status_code} {resp.text}")

    # Update settings
    resp = httpx.patch(
        f"{base}/indexes/{INDEX_NAME}/settings",
        json=INDEX_SETTINGS,
        headers=headers,
    )
    print(f"Index settings updated: {resp.status_code}")


if __name__ == "__main__":
    init_index()
