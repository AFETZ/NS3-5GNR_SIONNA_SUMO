#!/usr/bin/env python3
"""Reproduce the manuscript's 140 EVA + 27 complete-case intersection analyses.

Inputs: data.json.gz and the three accompanying ZIP files. No simulator or network is needed.
Python >=3.11; NumPy 2.3.5. Run: python analyze.py [--output results.json]
"""
from __future__ import annotations
import argparse, csv, gzip, hashlib, io, json, math, zipfile
from collections import defaultdict
from pathlib import Path
import xml.etree.ElementTree as ET
import numpy as np

ROOT = Path(__file__).resolve().parent
MODES = ('radar_bad_link', 'radar_only', 'radar_good_link')
DATA_SHA256 = '3686b14a48cab558fba841847f9da47ec8621e8875ae769329ab78db86f771a0'
BUNDLE_SHA256 = {'eva_logs.zip': '43a6fa0cd605d4bd4d00d3b46cc6788f6f7ef16f4d6cd3b5bd054fee8c8f8d7c', 'launch_configurations.zip': 'c5c615275b45f299c1bdecf66c7982e98cb13f7508bb765852cd263022c462e0', 'phy_sources.zip': '0670cc5aab005a6259a8cb211d16b20299f540ca3ecc5943262f7a679c9d5e69'}

def parse_csv(text: str) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(text)))


def num(value) -> float:
    try: return float(value)
    except (ValueError,TypeError): return math.nan

def close(a,b,tol=1e-8):
    if not math.isclose(float(a),float(b),rel_tol=1e-10,abs_tol=tol):
        raise AssertionError((a,b))

def bootstrap(values,statistic,n=10000):
    values=np.asarray(values,dtype=float)
    if not len(values): return [None,None]
    rng=np.random.default_rng(204)  # reset per treatment and statistic
    draws=values[rng.integers(0,len(values),size=(n,len(values)))]
    return [float(x) for x in np.quantile(statistic(draws,axis=1),[.025,.975],method='linear')]

