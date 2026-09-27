
from wax.media.manifest import build_manifest_from_payload, AssetManifest

def test_audio_manifest():
    m = build_manifest_from_payload({
        "local_media_path": "/w/media/v.ogg",
        "media_probe": {"kind": "audio", "duration_sec": 10, "capability_list": ["transcribe", "inspect"]},
    })
    assert len(m.assets) == 1
    assert "transcribe" in m.assets[0].capabilities
    text = m.render_for_ci()
    assert "ATTACHED ASSETS" in text
    assert "audio" in text

def test_brief_has_asset_fields():
    from wax.intelligence.context_intel.brief import ContextBrief
    b = ContextBrief(asset_requirements=[{"asset_id": "a1"}], experience_requirements=["interactive"])
    assert b.asset_requirements
