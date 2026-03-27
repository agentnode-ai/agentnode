from langchain.tools import tool


class RateLimiter:
    """Simple in-memory rate limiter."""
    def __init__(self, max_calls: int = 10):
        self.max_calls = max_calls
        self.calls = 0

    def check(self) -> bool:
        self.calls += 1
        return self.calls <= self.max_calls

    def reset(self):
        self.calls = 0


limiter = RateLimiter(max_calls=100)


@tool
def rate_limited_search(query: str) -> dict:
    """Search with rate limiting."""
    if not limiter.check():
        return {"error": "Rate limit exceeded", "results": []}
    return {"results": [f"Result for: {query}"], "rate_limit_remaining": limiter.max_calls - limiter.calls}
