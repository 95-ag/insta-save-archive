from insta_save.stages import deterministic as det


def test_slugify_collection_kebabs_and_strips():
    assert det.slugify_collection("Plants & Pets") == "plants-pets"
    assert det.slugify_collection("Clothing hacks") == "clothing-hacks"
    assert det.slugify_collection("BLR") == "blr"
    assert det.slugify_collection("boi saves") == "boi-saves"
    assert det.slugify_collection("  Interesting   buys!! ") == "interesting-buys"


def test_deterministic_tags_union_sorted_deduped():
    assert det.deterministic_tags(["Makeup", "Hair hacks", "Makeup"]) == ["hair-hacks", "makeup"]
    assert det.deterministic_tags([]) == []
    # a name that slugifies to empty is dropped
    assert det.deterministic_tags(["!!!", "Travel"]) == ["travel"]
