"""One-call pipeline front-fold: discover -> ingest -> select, then the per-group
interactive loop (extract -> calibrate gate -> enrich -> route). first-time crawls fresh;
incremental reuses snapshots. Resumable: every stage writes Notion; re-running recomputes."""
from insta_save.helpers.observability import StageProgress
from insta_save.stages.discover import run_discover
from insta_save.stages.ingest import run_ingest
from insta_save.stages.select import run_select_stage
from insta_save.orchestrator.sequence import run_first_time, run_incremental


def run_pipeline(env, run_cfg, collections_cfg, vocab, backend, routes, *,
                 mode, dry_run=False, select_mode="inline", ig_username=None,
                 headed=False, progress_factory=None):
    """Run discover -> ingest -> select, then the per-group interactive loop.

    In dry_run mode, skips the population stages entirely and returns only the
    computed plan (no Notion writes, no crawling).

    Args:
        env:             EnvConfig with .tmp_dir, .ig_username, .notion_write_delay.
        run_cfg:         RunConfig.
        collections_cfg: CollectionsConfig (may be reloaded after discover).
        vocab:           Vocab.
        backend:         Backend module.
        routes:          Routes.
        mode:            "first-time" or "incremental".
        dry_run:         Skip population stages; return the computed plan.
        select_mode:     "inline" or "editor" — forwarded to discover for the
                         inline collection select picker.
        ig_username:     Instagram username override; falls back to env.ig_username.
        headed:          Launch a visible browser window (for re-auth flows).
        progress_factory: Optional ``(label: str) -> context manager``.

    Returns:
        Plan from the sequencer loop.
    """
    loop = run_first_time if mode == "first-time" else run_incremental

    if dry_run:
        # Skip discover/ingest/select — just compute the plan from current Notion state.
        return loop(env, run_cfg, collections_cfg, vocab, backend, routes, dry_run=True)

    username = ig_username or getattr(env, "ig_username", None) or ""

    # 1. discover: crawl IG saved, surface new collections; first-time crawls fresh.
    run_discover(
        env,
        ig_username=username,
        collections_path="config/collections.json",
        tmp_dir=env.tmp_dir,
        headed=headed,
        fresh=(mode == "first-time"),
        select_mode=select_mode,
    )
    # Reload after discover so the inline select picker's changes (group/extract
    # annotations) are picked up before ingest and the sequencer loop.
    collections_cfg = _reload_collections()

    # 2. ingest: create/retag Notion pages for every post found in the crawl snapshots.
    with StageProgress("Ingest") as progress:
        run_ingest(env, collections_cfg=collections_cfg, progress=progress)

    # 3. select: classify each Imported item -> Queued (extract path) or leave Imported
    #    (deterministic branch for extract=no collections).
    with StageProgress("Select") as progress:
        run_select_stage(env, collections_cfg, progress,
                         write_delay=env.notion_write_delay)

    # 4. per-group interactive loop: drain extract -> calibrate gate -> enrich -> route.
    return loop(env, run_cfg, collections_cfg, vocab, backend, routes,
                progress_factory=progress_factory)


def _reload_collections():
    """Load collections config fresh from disk (patchable in tests)."""
    from insta_save.config.collections import load_collections
    return load_collections("config/collections.json")
