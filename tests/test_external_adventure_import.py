"""Real format-shaped fixtures through the common side-effect-free review boundary."""
import io
import json
import zipfile

import pytest

from src.adventures.external_import import parse_import
from tests.test_module_install_hardening import _make_api

RUNTIME = {"id": "core:dnd2024", "minimum_version": 1}
FOUNDRY = {"_id": "FoundryQuest", "name": "The gate", "_stats": {"coreVersion": "14.370"}, "journal": [{"_id": "JournalGate", "name": "Gate", "pages": [{"_id": "PageOne", "type": "text", "text": {"content": "<p>Enter the <strong>gate</strong>.</p><script>alert('secret')</script>"}}]}], "actors": [{"_id": "ActorGuard", "name": "Guard", "system": {"details": {"biography": {"value": "<p>A keeper.</p>"}}, "attributes": {"hp": {"value": 12}}}}], "items": [{"_id": "ItemKey", "name": "Key", "system": {"description": {"value": "<p>Brass.</p>"}}}], "scenes": [{"_id": "SceneGate", "name": "Gate map", "background": {"src": "maps/gate.png"}}], "macros": [{"command": "throw new Error()"}]}
FG_XML = b'<root version="4.5"><encounter><category name="Chapter"><id-00001><name type="string">Gate</name><text type="formattedtext"><p>Enter the gate.</p></text></id-00001></category></encounter><npc><id-00001><name type="string">Guard</name><text type="formattedtext"><p>A keeper.</p></text><hp type="number">12</hp></id-00001></npc><item><id-00001><name type="string">Key</name><description type="formattedtext"><p>Brass.</p></description></id-00001></item></root>'


def fg_package():
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr('definition.xml', '<root><name>Gate module</name><ruleset>5E</ruleset></root>')
        archive.writestr('db.xml', FG_XML)
    return output.getvalue()


def pdf_source(*, blank=False):
    from pypdf import PdfWriter
    from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject
    writer = PdfWriter()
    page = writer.add_blank_page(width=500, height=700)
    if not blank:
        font = DictionaryObject({NameObject('/Type'): NameObject('/Font'), NameObject('/Subtype'): NameObject('/Type1'), NameObject('/BaseFont'): NameObject('/Helvetica')})
        page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'): DictionaryObject({NameObject('/F1'): writer._add_object(font)})})
        stream = DecodedStreamObject()
        stream.set_data(b'BT /F1 12 Tf 40 600 Td (Enter the gate.) Tj ET')
        page[NameObject('/Contents')] = writer._add_object(stream)
    output = io.BytesIO(); writer.write(output)
    return output.getvalue()


def test_pdf_text_layer_is_reviewed_and_scanned_or_invalid_documents_are_rejected():
    draft = parse_import(pdf_source(), 'gate.pdf', RUNTIME, 'en')
    assert draft['provenance']['format'] == 'pdf-text'
    assert draft['files']['adventure.json']['nodes'][0]['description'] == 'Enter the gate.'
    assert 'pdfReviewLayout' in draft['warnings']
    for source in (pdf_source(blank=True), b'not a pdf'):
        with pytest.raises(ValueError):
            parse_import(source, 'gate.pdf', RUNTIME, 'en')


def test_pdf_worker_timeout_is_a_failed_preview_not_a_background_parse(monkeypatch):
    import subprocess
    from src.adventures.importers import pdf_text
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired('pdf-worker', 8)
    monkeypatch.setattr(pdf_text.subprocess, 'run', timeout)
    with pytest.raises(ValueError, match='time limit'):
        parse_import(b'%PDF', 'gate.pdf', RUNTIME, 'en')


@pytest.mark.parametrize('payload,filename,format_name', [(json.dumps(FOUNDRY).encode(), 'quest.json', 'foundry-adventure'), (FG_XML, 'db.xml', 'fantasy-grounds'), (fg_package(), 'quest.mod', 'fantasy-grounds')])
def test_structured_formats_preserve_prose_resources_provenance_without_executing_or_guessing(payload, filename, format_name):
    draft = parse_import(payload, filename, RUNTIME, 'en')
    assert draft == parse_import(payload, filename, RUNTIME, 'en')
    assert draft['provenance']['format'] == format_name
    graph = draft['files']['adventure.json']
    assert 'Enter the gate.' in str(graph)
    assert all(node['visibility'] == 'gm' for node in graph['nodes'])
    assert not draft['references']
    assert any(value.get('kind') == 'npc' for value in draft['files'].values())
    assert '12' in str(draft)  # Foreign stats remain source evidence, never inferred combat authority.
    assert all('stat_block' not in value for value in draft['files'].values())
    assert "alert(" not in str(graph) and 'throw new Error' not in str(graph)


@pytest.mark.parametrize('payload,filename', [(b'[]', 'quest.json'), (b'{}', 'quest.json'), (b'<!DOCTYPE root [<!ENTITY x "leak">]><root>&x;</root>', 'db.xml'), (b'<broken>', 'db.xml')])
def test_ambiguous_or_unsafe_foreign_documents_fail_closed(payload, filename):
    with pytest.raises(ValueError):
        parse_import(payload, filename, RUNTIME, 'en')


