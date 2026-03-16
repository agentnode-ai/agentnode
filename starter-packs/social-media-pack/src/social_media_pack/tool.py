"""Social media integration for Twitter (X) and LinkedIn via their REST APIs."""

from __future__ import annotations


def run(platform: str, token: str, operation: str, **kwargs) -> dict:
    """Post to or read from social media platforms.

    Args:
        platform: Social media platform - "twitter" or "linkedin".
        token: Bearer token / access token for the platform.
        operation: Platform-specific operation (see below).
        **kwargs:
            text (str): Post content (for "post_tweet", "share_post").
            limit (int): Max results (for "get_timeline", default 10).

    Twitter operations:
        "post_tweet": Post a tweet. Requires text.
        "get_timeline": Get recent tweets from home timeline.

    LinkedIn operations:
        "share_post": Share a text post. Requires text.

    Returns:
        dict varying by platform and operation.
    """
    import httpx

    if not token:
        raise ValueError("token is required")

    platforms = {
        "twitter": _twitter,
        "linkedin": _linkedin,
    }

    if platform not in platforms:
        raise ValueError(f"Unsupported platform: {platform}. Choose from {list(platforms)}")

    return platforms[platform](token, operation, **kwargs)


def _twitter(token: str, operation: str, **kwargs) -> dict:
    import httpx

    base = "https://api.twitter.com/2"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    if operation == "post_tweet":
        return _twitter_post_tweet(base, headers, **kwargs)
    elif operation == "get_timeline":
        return _twitter_get_timeline(base, headers, **kwargs)
    else:
        raise ValueError(f"Unknown Twitter operation: {operation}. Choose from: post_tweet, get_timeline")


def _twitter_post_tweet(base: str, headers: dict, **kwargs) -> dict:
    import httpx

    text = kwargs.get("text", "")
    if not text:
        raise ValueError("text is required for post_tweet")

    with httpx.Client(timeout=30.0) as client:
        resp = client.post(
            f"{base}/tweets",
            headers=headers,
            json={"text": text},
        )
        resp.raise_for_status()
        data = resp.json()

    tweet_data = data.get("data", {})
    return {
        "status": "posted",
        "platform": "twitter",
        "tweet_id": tweet_data.get("id", ""),
        "text": tweet_data.get("text", text),
    }


def _twitter_get_timeline(base: str, headers: dict, **kwargs) -> dict:
    import httpx

    limit = int(kwargs.get("limit", 10))

    with httpx.Client(timeout=30.0) as client:
        # Use reverse chronological timeline (requires user context / OAuth 2.0 user token)
        resp = client.get(
            f"{base}/tweets/search/recent",
            headers=headers,
            params={
                "max_results": min(limit, 100),
                "tweet.fields": "created_at,public_metrics,author_id",
            },
        )
        resp.raise_for_status()
        data = resp.json()

    tweets = []
    for tweet in data.get("data", []):
        metrics = tweet.get("public_metrics", {})
        tweets.append({
            "id": tweet.get("id", ""),
            "text": tweet.get("text", ""),
            "author_id": tweet.get("author_id", ""),
            "created_at": tweet.get("created_at", ""),
            "likes": metrics.get("like_count", 0),
            "retweets": metrics.get("retweet_count", 0),
            "replies": metrics.get("reply_count", 0),
        })

    return {"platform": "twitter", "tweets": tweets, "total": len(tweets)}


def _linkedin(token: str, operation: str, **kwargs) -> dict:
    import httpx

    if operation == "share_post":
        return _linkedin_share_post(token, **kwargs)
    else:
        raise ValueError(f"Unknown LinkedIn operation: {operation}. Choose from: share_post")


def _linkedin_share_post(token: str, **kwargs) -> dict:
    import httpx

    text = kwargs.get("text", "")
    if not text:
        raise ValueError("text is required for share_post")

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "X-Restli-Protocol-Version": "2.0.0",
    }

    # First, get the user's profile URN
    with httpx.Client(timeout=30.0) as client:
        me_resp = client.get(
            "https://api.linkedin.com/v2/userinfo",
            headers={"Authorization": f"Bearer {token}"},
        )
        me_resp.raise_for_status()
        user_sub = me_resp.json().get("sub", "")

        # Create a share using the UGC Post API
        payload = {
            "author": f"urn:li:person:{user_sub}",
            "lifecycleState": "PUBLISHED",
            "specificContent": {
                "com.linkedin.ugc.ShareContent": {
                    "shareCommentary": {"text": text},
                    "shareMediaCategory": "NONE",
                }
            },
            "visibility": {
                "com.linkedin.ugc.MemberNetworkVisibility": "PUBLIC"
            },
        }

        resp = client.post(
            "https://api.linkedin.com/v2/ugcPosts",
            headers=headers,
            json=payload,
        )
        resp.raise_for_status()
        data = resp.json()

    return {
        "status": "posted",
        "platform": "linkedin",
        "post_id": data.get("id", ""),
        "text": text,
    }
