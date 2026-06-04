"""
Single source of truth for all Instagram collection metadata.

Groups define ingestion priority order. New collections should be assigned
to a group before being added here — the pipeline will flag unknown collections.

extract=True marks a collection for inclusion in the Phase 3 pilot set:
ingest + deep extraction + enrichment.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class CollectionEntry:
    name: str        # Exact display name matching Instagram's collection label
    slug: str        # URL slug from /saved/{slug}/{numeric_id}/
    numeric_id: str  # Instagram numeric collection ID
    group: str       # Group name (must be in GROUP_PRIORITY)
    extract: bool    # True = include in Phase 3 pilot extraction


# Ingestion runs in this group order — edit to reprioritise.
GROUP_PRIORITY = [
    "Hustling",
    "Content",
    "Creative",
    "Biz",
    "Biz - Clothing",
    "Lifestyle",
]

# All 43 collections. Order within each group = ingestion order within that group.
# Job Hunt is listed first (group Hustling) — ingest_batch dedup skips its 36 existing rows.
COLLECTIONS = [
    # --- Hustling ---
    CollectionEntry("Job Hunt",              "job-hunt",                   "17914282538490056", "Hustling",      True),
    CollectionEntry("Coding - AI",           "coding-ai",                  "18073491293555023", "Hustling",      True),
    CollectionEntry("Coding - Web Design",   "coding-web-design",          "18020699612505985", "Hustling",      True),
    CollectionEntry("Website Handling",      "website-handling",           "17938227134843727", "Hustling",      True),
    CollectionEntry("Branding & Logo",       "branding-logo",              "18293813869211953", "Hustling",      True),
    CollectionEntry("Inspo - Website",       "inspo-website",              "18022299515745047", "Hustling",      True),
    # --- Content ---
    CollectionEntry("Digital Content Creation",      "digital-content-creation",      "17871981612011838", "Content", True),
    CollectionEntry("Tips - Content Creation",       "tips-content-creation",         "17958125645841081", "Content", True),
    CollectionEntry("Inspo - Reel/Post/Story Ideas", "inspo-reelpoststory-ideas",     "17904942294373794", "Content", False),
    CollectionEntry("Inspo - Quotes/Captions/Audio", "inspo-quotescaptionsaudio",     "18379395337092798", "Content", True),
    CollectionEntry("Inspo - Video Film/Editing",    "inspo-video-filmediting",       "18074119415279188", "Content", False),
    CollectionEntry("Photography/Filmography",       "photographyfilmography",        "17972854166531211", "Content", False),
    # --- Creative ---
    CollectionEntry("Inspo - Art",           "inspo-art",                  "18034595918650810", "Creative",      False),
    CollectionEntry("Inspo - Digital Art",   "inspo-digital-art",          "18034448639694505", "Creative",      False),
    CollectionEntry("Inspo - Crafts",        "inspo-crafts",               "18104226085599785", "Creative",      False),
    CollectionEntry("Arts & craft",          "arts-craft",                 "18027016366415822", "Creative",      False),
    CollectionEntry("Canva Hacks",           "canva-hacks",                "18386422039109023", "Creative",      False),
    # --- Biz ---
    CollectionEntry("Hustle Ideas",          "hustle-ideas",               "18044737310018186", "Biz",           True),
    CollectionEntry("Side Hustle Help",      "side-hustle-help",           "18058741096960025", "Biz",           True),
    CollectionEntry("Inspo - BoI Biz",       "inspo-boi-biz",              "18086926003888619", "Biz",           True),
    CollectionEntry("Patches Collab",        "patches-collab",             "18039253946462978", "Biz",           False),
    CollectionEntry("Info for patch",        "info-for-patch",             "17934581390961502", "Biz",           False),
    CollectionEntry("Photo booth",           "photo-booth",                "18479102938042438", "Biz",           False),
    CollectionEntry("3D printing",           "3d-printing",                "17882859060127866", "Biz",           False),
    # --- Biz - Clothing ---
    CollectionEntry("Clothing - Tutorials/Making", "clothing-tutorialsmaking", "17911931076271954", "Biz - Clothing", False),
    CollectionEntry("Clothing - Brands/Ideas",     "clothing-brandsideas",     "18543261070039422", "Biz - Clothing", False),
    CollectionEntry("Clothing - lino prints",      "clothing-lino-prints",     "18107788597725926", "Biz - Clothing", False),
    CollectionEntry("Clothing - Accessories",      "clothing-accessories",     "18527170552064005", "Biz - Clothing", False),
    CollectionEntry("Clothing - Suppliers",        "clothing-suppliers",       "18341283460237116", "Biz - Clothing", False),
    # --- Lifestyle ---
    CollectionEntry("Foodie",                "foodie",                     "17845476017808776", "Lifestyle",     True),
    CollectionEntry("Fitness",               "fitness",                    "17969245787197097", "Lifestyle",     True),
    CollectionEntry("Quotes",                "quotes",                     "17848209875713958", "Lifestyle",     True),
    CollectionEntry("Clothing hacks",        "clothing-hacks",             "17961587377540084", "Lifestyle",     False),
    CollectionEntry("BLR",                   "blr",                        "17997563705730185", "Lifestyle",     False),
    CollectionEntry("Hair hacks",            "hair-hacks",                 "17942422732746916", "Lifestyle",     False),
    CollectionEntry("Makeup",                "makeup",                     "17854195137135151", "Lifestyle",     False),
    CollectionEntry("Home ideas",            "home-ideas",                 "18438150796052393", "Lifestyle",     False),
    CollectionEntry("Plants & Pets",         "plants-pets",                "17872979555999747", "Lifestyle",     False),
    CollectionEntry("Interesting buys",      "interesting-buys",           "18044747635371182", "Lifestyle",     False),
    CollectionEntry("Travel",                "travel",                     "17857015184941864", "Lifestyle",     False),
    CollectionEntry("Posing",                "posing",                     "18042323627359116", "Lifestyle",     False),
    CollectionEntry("boi saves",             "boi-saves",                  "17929757250053412", "Lifestyle",     False),
    CollectionEntry("Tutorials",             "tutorials",                  "18165387484206367", "Lifestyle",     False),
]

# Validate all groups are known at import time.
_all_groups = {e.group for e in COLLECTIONS}
_unknown = _all_groups - set(GROUP_PRIORITY)
if _unknown:
    raise ValueError(f"collections_config: unknown groups: {_unknown}")


def ordered_for_ingestion() -> list[CollectionEntry]:
    """Return all collections sorted by group priority, preserving intra-group order."""
    priority = {g: i for i, g in enumerate(GROUP_PRIORITY)}
    return sorted(COLLECTIONS, key=lambda e: (priority[e.group], COLLECTIONS.index(e)))


def pilot_collections() -> list[CollectionEntry]:
    """Return collections with extract=True, in ingestion order."""
    return [e for e in ordered_for_ingestion() if e.extract]


def classify_new_collection(name: str) -> str:
    """
    Interactive prompt to assign a new collection to a group.
    Call this when list_collections.py finds a name not in COLLECTIONS.
    Returns the chosen group name.
    """
    print(f"\nNew collection detected: {name!r}")
    print("Available groups:")
    for i, g in enumerate(GROUP_PRIORITY, 1):
        print(f"  {i}. {g}")
    while True:
        raw = input("Assign to group (enter number): ").strip()
        try:
            idx = int(raw) - 1
            if 0 <= idx < len(GROUP_PRIORITY):
                return GROUP_PRIORITY[idx]
        except ValueError:
            pass
        print("  Invalid — enter a number from the list.")