def test_foundry_scene_rows_become_portable_scene_definitions():
    draft = parse_import(json.dumps(FOUNDRY).encode(), 'quest.json', RUNTIME, 'en')

    scene_paths = [path for path in draft['files'] if path.startswith('content/scene/')]
    assert len(scene_paths) == 1
    entity = draft['files'][scene_paths[0]]
    assert entity['kind'] == 'scene' and entity['spec'] == 'tactical-scene-v1'
    assert entity['source_ref'] == draft['files']['adventure.json']['source_ref']
    document = entity['tactical_scene']
    assert document['kind'] == 'scene' and document['display_name'] == 'Gate map'
    assert document['canonical_id'].startswith('fvtt.')
    assert document['background']['source_path'] == 'maps/gate.png'
    # Display placeables are exactly what the adapter converted; nothing is guessed.
    assert document['placeables']['tokens'] == {}
    assert document['placeables']['walls'] == {}

    node = next(n for n in draft['files']['adventure.json']['nodes'] if n['name'] == 'Gate map')
    assert node['type'] == 'reference'
    assert node['scene_ref'] == f"scene:{entity['id']}"
    # The raw Foundry JSON no longer stands in for the scene definition.
    assert 'maps/gate.png' not in node['description']


def test_foundry_scene_without_a_declared_version_stays_reference_evidence(tmp_path):
    payload = dict(FOUNDRY)
    payload.pop('_stats')
    draft = parse_import(json.dumps(payload).encode(), 'quest.json', RUNTIME, 'en')

    assert not [path for path in draft['files'] if path.startswith('content/scene/')]
    assert 'externalImportSceneUnconverted' in draft['warnings']
    node = next(n for n in draft['files']['adventure.json']['nodes'] if n['name'] == 'Gate map')
    assert 'scene_ref' not in node
    assert 'SceneGate' in node['description']  # inert raw evidence, never a guessed schema

    api, _host, store = _make_api(tmp_path / 'data')
    try:
        checked = api.preview_adventure_script(json.dumps(payload).encode(), 'quest.json', 'en')
        assert not checked['blockers'], checked
        assert 'externalImportSceneUnconverted' in checked['warnings']
    finally:
        store.close()


def test_foundry_malformed_scene_row_is_kept_as_reference_not_guessed():
    payload = dict(FOUNDRY)
    payload["scenes"] = [{
        "_id": "SceneBroken001",
        "name": "Broken map",
        "_stats": {"coreVersion": "14.370"},
        "grid": {"type": 1, "size": "huge"},
    }]
    draft = parse_import(json.dumps(payload).encode(), 'quest.json', RUNTIME, 'en')

    assert not [path for path in draft['files'] if path.startswith('content/scene/')]
    assert 'externalImportSceneUnconverted' in draft['warnings']
    node = next(n for n in draft['files']['adventure.json']['nodes'] if n['name'] == 'Broken map')
    assert 'scene_ref' not in node and 'SceneBroken001' in node['description']


@pytest.mark.parametrize('payload,filename', [(json.dumps(FOUNDRY).encode(), 'quest.json'), (fg_package(), 'quest.mod'), (pdf_source(), 'quest.pdf')])
def test_foreign_import_reviews_edits_and_publishes_through_native_owner(tmp_path, payload, filename):
    api, _host, store = _make_api(tmp_path / 'data')
    try:
        checked = api.preview_adventure_script(payload, filename, 'en')
        assert checked['ok'] and not checked['blockers'], checked
        draft = checked['draft']
        draft['files']['adventure.json']['nodes'][0]['description'] = 'Reviewed scene prose'
        with pytest.raises(ValueError, match='stale'):
            api.commit_adventure_script({**draft, 'reviewed': True, 'plan_digest': checked['plan_digest']})
        checked = api.review_adventure_script(draft)
        assert not checked['blockers'], checked
        result = api.commit_adventure_script({**draft, 'reviewed': True, 'plan_digest': checked['plan_digest']})
        assert result['ok']
        assert api.adventure_detail(result['adventure_id'])['adventure']['files']['adventure.json']['nodes'][0]['description'] == 'Reviewed scene prose'
    finally:
        store.close()


def test_imported_local_resource_references_cannot_be_deleted_or_reinterpreted_by_later_edits(tmp_path):
    api, _host, store = _make_api(tmp_path / 'data')
    try:
        checked = api.preview_adventure_script(json.dumps(FOUNDRY).encode(), 'quest.json', 'en')
        draft = checked['draft']
        npc = next(value for value in draft['files'].values() if value.get('kind') == 'npc')
        draft['files']['adventure.json']['nodes'][0]['npc_refs'] = [f"npc:{npc['id']}"]
        checked = api.review_adventure_script(draft)
        repeated = api.review_adventure_script(checked['draft'])
        assert repeated['plan_digest'] == checked['plan_digest']
        result = api.commit_adventure_script({**draft, 'reviewed': True, 'plan_digest': checked['plan_digest']})
        files = api.adventure_detail(result['adventure_id'])['adventure']['files']
        path = next(path for path, value in files.items() if value.get('kind') == 'npc')
        del files[path]
        with pytest.raises(ValueError, match='unresolved'):
            api.update_adventure(result['adventure_id'], {'files': files})
        assert path in api.adventure_detail(result['adventure_id'])['adventure']['files']
        files['manifest.json'].pop('reference_policy', None)
        with pytest.raises(ValueError, match='reference policy'):
            api.update_adventure(result['adventure_id'], {'files': files})
    finally:
        store.close()


@pytest.mark.asyncio
async def test_gm_source_documents_are_not_returned_to_a_player_share_query(tmp_path):
    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer
    from src.webui.routes.adventures import register_adventures
    api, _host, store = _make_api(tmp_path / 'data')
    try:
        checked = api.preview_adventure_script(json.dumps(FOUNDRY).encode(), 'quest.json', 'en')
        result = api.commit_adventure_script({**checked['draft'], 'reviewed': True, 'plan_digest': checked['plan_digest']})
        app = web.Application(); app['api'] = api; register_adventures(app)
        async with TestClient(TestServer(app)) as client:
            response = await client.get(f"/api/adventures/{result['adventure_id']}?user=player")
            assert response.status == 403
            assert 'source_record' not in await response.text()
    finally:
        store.close()
