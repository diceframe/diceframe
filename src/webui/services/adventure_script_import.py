"""Script review/commit application boundary; native Adventure owner persists it."""

from __future__ import annotations

import io
import json
import zipfile
from copy import deepcopy
from pathlib import PurePosixPath
from typing import Any

from src.adventures.bundle import AdventureBundleLoader
from src.adventures.graph_v2 import reachable_nodes, validate_graph_v2
from src.adventures.script_import import parse_script
from src.adventures.external_import import parse_import
from src.content_modules.refs import ContentDraft, build_commit_plan, commit_plan_digest, parse_content_ref
from src.webui.services import adventures
from src.webui.services.adventures import _user_adventure_root


def preview(deps: adventures.AdventureDependencies, payload: bytes, filename: str, language: str) -> dict[str, Any]:
    draft = parse_import(payload, filename, deps.default_runtime_requirement(), language)
    return review(deps, draft)


def prepare_text_adventure(deps: adventures.AdventureDependencies, text: str, directory_id: str, language: str) -> dict[str, Any]:
    """Prepare a prose fragment at its reviewed identity, including locale targets."""
    directory = adventures.validate_directory_id(directory_id)
    draft = parse_script(text.encode(), f"{directory}.md", deps.default_runtime_requirement(), language)
    old_id = draft["files"]["adventure.json"]["id"]
    draft["directory_id"] = directory
    draft["files"]["manifest.json"]["adventure_id"] = f"user:{directory}"
    draft["files"]["adventure.json"]["id"] = directory
    for reference in draft["references"]:
        if reference.get("source_kind") == "adventure" and reference.get("source_id") == old_id:
            reference["source_id"] = directory
    for value in draft["files"].values():
        target = value.get("target") if isinstance(value, dict) else None
        if isinstance(target, dict) and target.get("kind") == "adventure" and target.get("id") == old_id:
            target["id"] = directory
    return review(deps, draft)


def review(deps: adventures.AdventureDependencies, draft: dict[str, Any]) -> dict[str, Any]:
    """Recompute plan for the *edited* draft; temporary validation leaves no content behind."""
    user_root = _user_adventure_root(deps)
    files = deepcopy(draft.get('files'))
    if not isinstance(files, dict):
        raise ValueError('script draft files are required')
    for name in files:
        if not isinstance(name, str) or '\\' in name or ':' in name or any(part.startswith('.') for part in PurePosixPath(name).parts):
            raise ValueError('unsafe script draft file path')
    directory = adventures.validate_directory_id(draft.get('directory_id'))
    manifest = files.get('manifest.json')
    if not isinstance(manifest, dict):
        raise ValueError('script draft manifest is required')
    identity = adventures._validate_package_id(manifest.get('adventure_id'))
    if manifest.get('format') != 'diceframe:adventure-graph-v2':
        raise ValueError('script drafts require Adventure graph v2')
    provenance = draft.get('provenance') or {}
    if not isinstance(provenance, dict):
        raise ValueError('script provenance must be an object')
    # Source metadata remains descriptive. It never grants rule authority.
    manifest['import_source'] = deepcopy(provenance)
    refs = draft.get('references', [])
    if not isinstance(refs, list) or len(refs) > 256:
        raise ValueError('script references must be a bounded array')
    references = [parse_content_ref(ref, default_source=f'adventure:{directory}') for ref in refs]
    warnings = ['scriptDefaultGm', 'scriptMechanicsReference']
    warnings.extend(code for code in ('storyDraftGmReview', 'storyDraftNpcReferencesOmitted') if code in draft.get('warnings', []))
    if 'scriptUnknownMarker' in draft.get('warnings', []):
        warnings.append('scriptUnknownMarker')
    warnings.extend(code for code in ('externalImportReference', 'externalImportMedia', 'externalImportScriptsOmitted', 'externalImportSceneUnconverted', 'externalImportSceneScriptsOmitted', 'pdfReviewLayout') if code in draft.get('warnings', []))
    blockers: list[str] = []
    try:
        adventures._ensure_identity_available(deps, identity)
        if (user_root / directory).exists():
            raise ValueError('adventure directory already exists; choose another identity')
    except ValueError as exc:
        blockers.append(str(exc))
    available = []
    try:
        staged, temporary = adventures._validated_stage(deps, directory, files)
        try:
            bundle = AdventureBundleLoader(staged.parent).load(directory)
            graph = validate_graph_v2(bundle.adventure)
            reachable = reachable_nodes(graph, graph['start_node_ids'])
            if any(n['id'] not in reachable and not n['optional'] for n in graph['nodes']):
                warnings.append('scriptUnreachableScenes')
            for node in graph['nodes']:
                for field in ('scene_ref', 'encounter_ref'):
                    if node.get(field):
                        references.append(parse_content_ref(node[field], default_source=f'adventure:{directory}'))
                references.extend(parse_content_ref(ref, default_source=f'adventure:{directory}') for ref in node['npc_refs'])
            for kind, records in bundle.entities.items():
                available.extend(parse_content_ref(f'{kind}:{key}', default_source=f'adventure:{directory}') for key in records)
        finally:
            temporary.cleanup()
    except ValueError as exc:
        blockers.append(str(exc))
    # Portable Draft refs have explicit sources. Normalize generated bare refs
    # to that same shape before deduplication, so re-review is a fixed point.
    references = list(dict.fromkeys(parse_content_ref(ref.to_portable_dict(), default_source=f'adventure:{directory}') for ref in references))
    clean_draft = {'directory_id': directory, 'files': files, 'references': [r.to_portable_dict() for r in references],
                   'provenance': provenance, 'warnings': warnings}
    content = ContentDraft(kind='adventure', source_kind='user', source_id=directory, external_id=identity,
                           payload=clean_draft, references=tuple(dict.fromkeys(references)), provenance=clean_draft['provenance'])
    plan = build_commit_plan([content], available=available)
    if plan.unresolved_references:
        blockers.append('scriptUnresolvedReferences')
    return {'ok': True, 'draft': clean_draft, 'content_draft': content.to_portable_dict(),
            'commit_plan': plan.to_portable_dict(), 'plan_digest': commit_plan_digest(plan),
            'warnings': warnings, 'blockers': blockers}


def commit(deps: adventures.AdventureDependencies, body: dict[str, Any]) -> dict[str, Any]:
    if body.get('reviewed') is not True:
        raise ValueError('script must be reviewed before publishing')
    checked = review(deps, body)
    if checked['blockers']:
        raise ValueError('script draft has blocking issues; review it again')
    if checked['plan_digest'] != str(body.get('plan_digest') or ''):
        raise ValueError('script preview is stale; review the edited draft again')
    draft = checked['draft']
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for path, value in draft['files'].items():
            archive.writestr(f'{draft["directory_id"]}/{path}', json.dumps(value, ensure_ascii=False))
    return adventures.import_adventure(deps, output.getvalue(), draft['directory_id'])
