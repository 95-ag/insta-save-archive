# tests/backends/test_sanitize.py
from insta_save.backends.sanitize import scrub_fabricated


def test_removes_fabricated_url_keeps_tool_name():
    text = "Xiaohei skill (github.com/helloianneo/ian-xiaohei) generates art."
    src = "this hand drawn illustration skill getting love on GitHub"
    clean, removed = scrub_fabricated(text, src)
    assert "github.com/helloianneo/ian-xiaohei" not in clean
    assert "Xiaohei skill" in clean and "generates art" in clean
    assert removed == ["github.com/helloianneo/ian-xiaohei"]


def test_keeps_url_whose_host_is_in_source():
    text = "Jitter (jitter.video) is a motion tool."
    clean, removed = scrub_fabricated(text, "go to jitter.video to animate")
    assert "jitter.video" in clean and removed == []


def test_keeps_deep_path_on_in_source_host():
    # host present in source -> whole URL kept (host-level check, by design)
    text = "Create a key at openrouter.ai/settings/keys."
    clean, removed = scrub_fabricated(text, "go to openrouter.ai for a key")
    assert "openrouter.ai/settings/keys" in clean and removed == []


def test_removes_fabricated_version():
    text = "The /btw feature added in v2.1.73 recently."
    clean, removed = scrub_fabricated(text, "claude code just added /btw")
    assert "v2.1.73" not in clean and "v2.1.73" in removed


def test_ignores_two_part_model_version():
    text = "Set model google/gemini-2.5-flash."
    clean, removed = scrub_fabricated(text, "use gemini 2.5 flash")
    assert clean == text and removed == []


def test_none_and_empty_safe():
    assert scrub_fabricated(None, "x") == (None, [])
    assert scrub_fabricated("", "x") == ("", [])


def test_removes_full_vX_Y_x_version_no_stranded_suffix():
    text = "The /btw feature added in v2.1.x recently."
    clean, removed = scrub_fabricated(text, "claude code just added /btw")
    assert "v2.1.x" not in clean and ".x" not in clean   # nothing stranded
    assert "recently" in clean
    assert removed == ["v2.1.x"]


def test_keeps_vX_Y_x_version_present_in_source():
    text = "Requires v2.1.x or later."
    clean, removed = scrub_fabricated(text, "you need v2.1.x to use this")
    assert "v2.1.x" in clean and removed == []


def test_keeps_at_handle_ending_in_tld():
    text = "[Creators]\n  @alassafi.ai — creator demonstrating the feature"
    clean, removed = scrub_fabricated(text, "a post by someone")
    assert "@alassafi.ai" in clean      # handle preserved whole, no dangling '@'
    assert "@ " not in clean
    assert removed == []


def test_strips_bare_domain_but_keeps_same_token_as_handle():
    # bare domain (fabricated) stripped; identical token as an @handle kept
    text = "See github.com/foo/bar and follow @github.io for updates."
    clean, removed = scrub_fabricated(text, "no urls in source here")
    assert "github.com/foo/bar" not in clean
    assert "@github.io" in clean        # the @handle form is preserved
