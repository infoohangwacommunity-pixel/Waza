
from wax.intelligence.tool_protocol import enforce_verified_artifacts


def test_blocks_invented_wax_run():
    out = enforce_verified_artifacts(
        "Yes, I am done with the page — the link is ready: https://wax.sunflower-42045094.wax.run/",
        tool_results=[],
        surfaces_tools_exposed=True,
    )
    assert "wax.run" not in out.lower()
    assert "http" not in out.lower() or "verified" in out.lower() or "publish" in out.lower() or "wasn't" in out.lower()


def test_allows_tool_returned_url():
    url = "https://web-production-b83b4.up.railway.app/s/abc123"
    out = enforce_verified_artifacts(
        f"Here is your page: {url}",
        tool_results=[{"name": "create_surface", "result": {"ok": True, "page_url": url}}],
        surfaces_tools_exposed=True,
    )
    assert url in out
