"""One answer to "what is in this figure, and how well do we know it?"

`figmint status` asks whether components have changed; `figmint check` asks
whether they are identified well enough to publish. Both questions are about the
same list of components, and until now both were answered by code living inside
the CLI — which meant anything else that wanted the answer (the editor, a MyST
build, a CI annotation) had to either shell out and scrape text or reimplement
the assessment and slowly drift away from it.

This module is that answer as data. The CLI formats it as text, the MyST plugin
renders it into a document, and neither owns the logic.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import calkit as calkit_mod
from . import pipelines as pipelines_mod
from . import credentials as credentials_mod
from . import provenance as provenance_mod
from .provenance import Level, Policy
from .status import DocumentReport, SourceState, check_any


@dataclass
class ComponentReport:
    """Everything known about one placed component."""

    key: str
    #: Declared origin path, or None for anonymous embedded content.
    origin: str | None
    #: What to show a human: the origin, or a stand-in when there is none.
    label: str
    level: Level
    reason: str
    #: Freshness of the embedded copy against the file on disk.
    state: SourceState = SourceState.UNKNOWN
    detail: str = ""
    #: Whether the project's policy accepts this component.
    permitted: bool = True
    #: Concrete next step when it does not, else None.
    fix: str | None = None
    resolved: Path | None = None
    #: Name of the Calkit stage that declares this file as an output, if any.
    stage: str | None = None
    #: Whether that stage is up to date. None when there is no stage, or no
    #: lock file to compare against.
    stage_current: bool | None = None
    #: Dependencies of the stage that changed since it last ran.
    stage_changed: tuple[str, ...] = ()
    #: Which pipeline declared the stage — "Calkit", "Snakemake", …
    stage_pipeline: str | None = None
    #: Files this component is derived from that nothing accounts for — no
    #: producing stage, no `imported_from`, no sidecar, no credentials.
    unaccounted_inputs: tuple[str, ...] = ()
    credentials: dict[str, Any] | None = None

    @property
    def upstream_stale(self) -> bool:
        """The stage that made this file needs re-running.

        Distinct from `stale`, which is about the copy embedded in the figure.
        Here the embedded copy and the file on disk can agree perfectly and both
        still be out of date with respect to the code that produced them.
        """
        return self.stage_current is False

    @property
    def missing_origin(self) -> bool:
        """A declared origin whose file is gone.

        Worth separating from a low level: there *is* a claim, but nothing can
        check it and the embedded copy can never be re-derived. The figure still
        renders, which is why it has to be said out loud.
        """
        return bool(self.origin) and (
            self.resolved is None or not self.resolved.is_file()
        )

    @property
    def stale(self) -> bool:
        return self.state in (SourceState.STALE, SourceState.MISSING)

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "origin": self.origin,
            "label": self.label,
            "level": self.level.slug,
            "levelLabel": provenance_mod.LEVEL_LABEL[self.level],
            "reason": self.reason,
            "state": self.state.value,
            "detail": self.detail,
            "upstreamStale": self.upstream_stale,
            "stageChanged": list(self.stage_changed),
            "unaccountedInputs": list(self.unaccounted_inputs),
            "permitted": self.permitted,
            "fix": self.fix,
            "stage": self.stage,
            "machineGenerated": bool((self.credentials or {}).get("machineGenerated")),
        }


@dataclass
class FigureReport:
    """A whole figure: its components, its policy, and whether it is honest."""

    document: Path
    policy: Policy
    components: list[ComponentReport] = field(default_factory=list)
    #: Built artifacts older than their inputs.
    outdated_outputs: list[Path] = field(default_factory=list)
    #: The stage that produces this document, when the pipeline declares one.
    document_stage: str | None = None
    #: Whether that stage and everything upstream of it are current.
    document_stage_current: bool | None = None
    #: What changed, and in which stage.
    document_stage_changed: tuple[str, ...] = ()
    document_stage_blamed: str | None = None
    #: True when the subject is a single artifact rather than a composite. The
    #: questions differ: a lone PNG has no embedded copy that could drift from
    #: its source, because it *is* the source.
    standalone: bool = False
    error: str | None = None

    @property
    def behind_source(self) -> bool:
        """The published figure is older than the diagram it was rendered from.

        A separate question from any component being stale. Editing the authored
        `.drawio` changes nothing about the components — they are embedded, and
        they still match their files — but the picture on the page is now a
        rendering of a diagram that no longer exists. Nothing else in this report
        can see that, because everything else looks *inside* the figure.
        """
        return self.document_stage_current is False

    @property
    def stale(self) -> bool:
        return (
            any(c.stale for c in self.components)
            or bool(self.outdated_outputs)
            or self.behind_source
        )

    @property
    def upstream_stale(self) -> list[ComponentReport]:
        """Components whose producing stage needs re-running."""
        return [c for c in self.components if c.upstream_stale]

    @property
    def unaccounted_inputs(self) -> tuple[str, ...]:
        """Every unexplained file the figure ultimately rests on.

        Deliberately not a policy violation. The components themselves are
        identified; what is missing is one link further back, and failing a
        build over it would punish the projects that adopted a pipeline at all.
        It is reported loudly and left as a warning.
        """
        seen: list[str] = []
        for component in self.components:
            for path in component.unaccounted_inputs:
                if path not in seen:
                    seen.append(path)
        return tuple(seen)

    @property
    def violations(self) -> list[ComponentReport]:
        return [c for c in self.components if not c.permitted or c.missing_origin]

    @property
    def publishable(self) -> bool:
        """Whether the figure clears the policy, honouring `enforce`."""
        return not self.violations or not self.policy.enforce

    @property
    def weakest(self) -> Level:
        """A figure is only as accountable as its least accountable panel."""
        if not self.components:
            return Level.UNIDENTIFIED
        return min(c.level for c in self.components)

    def to_dict(self) -> dict[str, Any]:
        return {
            "document": str(self.document),
            "policy": self.policy.to_dict(),
            "components": [c.to_dict() for c in self.components],
            "stale": self.stale,
            "upstreamStale": [c.key for c in self.upstream_stale],
            "behindSource": self.behind_source,
            "documentStage": self.document_stage,
            "documentStageChanged": list(self.document_stage_changed),
            "unaccountedInputs": list(self.unaccounted_inputs),
            "publishable": self.publishable,
            "standalone": self.standalone,
            "weakest": self.weakest.slug,
            "error": self.error,
        }


def inspect_asset(path: Path, *, root: Path | None = None) -> FigureReport:
    """Assess one artifact that is not a composite.

    Not every figure is a document figmint can open. A plot written straight to
    PNG by a pipeline, or an image someone was sent, still has the question
    "where did this come from, and is it identified well enough to publish?" —
    and figmint already answers it for directory scans. This makes the same
    answer reachable per-file, so a document can show provenance for a figure
    that was never composed in figmint at all.
    """
    from .assets import describe

    # `describe` reports paths relative to the root, so the subject has to be
    # absolute or the two disagree.
    path = Path(path).resolve()
    project_root = (
        Path(root).resolve()
        if root
        else (pipelines_mod.find_project(path.parent) or path.parent).resolve()
    )
    try:
        policy = provenance_mod.load_policy(project_root)
    except ValueError as exc:
        return FigureReport(
            document=path, policy=provenance_mod.DEFAULT_POLICY, error=str(exc)
        )

    if not path.is_file():
        return FigureReport(
            document=path, policy=policy, standalone=True, error=f"no such file: {path}"
        )

    project = pipelines_mod.project_for(project_root)
    asset = describe(path, project_root, project)
    level = Level.parse(str((asset.assessment or {}).get("level", "unidentified")))
    stage = project.stage_for(path) if project else None

    component = ComponentReport(
        key=path.name,
        origin=asset.path,
        label=asset.path,
        level=level,
        reason=str((asset.assessment or {}).get("reason", "")),
        # There is no embedded copy to compare against: the artifact is the
        # file. Reporting it as "matches source" would be a category error, so
        # the renderer drops that column for a standalone subject.
        state=SourceState.OK,
        permitted=policy.permits(level),
        resolved=path,
        stage=stage.name if stage is not None else None,
        stage_current=getattr(stage, "current", None) if stage is not None else None,
        stage_changed=getattr(stage, "changed_deps", ()) if stage is not None else (),
        stage_pipeline=getattr(stage, "pipeline", None) if stage is not None else None,
        unaccounted_inputs=(
            _unaccounted_inputs(stage, project, project_root) if stage is not None else ()
        ),
        credentials=asset.credentials,
    )
    if not component.permitted:
        component.fix = provenance_mod.explain_fix(level, asset.path, policy.require)

    return FigureReport(
        document=path, policy=policy, components=[component], standalone=True
    )


def inspect_any(path: Path, *, root: Path | None = None) -> FigureReport:
    """Report on whatever this is — a composite, or a single artifact."""
    from . import formats

    path = Path(path)
    if formats.is_document(path):
        return inspect_document(path, root=root)
    return inspect_asset(path, root=root)


def _project_root(document, fallback: Path) -> Path:
    """Where `figmint.toml` and `calkit.yaml` are looked up for this document."""
    from . import formats

    if isinstance(document, formats.DrawioDocument):
        return document.project_root
    return fallback.resolve()


def inspect_component(
    component,
    *,
    root: Path,
    policy: Policy,
    project: Any,
    document,
    freshness: dict[str, Any] | None = None,
) -> ComponentReport:
    """Assess one component: where it came from, and whether it still matches."""
    from .assets import merge_provenance

    resolved = component.resolved
    on_disk = resolved is not None and resolved.is_file()

    stage = project.stage_for(resolved) if project and resolved else None
    creds = credentials_mod.read(resolved) if on_disk else None

    # Provenance comes from the *file*, not from the fact that we managed to
    # locate it. Recovering an origin by content hash says which file this is;
    # it says nothing about where that file came from. Treating the recovered
    # path as a declared origin let an AI-generated image with no sidecar and no
    # credentials pass the policy, which is precisely the case this exists to
    # catch.
    recorded = dict(component.provenance)
    if on_disk:
        recorded = {**merge_provenance(resolved, root, creds), **recorded}

    creds_dict = creds.to_dict() if creds else component.credentials
    assessment = provenance_mod.assess(recorded, creds_dict, stage)

    report = ComponentReport(
        key=component.key,
        origin=component.origin or None,
        label=component.origin or "(anonymous embedded content)",
        level=assessment.level,
        reason=assessment.reason,
        permitted=policy.permits(assessment.level),
        resolved=resolved,
        stage=stage.name if stage is not None else None,
        stage_current=getattr(stage, "current", None) if stage is not None else None,
        stage_changed=getattr(stage, "changed_deps", ()) if stage is not None else (),
        stage_pipeline=getattr(stage, "pipeline", None) if stage is not None else None,
        unaccounted_inputs=(
            _unaccounted_inputs(stage, project, root) if stage is not None else ()
        ),
        credentials=creds_dict,
    )

    if freshness is not None:
        source = freshness.get(component.key)
        if source is not None:
            report.state = source.state
            report.detail = source.detail

    if report.missing_origin:
        report.permitted = False
        report.fix = (
            "the declared origin no longer exists; the embedded copy cannot be "
            "re-derived or updated"
        )
    elif not report.permitted:
        report.fix = _fix_for(component, document, assessment.level, policy)

    return report


def _unaccounted_inputs(stage, project, root: Path) -> tuple[str, ...]:
    """Roots of this component's input chain that nothing accounts for.

    Calkit answers the first half — which inputs no stage produces and no
    `imported_from` explains. figmint then drops any that it can account for on
    its own evidence, because a `.prov.yaml` sidecar or a C2PA manifest is a
    stated origin even when `calkit.yaml` says nothing. What survives is a file
    that genuinely appeared from nowhere.
    """
    from .assets import merge_provenance

    candidates = project.unaccounted_inputs(stage.name)
    remaining: list[str] = []
    for relative in candidates:
        path = root / relative
        creds = credentials_mod.read(path) if path.is_file() else None
        provenance = merge_provenance(path, root, creds) if path.is_file() else {}
        level = provenance_mod.assess(
            provenance, creds.to_dict() if creds else None, None
        ).level
        if level is Level.UNIDENTIFIED:
            remaining.append(relative)
    return tuple(remaining)


def _fix_for(component, document, level: Level, policy: Policy) -> str:
    if getattr(document, "rendered", False):
        # A rendered .drawio.svg cannot be written to, so pointing at `adopt`
        # here would send the user into a refusal.
        return "declare it in the .drawio source, then re-export the .drawio.svg"
    if document.embeds_components and not component.origin:
        return f"figmint adopt {document.path}"
    return provenance_mod.explain_fix(level, component.origin or "<file>", policy.require)


def inspect_document(path: Path, *, root: Path | None = None) -> FigureReport:
    """Full report for one figure document.

    Combines the freshness question (`status`) and the identification question
    (`check`) into a single pass, because every caller that wants one usually
    wants the other.
    """
    from . import formats

    path = Path(path)
    try:
        document = formats.open_document(path)
    except (formats.UnsupportedFormat, formats.base.DocumentError) as exc:
        return FigureReport(
            document=path, policy=provenance_mod.DEFAULT_POLICY, error=str(exc)
        )

    project_root = _project_root(document, root or path.parent)
    try:
        policy = provenance_mod.load_policy(project_root)
    except ValueError as exc:
        return FigureReport(
            document=path, policy=provenance_mod.DEFAULT_POLICY, error=str(exc)
        )

    staleness: DocumentReport = check_any(document)
    freshness = {s.key: s for s in staleness.sources}
    project = pipelines_mod.project_for(project_root)

    report = FigureReport(
        document=path, policy=policy, outdated_outputs=list(staleness.outdated_outputs)
    )

    # Is the document itself behind whatever renders it? Asked of the document,
    # not of its components, because that is where the answer lives.
    document_stage = project.stage_for(path.resolve()) if project else None
    if document_stage is not None:
        report.document_stage = document_stage.name
        report.document_stage_current = document_stage.current
        report.document_stage_changed = document_stage.changed_deps
        report.document_stage_blamed = document_stage.stale_stage
    for component in document.components():
        report.components.append(
            inspect_component(
                component,
                root=project_root,
                policy=policy,
                project=project,
                document=document,
                freshness=freshness,
            )
        )
    return report
