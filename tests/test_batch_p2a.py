from pathlib import Path


def test_export_module():
    assert Path("wax/domain/export.py").exists()
    assert "export_principal_package" in Path("wax/domain/export.py").read_text()


def test_policy_module():
    from wax.security.policy import tool_allowed
    ok, reason = tool_allowed("get_current_time")
    assert ok


def test_identity_link():
    assert "link_identity_to_principal" in Path("wax/domain/identity.py").read_text()


def test_html_page_render():
    from wax.artifacts.html_page import render_branded_html
    html = render_branded_html(title="T", body="Hello <script>")
    assert "<script>" not in html or "&lt;script&gt;" in html
    assert "Hello" in html


def test_tools_registered():
    src = Path("wax/tools/registry.py").read_text()
    for n in ("export_learner_data", "link_channel_identity", "create_html_page"):
        assert n in src
