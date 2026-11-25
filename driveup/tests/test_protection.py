import pytest
from driveup.db import repo
from driveup.tests.test_worker import env, make_job, wait_done  # noqa: F401
from driveup.drive.errors import DriveError

@pytest.mark.asyncio
async def test_upload_protected_for_writers_and_readers(env):
    job_id = await make_job(env, [('secret.bin', b'abc' * 100)])
    await repo.update_job(env['db'], job_id, status='queued')
    env['worker'].spawn(job_id)
    await wait_done(env['worker'], job_id, env['db'])
    item = (await repo.items_of(env['db'], job_id))[0]
    assert item['state'] == 'done'
    assert item['drive']['protected'] is True
    restrictions = env['fd'].files[item['drive']['file_id']]['downloadRestrictions']['itemDownloadRestriction']
    assert restrictions == {'restrictedForReaders': True, 'restrictedForWriters': True}

@pytest.mark.asyncio
async def test_failed_protection_keeps_uploaded_id_for_retry(env):
    env['fd'].faults['protect'] = True
    job_id = await make_job(env, [('secret.bin', b'abc' * 100)])
    await repo.update_job(env['db'], job_id, status='queued')
    env['worker'].spawn(job_id)
    await wait_done(env['worker'], job_id, env['db'])
    item = (await repo.items_of(env['db'], job_id))[0]
    assert item['state'] == 'failed'
    assert item['drive']['protection_pending']
    fid = item['drive']['file_id']
    assert fid in env['fd'].uploaded
    env['fd'].faults['protect'] = False
    job = await repo.get_job(env['db'], job_id)
    await env['worker']._protect_uploaded(job, item, item['drive'])
    assert env['fd'].files[fid]['downloadRestrictions']['itemDownloadRestriction']['restrictedForWriters']
    assert len(env['fd'].uploaded) == 1

@pytest.mark.asyncio
async def test_recursive_inventory_skips_shortcuts_and_checks_limit(env):
    client = await env['accounts'].client(env['uid'])
    root = await client.create_folder('Root')
    sub = await client.create_folder('Nested', root['id'])
    for fid, mime, parent in [('f1', 'text/plain', root['id']), ('f2', 'text/plain', sub['id']), ('shortcut', 'application/vnd.google-apps.shortcut', root['id'])]:
        env['fd'].files[fid] = {'id': fid, 'name': fid, 'mimeType': mime, 'parents': [parent]}
    files = await client.protection_inventory(root['id'])
    assert {f['id'] for f in files} == {'f1', 'f2'}
    assert not [c for c in env['fd'].calls if c[0] == 'protect']
    with pytest.raises(DriveError):
        await client.protection_inventory(root['id'], max_items=1)
    assert not [c for c in env['fd'].calls if c[0] == 'protect']

@pytest.mark.asyncio
async def test_copy_preserves_original_name(env):
    client = await env['accounts'].client(env['uid'])
    env['fd'].files['src'] = {'id': 'src', 'name': 'video.mp4', 'mimeType': 'video/mp4', 'parents': ['root']}
    result = await client.copy_file('src')
    assert result['name'] == 'video.mp4'
