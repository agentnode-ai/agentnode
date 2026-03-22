# Global reserved — never usable as post type url_prefix
RESERVED_URL_PREFIXES = frozenset({
    "admin", "auth", "dashboard", "api", "search", "docs", "for-developers",
    "why-agentnode", "import", "publish", "builder", "capabilities", "compare",
    "license", "discover", "packages", "publishers", "sitemap",
})
# Note: "blog", "tutorials", "changelog", "case-studies" are NOT in this set.
# They are blocked by UNIQUE(url_prefix) on blog_post_types, not by this list.
# This list = app-level reserved paths. Type prefixes = DB-enforced uniqueness.

STATIC_PAGES = [
    {"path": "/",              "priority": 1.0, "changefreq": "daily",   "indexable": True},
    {"path": "/search",        "priority": 0.8, "changefreq": "daily",   "indexable": True},
    {"path": "/discover",      "priority": 0.8, "changefreq": "daily",   "indexable": True},
    {"path": "/docs",          "priority": 0.7, "changefreq": "weekly",  "indexable": True},
    {"path": "/for-developers","priority": 0.7, "changefreq": "monthly", "indexable": True},
    {"path": "/why-agentnode", "priority": 0.6, "changefreq": "monthly", "indexable": True},
    {"path": "/import",        "priority": 0.6, "changefreq": "monthly", "indexable": True},
    {"path": "/publish",       "priority": 0.6, "changefreq": "monthly", "indexable": True},
    {"path": "/builder",       "priority": 0.7, "changefreq": "weekly",  "indexable": True},
    {"path": "/capabilities",  "priority": 0.6, "changefreq": "monthly", "indexable": True},
    {"path": "/compare",       "priority": 0.5, "changefreq": "monthly", "indexable": True},
    {"path": "/license",       "priority": 0.3, "changefreq": "yearly",  "indexable": True},
]
