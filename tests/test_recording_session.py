import json
from pathlib import Path
import threading
from types import SimpleNamespace

import pytest

from cs2_data import recording_session as session, session_evidence as evidence, session_processing as processing
from cs2_data import full_demo, demo_pipeline
from cs2_data.io import read_json, sha256_file
from cs2_data.launcher_backend import save_settings
from cs2_data.training_archive import FORMAT


@pytest.fixture
def plan(tmp_path):
    demo=tmp_path/'source.dem'; demo.write_bytes(b'demo')
    source={'steam_id':'123','demo_id':sha256_file(demo),'demo':str(demo)}
    def segment(key,start,end,source_end=20000):
        return {'id':key,'round_id':3,'start_demo_tick':start,'end_demo_tick':end,
            'source_job':{**source,'fps':32,'width':640,'height':360,'spectator_user_id':9,
                'start_demo_tick':1000,'end_demo_tick':source_end}}
    return {'format':FORMAT,'source':source,'segments':[segment('a',1000,8680),segment('b',8666,9800),segment('c',12000,13000)]}


def test_long_window_and_history_overlap_use_one_movie(plan):
    result=session.plan_session(plan)
    assert len(result['runs'])==2
    assert result['runs'][0]['segments']==['a','b']
    assert (result['runs'][0]['start_tick'],result['runs'][0]['end_tick'])==(998,9802)
    assert result['runs'][0]['min_frames']>3840
    assert result['storage']['required_free_bytes']>result['storage']['raw_frame_bytes']+session.RESERVE
    assert result['storage']['required_free_bytes'] >= sum(result['storage'][key] for key in
        ('raw_frame_bytes','max_log_bytes','max_index_bytes','max_shared_archive_bytes','max_training_archive_bytes','staged_demo_bytes','reserve_bytes'))
    assert result['max_frames']<sum((s['end_demo_tick']-s['start_demo_tick'])//2+3 for s in plan['segments'])


def test_short_gaps_bridge_but_remain_excluded(plan):
    plan['segments'][1].update(start_demo_tick=8700,end_demo_tick=9800)
    result=session.plan_session(plan)
    assert result['runs'][0]['eligible']==[{'start':1000,'end':8680,'round_id':3},{'start':8700,'end':9800,'round_id':3}]


@pytest.mark.parametrize('change',[
    lambda p:p.update(format={**FORMAT,'color':'gray'}),
    lambda p:p['segments'].reverse(),
    lambda p:p['segments'][0].update(end_demo_tick=20002),
    lambda p:p['segments'][0]['source_job'].update(steam_id='456'),
    lambda p:p['segments'][0].update(end_demo_tick=8681),
])
def test_invalid_schedule_never_reaches_game(plan,change):
    change(plan)
    with pytest.raises(ValueError):session.plan_session(plan)


@pytest.fixture
def indexed(tmp_path):
    root=tmp_path/'session';root.mkdir()
    schedule={'runs':[{'id':'run','prefix':'abc','segments':['a','b'],'min_frames':6,'max_frames':7,
        'eligible':[{'start':1000,'end':1006},{'start':1004,'end':1010}]}]}
    save_settings(root/'session-plan.json',schedule)
    profile=evidence.get_native_replay_profile(evidence.CURRENT_PROFILE)
    rows=[{'event':'header','schema_version':1,'native_profile':evidence.CURRENT_PROFILE,
        'engine_sha256':profile['engine_sha256'],'native_observation':{'client_sha256':profile['client_sha256']}},
        {'event':'demo_packet_hook_installed'},
        {'event':'demo_packet_read','read_invocation':1,'phase':'entry'},
        {'event':'demo_packet_read','read_invocation':1,'phase':'return'}]
    for n in range(6):
        movie={'event':'movie_frame','movie_name':'abc_','capture_index':n,'counter_after':n+1,
            'replay_demo_tick':1000+2*n,'tga_filename':f'abc_{n:08d}.tga','native_clock':{'lifetime':n+100}}
        rows += [{'event':'network_message','invocation_index':n+100},movie,
            {'event':'pixel_readback','submission_candidate':movie,'success':True}]
    rows += [{'event':'movie_end','movie_name':'abc_','next_capture_index':6,'replay_demo_tick':1012},
        {'event':'session_run_closed','run_id':'run','frames':6,'complete':True}]
    ledger=root/'capture_ledger.jsonl';ledger.write_text(''.join(json.dumps(r)+'\n' for r in rows))
    save_settings(root/'one.render.json',{'render_status':'session_captured_timing_unverified',
        'plugin_sha256':session.PLUGIN_SHA256,'plugin_source_sha256':session.PLUGIN_SHA256,'plugin_staged_sha256':session.PLUGIN_SHA256,
        'cs2_exit_code':0,'settings_restored':True,'gameinfo_restored':True,'staged_plugin_removed_from_game':True,
        'recording_session':{'profile':session.PROFILE,'plan_sha256':sha256_file(root/'session-plan.json'),'completed_runs':1},
        'capture_ledger_sha256':sha256_file(ledger)})
    return evidence.index_session(root),schedule['runs'][0],root


def test_logical_cut_uses_real_subsequent_frame_and_keeps_global_history(indexed):
    index,run,root=indexed
    records=evidence.make_view(index,run,{'id':'a','start_demo_tick':1000,'end_demo_tick':1006},root/'a.view.json')
    frames=list(evidence.events(records,('movie_frame',)))
    assert [r['capture_index'] for r in frames]==[0,1,2]
    assert [r['native_capture_index'] for r in frames]==[1,2,3]
    assert frames[0]['tga_filename']=='abc_00000001.tga'
    boundary=evidence.endpoints(records)[0]
    assert boundary['event']=='session_boundary' and boundary['session_origin_event']=='movie_frame'
    assert boundary['native_capture_index']==4 and boundary['native_clock']=={'lifetime':104}
    assert [r['invocation_index'] for r in evidence.events(records,('network_message',))]==list(range(100,105))
    assert len(list(evidence.events(records,('demo_packet_read',))))==2
    assert not list(evidence.events(records,('movie_end',)))
    assert evidence.endpoints([boundary])==[] # A JSON claim alone cannot create a logical endpoint.


def test_last_shard_uses_real_movie_end(indexed):
    index,run,root=indexed
    records=evidence.make_view(index,run,{'id':'b','start_demo_tick':1004,'end_demo_tick':1010},root/'b.view.json')
    assert evidence.endpoints(records)[0]['session_origin_event']=='movie_end'
    assert [r['native_capture_index'] for r in evidence.events(records,('movie_frame',))]==[3,4,5]


def test_view_cannot_expand_eligibility_or_change_shared_evidence(indexed):
    index,run,root=indexed
    with pytest.raises(ValueError,match='eligibility'):
        evidence.make_view(index,run,{'id':'a','start_demo_tick':1000,'end_demo_tick':1010},root/'bad.view.json')
    Path(index['ledger']).write_text('changed')
    with pytest.raises(ValueError,match='changed'):
        evidence.make_view(index,run,{'id':'a','start_demo_tick':1000,'end_demo_tick':1006},root/'bad2.view.json')


def test_partial_index_is_retained_and_rebuilt(indexed):
    index,run,root=indexed
    (root/'session-index.json').unlink()
    result=evidence.index_session(root)
    assert result['record_count']==index['record_count']
    assert len(list(root.glob('incomplete-index-*.sqlite')))==1


def test_capture_barrier_stops_before_index_or_validation(plan,tmp_path,monkeypatch):
    root=tmp_path/'queue';root.mkdir()
    save_settings(root/'demo_plan.json',plan)
    progress={'segments':{},'accepted_samples':0}
    stop=threading.Event()
    monkeypatch.setattr(full_demo,'write_report',lambda *a:None)
    calls=[]
    def capture(root,plan,schedule,tools,**kw):
        out=kw['output'];out.mkdir(parents=True)
        save_settings(out/'session-plan.json',schedule)
        save_settings(out/'a.render.json',{'render_status':'session_captured_timing_unverified',
            'recording_session':{'plan_sha256':sha256_file(out/'session-plan.json'),'completed_runs':1,'status':'stopped'}})
        calls.append('capture');stop.set()
    monkeypatch.setattr(session,'capture_session',capture)
    monkeypatch.setattr(processing,'index_session',lambda *a:pytest.fail('Must not index on user stop'))
    assert session.prepare_recordings(root,plan,progress,{},stop=stop,emit=lambda m:None) is None
    assert calls==['capture'] and progress['status']=='stopped'
    assert read_json(root/'session-state.json')['attempts'][0]['status']=='captured'


def test_full_demo_never_starts_workers_until_barrier_returns(tmp_path,monkeypatch):
    root=tmp_path/'queue';root.mkdir()
    source=root/'source.dem';source.write_bytes(b'demo')
    plan={'profile':full_demo.PROFILE,'format':FORMAT,'source_files':{str(source):sha256_file(source)},'segments':[]}
    save_settings(root/'demo_plan.json',plan)
    save_settings(root/'progress.json',{'plan_sha256':sha256_file(root/'demo_plan.json'),'segments':{},'accepted_samples':0})
    monkeypatch.setattr(full_demo,'renderer_tools',lambda *a:{})
    calls=[]
    def prepare(*a,**kw):calls.append('all_capture_finished');return {}
    def validate(*a,**kw):
        assert calls==['all_capture_finished'] and kw['session_jobs']=={}
        calls.append('validators');return {'status':'complete'}
    monkeypatch.setattr(session,'prepare_recordings',prepare)
    monkeypatch.setattr(demo_pipeline,'run_pipeline',validate)
    monkeypatch.setattr(processing,'release_completed_sessions',lambda *a:None)
    assert full_demo.run_demo(root)['status']=='complete'
    assert calls==['all_capture_finished','validators']


def test_resume_captures_only_missing_intervals_before_indexing(plan,tmp_path,monkeypatch):
    root=tmp_path/'queue';root.mkdir()
    save_settings(root/'demo_plan.json',plan)
    progress={'segments':{},'accepted_samples':0}
    stop=threading.Event()
    monkeypatch.setattr(full_demo,'write_report',lambda *a:None)
    captures,indexed=[],[]
    def capture(root,plan,schedule,tools,**kw):
        captures.append([s['id'] for s in plan['segments']])
        out=kw['output'];out.mkdir(parents=True)
        save_settings(out/'session-plan.json',schedule)
        first=len(captures)==1
        save_settings(out/'a.render.json',{'render_status':'session_captured_timing_unverified',
            'recording_session':{'plan_sha256':sha256_file(out/'session-plan.json'),
                'completed_runs':1,'status':'stopped' if first else 'complete'}})
        if first:stop.set()
    def index(out,**kwargs):
        assert captures==[['a','b','c'],['c']], 'Both capture phases must finish first'
        indexed.append(out)
        return {'root':str(out)}
    monkeypatch.setattr(session,'capture_session',capture)
    monkeypatch.setattr(processing,'index_session',index)
    monkeypatch.setattr(processing,'pack_session',lambda index,destination,**kw:destination/'receipt.json')
    assert session.prepare_recordings(root,plan,progress,{},stop=stop,emit=lambda m:None) is None
    assert not indexed
    stop.clear()
    jobs=session.prepare_recordings(root,plan,progress,{},stop=stop,emit=lambda m:None)
    assert set(jobs)=={'a','b','c'} and len(indexed)==2
    assert jobs['a']['index']==jobs['b']['index'] and jobs['a']['index']!=jobs['c']['index']


def test_frame_inventory_keeps_already_owned_files_in_place(indexed,monkeypatch):
    index,run,root=indexed
    folder=root/'renderer-sandbox/movie/stamp';folder.mkdir(parents=True)
    paths=[]
    for n in range(6):
        path=folder/f'abc_{n:08d}.tga';path.write_bytes(bytes([n]));paths.append(path)
    render=read_json(Path(index['render_manifest']))
    render['recording_roots']=[str(folder.parent)]
    save_settings(Path(index['render_manifest']),render)
    monkeypatch.setattr(processing.batch,'_worker',lambda:SimpleNamespace(
        capture_files=lambda roots,prefix:paths,inspect_tga=lambda *a:None))
    inventory=processing.collect_frames(index)
    assert all(path.exists() and inventory[path.name]['path']==str(path) for path in paths)
    assert not list((root/'raw').rglob('*.tga'))


def test_index_and_view_release_database_handles_without_gc(tmp_path):
    import gc
    from cs2_data.immutable_evidence import hold_files
    gc.collect()
    enabled=gc.isenabled()
    gc.disable()
    try:
        index,run,root=indexed.__wrapped__(tmp_path)
        records=evidence.make_view(index,run,{'id':'a','start_demo_tick':1000,'end_demo_tick':1006},root/'a.view.json')
        stream=evidence.events(records,('network_message',))
        next(stream)
        stream.close()
        with hold_files(processing.index_files(index)):
            assert evidence.endpoints(records)[0]['session_origin_event']=='movie_frame'
        # Windows refuses this rename if a SQLite reader still owns the file.
        Path(index['database']).rename(root/'closed.sqlite')
    finally:
        if enabled:gc.enable()
        gc.collect()


def test_lean_storage_budget_omits_evidence_archive_only(plan):
    full = session.plan_session(plan)["storage"]
    lean = session.plan_session({**plan, "evidence_retention": "lean"})["storage"]
    assert lean["max_shared_archive_bytes"] == 0
    assert full["required_free_bytes"]-lean["required_free_bytes"] == full["max_shared_archive_bytes"]
    for key in ("raw_frame_bytes", "max_log_bytes", "max_index_bytes", "max_training_archive_bytes", "reserve_bytes"):
        assert lean[key] == full[key]


@pytest.fixture
def lean_session(tmp_path, monkeypatch):
    queue = tmp_path/"queue"
    (queue/"sessions").mkdir(parents=True)
    index, run, root = indexed.__wrapped__(queue/"sessions")
    source = queue/"original.dem"
    source.write_bytes(b"demo bytes")
    (root/"input.dem").write_bytes(source.read_bytes())
    raw = root/"raw/abc"
    raw.mkdir(parents=True)
    paths = []
    for n in range(6):
        path = raw/f"abc_{n:08d}.tga"
        path.write_bytes(bytes([n]))
        paths.append(path)
    render_path = Path(index["render_manifest"])
    render = read_json(render_path)
    render.update(recording_roots=[str(raw)], demo_id=sha256_file(source), source_job={"demo_uri": str(source)})
    save_settings(render_path, render)
    index["render_sha256"] = sha256_file(render_path)
    save_settings(root/"session-index.json", index)
    monkeypatch.setattr(processing.batch, "_worker", lambda: SimpleNamespace(
        capture_files=lambda roots, prefix: paths, inspect_tga=lambda *a: None))
    receipt_path = processing.pack_session(index, queue/"session-packages"/root.name)
    progress = {"segments": {}}
    for key in ("a", "b"):
        destination = queue/"packages"/key
        destination.mkdir(parents=True)
        training = destination/"training.zip"
        training.write_bytes(("verified training "+key).encode())
        package = {"schema_version": 2, "status": "complete", "segment_id": key,
            "evidence_retention": "lean", "training_archive": {"path": str(training), "sha256": sha256_file(training)},
            "shared_session": {"receipt": str(receipt_path), "receipt_sha256": sha256_file(receipt_path)}}
        save_settings(destination/"receipt.json", package)
        progress["segments"][key] = {"status": "complete", "receipt": str(destination/"receipt.json"),
            "receipt_sha256": sha256_file(destination/"receipt.json")}
    save_settings(queue/"session-state.json", {"attempts": [{"status": "captured", "out": str(root)}]})
    return queue, root, index, receipt_path, progress


def test_lean_session_never_archives_and_waits_for_all_dependent_segments(lean_session):
    queue, root, index, receipt_path, progress = lean_session
    receipt = read_json(receipt_path)
    assert receipt["evidence_retention"] == "lean" and receipt["compressed_bytes"] == 0
    assert not list((queue/"session-packages").rglob("*.zip"))
    assert len(processing.live_session_frames(receipt)) == 6
    assert processing.pack_session(index, receipt_path.parent) == receipt_path
    progress["segments"]["b"]["status"] = "needs_attention"
    processing.release_completed_sessions(queue, progress)
    assert root.exists() and len(list(root.rglob("*.tga"))) == 6
    progress["segments"]["b"]["status"] = "complete"
    processing.release_completed_sessions(queue, progress)
    assert not root.exists() and receipt_path.is_file()
    assert processing.verify_archive(read_json(receipt_path)) == receipt
    assert read_json(queue/"session-state.json")["attempts"][0]["status"] == "released"
    processing.release_completed_sessions(queue, progress)


@pytest.mark.parametrize("mutation", ["training", "receipt", "source"])
def test_lean_session_corruption_blocks_raw_cleanup(lean_session, mutation):
    queue, root, _, receipt_path, progress = lean_session
    path = {"training": queue/"packages/a/training.zip", "receipt": receipt_path,
            "source": queue/"original.dem"}[mutation]
    with path.open("ab") as stream:
        stream.write(b" " if mutation == "receipt" else b"changed")
    with pytest.raises(ValueError):
        processing.release_completed_sessions(queue, progress)
    assert root.exists() and len(list(root.rglob("*.tga"))) == 6


def test_lean_cleanup_resumes_after_partial_raw_deletion(lean_session, monkeypatch):
    queue, root, _, _, progress = lean_session
    original = Path.unlink
    removed = []
    def interrupted(path, *args, **kwargs):
        if path.is_relative_to(root):
            if removed:
                raise OSError("interrupted cleanup")
            removed.append(path)
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, "unlink", interrupted)
    with pytest.raises(OSError, match="interrupted cleanup"):
        processing.release_completed_sessions(queue, progress)
    assert root.exists() and removed
    assert read_json(queue/"session-state.json")["attempts"][0]["status"] == "captured"
    monkeypatch.setattr(Path, "unlink", original)
    processing.release_completed_sessions(queue, progress)
    assert not root.exists()
