"""Tests for seo-optimizer-pack."""

import pytest


GOOD_HTML = """<!DOCTYPE html>
<html>
<head>
    <title>Best Python Programming Guide for Beginners 2026</title>
    <meta name="description" content="Learn Python programming from scratch with this comprehensive guide covering basics, data structures, web development, and more. Perfect for beginners.">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <link rel="canonical" href="https://example.com/python-guide">
</head>
<body>
    <h1>Python Programming Guide for Beginners</h1>
    <p>{body}</p>
    <a href="/about">About</a>
    <a href="/contact">Contact</a>
    <a href="/docs">Docs</a>
    <a href="https://python.org">Python</a>
    <img src="logo.png" alt="Python logo">
</body>
</html>""".format(body=" ".join(["Python programming is a great skill to learn for modern software development."] * 60))

MINIMAL_HTML = "<html><body><p>Short.</p></body></html>"


def test_keyword_density():
    from seo_optimizer_pack.tool import _keyword_density

    assert _keyword_density("the cat sat on the mat", "cat") == pytest.approx(16.67, abs=0.1)
    assert _keyword_density("hello world", "missing") == 0.0
    assert _keyword_density("", "anything") == 0.0
    assert _keyword_density("some text", "") == 0.0


def test_perfect_page():
    from seo_optimizer_pack.tool import run

    result = run(html=GOOD_HTML, keyword="python")
    assert result["score"] >= 80
    assert isinstance(result["issues"], list)
    assert isinstance(result["recommendations"], list)
    assert result["meta"]["word_count"] > 300


def test_missing_title():
    from seo_optimizer_pack.tool import run

    html = "<html><head></head><body><h1>Hello</h1><p>Content here.</p></body></html>"
    result = run(html=html)
    assert any("title" in i.lower() for i in result["issues"])
    assert result["score"] < 100


def test_missing_meta_description():
    from seo_optimizer_pack.tool import run

    html = "<html><head><title>Valid Title That Is Long Enough Here</title></head><body><h1>Hi</h1></body></html>"
    result = run(html=html)
    assert any("meta description" in i.lower() for i in result["issues"])


def test_missing_h1():
    from seo_optimizer_pack.tool import run

    html = "<html><head><title>Valid Title That Is Long Enough Here</title></head><body><p>No heading.</p></body></html>"
    result = run(html=html)
    assert any("h1" in i.lower() for i in result["issues"])


def test_multiple_h1():
    from seo_optimizer_pack.tool import run

    html = "<html><head><title>Valid Title That Is Long Enough Here</title></head><body><h1>First</h1><h1>Second</h1></body></html>"
    result = run(html=html)
    assert any("multiple h1" in i.lower() for i in result["issues"])


def test_images_without_alt():
    from seo_optimizer_pack.tool import run

    html = '<html><head><title>Valid Title That Is Long Enough Here</title></head><body><h1>Hi</h1><img src="a.png"><img src="b.png" alt="ok"></body></html>'
    result = run(html=html)
    assert any("alt" in i.lower() for i in result["issues"])
    assert result["meta"]["images_without_alt"] == 1
    assert result["meta"]["images_total"] == 2


def test_missing_viewport():
    from seo_optimizer_pack.tool import run

    result = run(html=MINIMAL_HTML)
    assert any("viewport" in i.lower() for i in result["issues"])


def test_missing_canonical():
    from seo_optimizer_pack.tool import run

    result = run(html=MINIMAL_HTML)
    assert any("canonical" in i.lower() for i in result["issues"])


def test_thin_content():
    from seo_optimizer_pack.tool import run

    result = run(html=MINIMAL_HTML)
    assert any("thin content" in i.lower() for i in result["issues"])
    assert result["meta"]["word_count"] < 300


def test_keyword_not_in_title():
    from seo_optimizer_pack.tool import run

    html = '<html><head><title>A Very Long Title About Something Else</title></head><body><h1>Heading</h1><p>Body text.</p></body></html>'
    result = run(html=html, keyword="python")
    assert any("keyword" in i.lower() and "title" in i.lower() for i in result["issues"])


def test_no_input():
    from seo_optimizer_pack.tool import run

    result = run()
    assert result["score"] == 0
    assert "No HTML or URL provided" in result["issues"]


def test_score_clamped():
    from seo_optimizer_pack.tool import run

    result = run(html=MINIMAL_HTML)
    assert 0 <= result["score"] <= 100


def test_meta_keys():
    from seo_optimizer_pack.tool import run

    result = run(html=GOOD_HTML)
    meta = result["meta"]
    assert "title" in meta
    assert "meta_description" in meta
    assert "headings" in meta
    assert "word_count" in meta
    assert "internal_links" in meta
    assert "external_links" in meta
