"""Tests for the filesystem watcher."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from watchfiles import Change

from figmint.watch import Watcher, classify, is_relevant

ROOT = Path("/project")


class TestRelevance:
    @pytest.mark.parametrize(
        "name",
        ["plot.svg", "photo.PNG", "scan.jpeg", "fig.pdf", "study.fig.yaml"],
    )
    def test_figures_and_documents_are_relevant(self, name: str):
        assert is_relevant(ROOT / "figures" / name)

    @pytest.mark.parametrize(
        "name", ["plot.svg.prov.yaml", "plot.svg.c2pa"],
    )
    def test_provenance_sidecars_are_relevant(self, name: str):
        # A change to provenance is a change to the component's meaning.
        assert is_relevant(ROOT / "figures" / name)

    @pytest.mark.parametrize("name", ["notes.txt", "script.py", "data.csv"])
    def test_unrelated_files_are_ignored(self, name: str):
        assert not is_relevant(ROOT / name)

    def test_our_own_temporary_writes_are_ignored(self):
        # Saving a document writes `.tmp` then renames. Reacting to the `.tmp`
        # would tell every client the document changed the moment they saved it.
        assert not is_relevant(ROOT / "study.fig.yaml.tmp")

    @pytest.mark.parametrize("directory", [".git", "node_modules", "__pycache__"])
    def test_noise_directories_are_skipped(self, directory: str):
        assert not is_relevant(ROOT / directory / "thing.svg")


class TestClassify:
    def test_splits_assets_from_documents(self):
        changes = classify(
            ROOT,
            {
                (Change.modified, "/project/figures/a.svg"),
                (Change.added, "/project/study.fig.yaml"),
            },
        )
        assert changes.assets == ["figures/a.svg"]
        assert changes.documents == ["study.fig.yaml"]

    def test_paths_are_relative_to_the_root(self):
        changes = classify(ROOT, {(Change.modified, "/project/deep/dir/a.svg")})
        assert changes.assets == ["deep/dir/a.svg"]

    def test_paths_outside_the_root_are_dropped(self):
        changes = classify(ROOT, {(Change.modified, "/elsewhere/a.svg")})
        assert changes.empty

    def test_irrelevant_changes_produce_an_empty_set(self):
        changes = classify(ROOT, {(Change.modified, "/project/notes.txt")})
        assert changes.empty

    def test_duplicate_events_collapse(self):
        changes = classify(
            ROOT,
            {
                (Change.added, "/project/figures/a.svg"),
                (Change.modified, "/project/figures/a.svg"),
            },
        )
        assert changes.assets == ["figures/a.svg"]

    def test_deletions_are_reported_like_any_other_change(self):
        # The client re-scans and finds the file gone, which is what should
        # surface as `missing` rather than being silently ignored here.
        changes = classify(ROOT, {(Change.deleted, "/project/figures/a.svg")})
        assert changes.assets == ["figures/a.svg"]


class TestWatcher:
    @pytest.mark.asyncio
    async def test_delivers_changes_to_a_subscriber(self, tmp_path: Path):
        watcher = Watcher(tmp_path.resolve())
        async with watcher.subscribe() as queue:
            await asyncio.sleep(0.3)  # let the watch establish
            (tmp_path / "plot.svg").write_text("<svg/>")
            changes = await asyncio.wait_for(queue.get(), timeout=8)
        assert "plot.svg" in changes.assets

    @pytest.mark.asyncio
    async def test_one_watch_serves_several_subscribers(self, tmp_path: Path):
        watcher = Watcher(tmp_path.resolve())
        async with watcher.subscribe() as first, watcher.subscribe() as second:
            await asyncio.sleep(0.3)
            (tmp_path / "plot.svg").write_text("<svg/>")
            a = await asyncio.wait_for(first.get(), timeout=8)
            b = await asyncio.wait_for(second.get(), timeout=8)
        assert a.assets == b.assets == ["plot.svg"]

    @pytest.mark.asyncio
    async def test_watch_stops_when_the_last_subscriber_leaves(self, tmp_path: Path):
        watcher = Watcher(tmp_path.resolve())
        async with watcher.subscribe():
            await asyncio.sleep(0.2)
        assert watcher._task is None

    @pytest.mark.asyncio
    async def test_irrelevant_writes_do_not_wake_subscribers(self, tmp_path: Path):
        watcher = Watcher(tmp_path.resolve())
        async with watcher.subscribe() as queue:
            await asyncio.sleep(0.3)
            (tmp_path / "notes.txt").write_text("hello")
            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(queue.get(), timeout=1.5)
        assert queue.empty()