def average_ranks(values):
    values=np.asarray(values,dtype=float);rank=np.empty(len(values))
    for v in np.unique(values):
        ids=np.flatnonzero(values==v)
        rank[ids]=np.count_nonzero(values<v)+(len(ids)+1)/2
    return rank

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output',type=Path,default=Path('results.json'))
    args=p.parse_args()
    payload=(ROOT/'data.json.gz').read_bytes()
    if hashlib.sha256(payload).hexdigest()!=DATA_SHA256:
        raise ValueError('data.json.gz checksum mismatch')
    data=json.loads(gzip.decompress(payload))
    for name,expected in BUNDLE_SHA256.items():
        if hashlib.sha256((ROOT/name).read_bytes()).hexdigest()!=expected:
            raise ValueError(name+' checksum mismatch')
    if not __debug__:
        raise SystemExit('Run without -O: validation assertions must remain enabled.')
    # This validates the retained selection ledger, not missing/excluded raw records.
    ledger=data['selection_ledger']
    assert len(ledger)==90 and len({r['run_id'] for r in ledger})==90
    available={m:{int(r['RngRun']) for r in ledger
                  if r['mode']==m and r['required_records_available'].lower()=='true'} for m in MODES}
    common=sorted(set.intersection(*available.values()))
    assert common==[1,2,3,4,5,6,7,8,11]
    assert sum(r['required_records_available'].lower()!='true' for r in ledger)==59
    assert sum(r['required_records_available'].lower()=='true' and int(r['RngRun']) not in common for r in ledger)==4
    for r in ledger:
        assert (r['included_primary'].lower()=='true') == (int(r['RngRun']) in common)
    manifests=data['run_metadata']
    assert len(manifests)==167 and len({r['run_id'] for r in manifests})==167
    eva_manifests=[r for r in manifests if r['campaign']=='eva']
    assert len(eva_manifests)==140
    eva_runtime=sum(float(r['runtime_s']) for r in eva_manifests)
    # EVA: retained receiver/run tables are the inputs; no raw EVA logs are packaged.
    runs=data['eva_runs']
    receivers=data['eva_receivers']
    assert len(runs)==140 and len({r['run_id'] for r in runs})==140 and len(receivers)==2660
    grouped=defaultdict(list)
    for r in receivers:grouped[r['run_id']].append(r)
    for r in runs:
        rx=grouped[r['run_id']];assert len(rx)==19
        ds=[];sens=[]
        for v in rx:
            assert num(v['observation_window_end'])==60
            if v['censored'].lower()=='false':
                delay=num(v['first_cam_rx_time'])-num(v['first_cam_tx_time'])
                close(delay,v['reaction_delay']);ds.append(delay);sens.append(delay)
            else:sens.append(60-num(v['first_cam_tx_time']))
        close(np.quantile(ds,.9,method='linear'),r['p90_reaction_delay_success_only'])
        close(np.quantile(sens,.9,method='linear'),r['p90_reaction_delay_censored_as_window'])
        assert int(r['potential_receptions'])==19*int(r['cam_tx_count'])
        close(num(r['successful_receptions'])/num(r['potential_receptions']),r['prr'])
    eva_summary=[]
    for power in sorted({num(r['txPower']) for r in runs}):
        rr=[r for r in runs if num(r['txPower'])==power];assert len(rr)==10
        vv=[v for r in rr for v in grouped[r['run_id']]]
        pr=[num(r['prr']) for r in rr];pp=[num(r['p90_reaction_delay_success_only']) for r in rr]
        pl,ph=bootstrap(pr,np.mean);ql,qh=bootstrap(pp,np.median)
        eva_summary.append(dict(txPower=power,n_runs=10,prr_mean=float(np.mean(pr)),prr_ci_low=pl,prr_ci_high=ph,
            p90_delay_median=float(np.median(pp)),p90_ci_low=ql,p90_ci_high=qh,
            receiver_histories=len(vv),censored=sum(v['censored'].lower()=='true' for v in vv),
            window_substitution_p90_median=float(np.median([num(r['p90_reaction_delay_censored_as_window']) for r in rr]))))
    # Independently extract the available 81 complete EVA MSG sets.
    raw_checks=[]
    with zipfile.ZipFile(ROOT/'eva_logs.zip') as logs:
        names=set(logs.namelist())
        folders={name.split('/')[0] for name in names}
        assert len(folders)==81 and folders<={r['run_id'] for r in runs}
        expected_files={f'eva-veh{i}-MSG.csv' for i in range(1,21)}
        for row in runs:
            run_id=row['run_id']
            if run_id not in folders:continue
            files={n.split('/',1)[1] for n in names if n.startswith(run_id+'/')}
            assert files==expected_files
            def messages_for(vehicle):
                return parse_csv(logs.read(f'{run_id}/eva-veh{vehicle}-MSG.csv').decode('utf-8-sig'))
            tx={r['msg_seq']:num(r['tx_t_s']) for r in messages_for(2)
                if r['msg_type']=='CAM' and num(r['tx_id'])==2
                and math.isfinite(num(r['tx_t_s'])) and math.isfinite(num(r['msg_seq']))}
            first=min(tx.values());received=0;delays=[]
            for vehicle in range(1,21):
                if vehicle==2:continue
                times=[num(r['rx_t_s']) for r in messages_for(vehicle)
                       if r['msg_type']=='CAM' and num(r['tx_id'])==2
                       and r['rx_ok']=='1' and math.isfinite(num(r['rx_t_s']))]
                received+=len(times)
                if times:delays.append(min(times)-first)
            values=dict(cam_tx_count=len(tx),successful_receptions=received,
                        prr=received/(19*len(tx)),
                        p90_reaction_delay_success_only=float(np.quantile(delays,.9,method='linear')))
            errors=[abs(float(v)-num(row[k])) for k,v in values.items()]
            assert max(errors)<1e-8
            raw_checks.append(dict(run_id=run_id,**values,max_abs_error=max(errors)))
    assert len(raw_checks)==81
    # One intersection selection for PRR, first control, AoI and collision outcomes.
    inputs=data['intersection_counts']
    assert len(inputs)==27 and len({r['run_id'] for r in inputs})==27
    assert {r['run_id'] for r in inputs}=={r['run_id'] for r in ledger if r['included_primary'].lower()=='true'}
    assert set(data['intersection_records'])=={r['run_id'] for r in inputs}
    inter=[];links=[]
    for row in inputs:
        assert int(row['RngRun']) in common
        record=next(r for r in ledger if r['run_id']==row['run_id'])
        assert row['mode']==record['mode'] and row['RngRun']==record['RngRun']
        assert record['included_primary'].lower()=='true'
        record=data['intersection_records'][row['run_id']]
        messages=parse_csv(record['MSG']);controls=parse_csv(record['CTRL'])
        xml=ET.fromstring(record['collision_XML']);assert xml.tag=='collisions'
        collisions=xml.findall('collision')
        assert all({c.get('collider'),c.get('victim')}=={'veh2','veh3'} for c in collisions)
        accepted=[c for c in controls if c['event_type'] in ('cam_reaction','sensor_reaction')]
        assert accepted
        first_event=min(accepted,key=lambda c:num(c['time_s']));first=num(first_event['time_s'])
        close(first,row['archived_first_control_s'])
        rx=[m for m in messages if m['msg_type']=='CAM' and m['tx_id']=='2' and m['rx_ok']=='1' and math.isfinite(num(m['rx_t_s']))]
        assert len(rx)==int(row['archived_successful_receptions'])
        tx=int(row['cam_tx_count_from_veh2'])
        assert 0<=len(rx)<=tx
        if row['mode']=='radar_only':assert tx==0 and len(rx)==0
        else:assert tx>0
        first_rx=min([num(m['rx_t_s']) for m in rx],default=None)
        before=[m for m in rx if num(m['rx_t_s'])<=first+1e-9 and math.isfinite(num(m['cam_gdt_ms']))]
        gen=max([num(m['cam_gdt_ms'])/1000 for m in before],default=None)
        if gen is not None:assert 0<=gen<=first and first<20
        indexed=defaultdict(list)
        for m in rx:indexed[(m['tx_id'],m['msg_seq'])].append(m)
        for c in controls:
            if c['event_type']!='cam_reaction':continue
            matches=indexed[(c['source_id'],c['msg_seq'])]
            assert len(matches)==1 and matches[0]['rx_id']=='3' and c['vehicle_id']=='veh3'
            delta=num(c['time_s'])-num(matches[0]['rx_t_s']);close(delta,0)
            assert c['pkt_uid'] in ('-1','18446744073709551615','')
            links.append(dict(run_id=row['run_id'],mode=row['mode'],source_id=c['source_id'],msg_seq=c['msg_seq'],
                              control_time_s=num(c['time_s']),rx_time_s=num(matches[0]['rx_t_s']),delta_s=delta,pkt_uid=c['pkt_uid']))
        inter.append(dict(run_id=row['run_id'],RngRun=int(row['RngRun']),mode=row['mode'],cam_tx_count=tx,
            successful_rx=len(rx),prr=len(rx)/tx if tx else '',first_control_s=first,first_control_type=first_event['event_type'],
            first_station2_rx_s=first_rx if first_rx is not None else '',
            aoi_first_control_ms=(first-gen)*1000 if gen is not None else '',
            collision_recorded=int(bool(collisions)),first_collision_s=min([num(c.get('time')) for c in collisions],default=''),
            collision_xml_status='valid',controller_status='valid',receiver_MSG_status='valid'))
    assert len(links)==281
    inter_summary=[]
    for mode in MODES:
        rr=[r for r in inter if r['mode']==mode];assert len(rr)==9
        assert sorted(r['RngRun'] for r in rr)==common
        pr=[r['prr'] for r in rr if r['prr']!=''];ts=[r['first_control_s'] for r in rr]
        pl,ph=bootstrap(pr,np.mean,20000);tl,th=bootstrap(ts,np.median,20000)
        ao=[r['aoi_first_control_ms'] for r in rr if r['aoi_first_control_ms']!='']
        inter_summary.append(dict(mode=mode,n_runs=9,prr_mean=float(np.mean(pr)) if pr else '',prr_ci_low=pl if pl is not None else '',
            prr_ci_high=ph if ph is not None else '',first_control_median_s=float(np.median(ts)),first_control_ci_low_s=tl,first_control_ci_high_s=th,
            collision_runs=sum(r['collision_recorded'] for r in rr),no_collision_runs=sum(1-r['collision_recorded'] for r in rr),
            aoi_defined=len(ao),aoi_median_ms=float(np.median(ao)) if ao else '',aoi_min_ms=min(ao) if ao else '',aoi_max_ms=max(ao) if ao else '',
            cam_control_matches=sum(r['mode']==mode for r in links)))
    assert [r['collision_runs'] for r in inter_summary]==[9,9,0]
    meta=data['intersection_metadata']
    seed_runtime={}
    for m in meta:
        seed=int(m['RngRun']);value=num(m['seed_batch_runtime_s'])
        if seed in seed_runtime:close(seed_runtime[seed],value)
        seed_runtime[seed]=value
    v2x=[r for r in inter if r['mode']!='radar_only']
    assert all(math.isclose(r['first_control_s'],r['first_station2_rx_s'],abs_tol=1e-8) for r in v2x)
    report=dict(analyzed_total=167,EVA_runs=140,EVA_receiver_histories=2660,EVA_censored=3,
        EVA_potential_receptions=sum(int(r['potential_receptions']) for r in runs),EVA_successful_receptions=sum(int(r['successful_receptions']) for r in runs),
        rho_power_prr=float(np.corrcoef(average_ranks([num(r['txPower']) for r in runs]),average_ranks([num(r['prr']) for r in runs]))[0,1]),
        rho_prr_p90=float(np.corrcoef(average_ranks([num(r['prr']) for r in runs]),average_ranks([num(r['p90_reaction_delay_success_only']) for r in runs]))[0,1]),
        intersection_runs=27,intersection_RngRun=common,intersection_runs_per_mode=9,intersection_valid_collision_XML=27,
        intersection_valid_MSG_CTRL=27,cam_control_matches=281,finite_CAM_AoI_runs=18,first_RX_equals_first_control_runs=len(v2x),
        selected_intersection_seed_batch_runtime_s=sum(seed_runtime.values()),EVA_recorded_runtime_s=eva_runtime,
        intersection=inter_summary,bootstrap=dict(EVA_resamples=10000,intersection_resamples=20000,seed=204,reset='each group and statistic',quantiles='linear'),
        uncertainty='Descriptive within-configuration bootstrap; no collision confidence intervals or p-values. Completeness-selected cases are not a random sample.',
        software=dict(numpy=np.__version__),
        verification_scope={'EVA': 'reaggregation of retained run and receiver tables', 'intersection': 'included receiver MSG, CTRL and collision XML', 'selection': 'consistency of retained availability ledger; excluded files are not supplied', 'EVA_raw_check': '81 complete MSG sets independently extracted and checked against the retained run table'})
    report['EVA_raw_crosscheck_runs']=len(raw_checks)
    report['EVA_raw_crosscheck_max_error']=max(r['max_abs_error'] for r in raw_checks)
    results=dict(summary=report,eva_raw_crosscheck=raw_checks,eva_summary=eva_summary,
                 intersection_run_metrics=inter,intersection_summary=inter_summary,
                 cam_control_matches=links)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(results,indent=2,allow_nan=False)+'\n',encoding='utf-8')
    print(f'PASS: 140 EVA runs; 27 intersection runs; {len(links)} CAM-control links.')
    print(f'Collision counts: {[r["collision_runs"] for r in inter_summary]} (9 runs per mode).')
    print(f'Results: {args.output}')
    print('EVA raw-log checks: 81/81 passed.')


if __name__=='__main__':main()
