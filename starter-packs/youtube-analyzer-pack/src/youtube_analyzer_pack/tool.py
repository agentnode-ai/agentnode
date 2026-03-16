"""YouTube Data API v3 integration for search, video info, captions, and comments."""

from __future__ import annotations


def run(api_key: str, operation: str, **kwargs) -> dict:
    """Interact with the YouTube Data API v3.

    Args:
        api_key: YouTube Data API key.
        operation: One of "search", "video_info", "captions", "comments".
        **kwargs:
            query (str): Search query (for "search").
            max_results (int): Max results (default 5).
            video_id (str): YouTube video ID (for "video_info", "captions", "comments").

    Returns:
        dict varying by operation.
    """
    import httpx

    if not api_key:
        raise ValueError("api_key is required")

    base = "https://www.googleapis.com/youtube/v3"

    ops = {
        "search": _search,
        "video_info": _video_info,
        "captions": _captions,
        "comments": _comments,
    }

    if operation not in ops:
        raise ValueError(f"Unknown operation: {operation}. Choose from {list(ops)}")

    with httpx.Client(timeout=30.0) as client:
        return ops[operation](client, base, api_key, **kwargs)


def _search(client, base: str, api_key: str, **kwargs) -> dict:
    query = kwargs.get("query", "")
    max_results = int(kwargs.get("max_results", 5))

    if not query:
        raise ValueError("query is required for search")

    resp = client.get(
        f"{base}/search",
        params={
            "key": api_key,
            "q": query,
            "part": "snippet",
            "maxResults": min(max_results, 50),
            "type": "video",
        },
    )
    resp.raise_for_status()
    data = resp.json()

    results = []
    for item in data.get("items", []):
        snippet = item.get("snippet", {})
        results.append({
            "video_id": item.get("id", {}).get("videoId", ""),
            "title": snippet.get("title", ""),
            "description": snippet.get("description", ""),
            "channel_title": snippet.get("channelTitle", ""),
            "published_at": snippet.get("publishedAt", ""),
            "thumbnail": snippet.get("thumbnails", {}).get("high", {}).get("url", ""),
        })

    return {"results": results, "total": len(results), "query": query}


def _video_info(client, base: str, api_key: str, **kwargs) -> dict:
    video_id = kwargs.get("video_id", "")
    if not video_id:
        raise ValueError("video_id is required for video_info")

    resp = client.get(
        f"{base}/videos",
        params={
            "key": api_key,
            "id": video_id,
            "part": "snippet,statistics,contentDetails",
        },
    )
    resp.raise_for_status()
    data = resp.json()
    items = data.get("items", [])

    if not items:
        return {"error": f"Video not found: {video_id}"}

    item = items[0]
    snippet = item.get("snippet", {})
    stats = item.get("statistics", {})
    content = item.get("contentDetails", {})

    return {
        "video_id": video_id,
        "title": snippet.get("title", ""),
        "description": snippet.get("description", ""),
        "channel_title": snippet.get("channelTitle", ""),
        "published_at": snippet.get("publishedAt", ""),
        "duration": content.get("duration", ""),
        "view_count": int(stats.get("viewCount", 0)),
        "like_count": int(stats.get("likeCount", 0)),
        "comment_count": int(stats.get("commentCount", 0)),
        "tags": snippet.get("tags", []),
        "category_id": snippet.get("categoryId", ""),
    }


def _captions(client, base: str, api_key: str, **kwargs) -> dict:
    video_id = kwargs.get("video_id", "")
    if not video_id:
        raise ValueError("video_id is required for captions")

    resp = client.get(
        f"{base}/captions",
        params={
            "key": api_key,
            "videoId": video_id,
            "part": "snippet",
        },
    )
    resp.raise_for_status()
    data = resp.json()

    captions = []
    for item in data.get("items", []):
        snippet = item.get("snippet", {})
        captions.append({
            "caption_id": item.get("id", ""),
            "language": snippet.get("language", ""),
            "name": snippet.get("name", ""),
            "track_kind": snippet.get("trackKind", ""),
            "is_auto_synced": snippet.get("isAutoSynced", False),
            "is_draft": snippet.get("isDraft", False),
        })

    return {"video_id": video_id, "captions": captions, "total": len(captions)}


def _comments(client, base: str, api_key: str, **kwargs) -> dict:
    video_id = kwargs.get("video_id", "")
    max_results = int(kwargs.get("max_results", 20))

    if not video_id:
        raise ValueError("video_id is required for comments")

    resp = client.get(
        f"{base}/commentThreads",
        params={
            "key": api_key,
            "videoId": video_id,
            "part": "snippet",
            "maxResults": min(max_results, 100),
            "order": "relevance",
            "textFormat": "plainText",
        },
    )
    resp.raise_for_status()
    data = resp.json()

    comments = []
    for item in data.get("items", []):
        top = item.get("snippet", {}).get("topLevelComment", {}).get("snippet", {})
        comments.append({
            "author": top.get("authorDisplayName", ""),
            "text": top.get("textDisplay", ""),
            "like_count": int(top.get("likeCount", 0)),
            "published_at": top.get("publishedAt", ""),
            "reply_count": item.get("snippet", {}).get("totalReplyCount", 0),
        })

    return {"video_id": video_id, "comments": comments, "total": len(comments)}
