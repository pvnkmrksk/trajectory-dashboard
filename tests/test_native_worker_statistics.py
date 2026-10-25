"""Execute the shipped Web Worker and compare its inference with SciPy.

These tests exercise the production JavaScript, including the binary/segment
boundary, rather than maintaining a second Python implementation of the math.
"""
from __future__ import annotations

import base64
import json
from pathlib import Path
import shutil
import subprocess

import numpy as np
import pandas as pd
import pytest
from scipy import stats

from native_dashboard.dataset import load_native_dataset


ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="Node is needed to execute the production worker")
RUNNER = r"""
const fs = require('node:fs'), vm = require('node:vm');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
let binary = null, payloadHeader = null, payloadBodyOffset = 0;
if (input.payload) {
  const bytes = Buffer.from(input.payload, 'base64');
  binary = bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength);
  const size = new DataView(binary).getUint32(0, true);
  payloadHeader = JSON.parse(new TextDecoder().decode(new Uint8Array(binary, 4, size)));
  payloadBodyOffset = 4 + size + payloadHeader.bodyPadding;
}
const messages = [];
const context = vm.createContext({self: {}, performance, binary, payloadHeader,
  payloadBodyOffset, structuredClone, postMessage: value => messages.push(value), messages});
const source = fs.readFileSync(input.worker, 'utf8');
const result = vm.runInContext(source + '\n' + input.script, context, {timeout: 20000});
process.stdout.write(JSON.stringify(result, (_, value) =>
  ArrayBuffer.isView(value) ? Array.from(value) : value));
"""


def worker(script: str, payload: bytes | None = None):
    result = subprocess.run(
        [NODE, "-e", RUNNER],
        input=json.dumps({
            "worker": str(ROOT / "native_dashboard/static/worker.js"),
            "script": script,
            "payload": base64.b64encode(payload).decode() if payload else None,
        }),
        text=True, capture_output=True, check=True, timeout=30,
    )
    return json.loads(result.stdout)


def test_exact_binomial_and_confidence_intervals_match_scipy():
    cases = [(0, 1, .5), (1, 1, .5), (2, 10, .5), (8, 10, .3),
             (1000, 5000, .5), (4900, 10000, .49), (0, 100000, .00001),
             (12, 120, .1), (0, 20, 0), (20, 20, 1), (1, 20, 0)]
    random = np.random.default_rng(147)
    for _ in range(30):
        n = int(random.integers(1, 12000))
        cases.append((int(random.integers(0, n + 1)), n, float(random.uniform(.001, .999))))
    actual = worker(f"{json.dumps(cases)}.map(values => exactBinomial(...values))")
    for case, value in zip(cases, actual, strict=True):
        reference = stats.binomtest(*case)
        interval = reference.proportion_ci(confidence_level=.95, method="exact")
        assert value["pValue"] == pytest.approx(reference.pvalue, rel=2e-8, abs=2e-12), case
        assert value["ciLow"] == pytest.approx(interval.low, abs=2e-10), case
        assert value["ciHigh"] == pytest.approx(interval.high, abs=2e-10), case


def test_binomial_empty_and_invalid_input_has_no_inference():
    actual = worker("[[0,0,.5],[-1,10,.5],[11,10,.5],[1,10,1.1]].map(c => exactBinomial(...c))")
    assert actual == [{"pValue": None, "ciLow": None, "ciHigh": None}] * 4


def test_rank_statistics_match_scipy_with_ties_and_identical_rank_sums():
    groups = [
        [[1, 2, 3], [4, 5, 6], [7, 8, 9]],
        [[0, 0, 1, 3, 4], [1, 1, 2, 2, 3], [5, 6, 6]],
        [[1, 2, 3, 4], [1, 2, 3, 4]],
        [list(range(100)), list(range(100, 200))],
    ]
    actual = worker(f"{json.dumps(groups)}.map(g => ({{kw: kruskalWallis(g), mw: mannWhitney(g[0], g[1])}}))")
    for samples, value in zip(groups, actual, strict=True):
        kw = stats.kruskal(*samples)
        mw = stats.mannwhitneyu(*samples[:2], method="asymptotic", use_continuity=True)
        assert value["kw"]["h"] == pytest.approx(kw.statistic, abs=1e-10)
        assert value["kw"]["p"] == pytest.approx(kw.pvalue, rel=2e-9, abs=1e-12)
        assert value["mw"]["u1"] == pytest.approx(mw.statistic)
        assert value["mw"]["p"] == pytest.approx(mw.pvalue, rel=2e-9, abs=1e-12)
    assert actual[2]["mw"]["p"] == 1


def test_wilcoxon_exact_tied_and_asymptotic_probabilities_match_scipy():
    cases = [[1, -1, 2, 2], [0, 0, -1, 2, 2, -3],
             [(-1 if i % 3 == 0 else 1) * (i + 1) for i in range(40)],
             [(-1 if i % 3 == 0 else 1) * (i % 7 + 1) for i in range(80)]]
    actual = worker(f"{json.dumps(cases)}.map(d => wilcoxonPaired(d, d.map(() => 0)))")
    for differences, value in zip(cases, actual, strict=True):
        nonzero = np.asarray([v for v in differences if v])
        if len(nonzero) <= 13:
            reference = stats.wilcoxon(nonzero, method=stats.PermutationMethod(n_resamples=np.inf))
        elif len(nonzero) <= 50:
            reference = stats.wilcoxon(nonzero, method="exact")
        else:
            reference = stats.wilcoxon(nonzero, method="approx", correction=True)
        assert value["w"] == pytest.approx(reference.statistic)
        assert value["p"] == pytest.approx(reference.pvalue, rel=2e-9, abs=1e-12)
    edge = worker("[wilcoxonPaired([], []), wilcoxonPaired([0,0], [0,0])]")
    assert edge[0]["p"] is None
    assert edge[1]["p"] == 1


def test_holm_excludes_undefined_tests_and_preserves_monotonicity():
    result = worker("holmAdjust([{p:.01}, {p:.04}, {p:.03}, {p:NaN}])")
    assert [value["adjustedP"] for value in result] == [.03, .06, .06, None]


def test_rayleigh_concentrated_sample_uses_positive_circstat_tail():
    # Golden reference: Zar approximation in CircStat circ_rtest.m. The older
    # polynomial becomes negative here and incorrectly reports exactly p=0.
    result = worker("[rayleigh(Array(10).fill(0)), rayleigh([0,10,20]), rayleigh([0,90,180,270,0,90,180,270])]")
    assert result[0]["p"] == pytest.approx(4.577806202990759e-7)
    assert result[0]["r"] == 1
    assert result[1]["p"] is None
    assert result[2]["p"] == pytest.approx(1)


def test_mirror_group_changes_invalidate_analytical_coordinate_frame():
    result = worker("""
const state={groupBy:'config',mirrorPool:true};const configKey=analysisKey(state);
state.groupBy='scene';const sceneKey=analysisKey(state);
state.mirrorPool=false;const unmirroredScene=analysisKey(state);
state.groupBy='config';
({mirroredDiffers:configKey!==sceneKey, unmirroredSame:unmirroredScene===analysisKey(state)});
""")
    assert result == {"mirroredDiffers": True, "unmirroredSame": True}


def test_histogram_p99_excludes_outliers_and_reports_complete_accounting():
    result = worker("histogram([...Array(100).fill(2), 1e9, NaN, Infinity], 10)")
    assert result["edges"][-1] == 2
    assert result["overflow"] == 1
    assert sum(result["counts"]) == result["displayedCount"] == 100
    assert result["finiteCount"] == 101
    assert result["invalidCount"] == 2
    assert result["upperPercentile"] == .99
    assert result["exactMaximum"] == 1e9
    empty, constant = worker("[histogram([NaN]), histogram([0,0,0])]")
    assert sum(empty["counts"]) == 0
    assert all(np.isfinite(empty["edges"]))
    assert sum(constant["counts"]) == 3


@pytest.fixture
def choice_binary(tmp_path):
    base = pd.Timestamp("2026-01-01")
    files = {"first_VR1_.csv": [(0, "Choice", [-3, 0, -3, 0, 3]),
                                  (1, "Never", [0, 0]),
                                  (2, "NoTargets", [0, 0])],
             "restart_VR1_.csv": [(0, "Choice", [3, 0, 3]),
                                    (1, "Ambiguous", [0, 0])],
             "invalid_VR1_.csv": [(0, "Invalid", [1, 2])]}
    for filename, trials in files.items():
        rows = []
        for step, config, positions in trials:
            for i, x in enumerate(positions):
                rows.append({
                    "Current Time": base + pd.Timedelta(seconds=step * 100 + i),
                    "CurrentTrial": 0, "CurrentStep": step,
                    "GameObjectPosX": x, "GameObjectPosZ": 0,
                    "GameObjectRotY": 0, "ConfigFile": config,
                    "FlyID": "one-animal", "Scene": config,
                })
        pd.DataFrame(rows).to_csv(tmp_path / filename, index=False)
    return load_native_dataset(str(tmp_path)).binary


CHOICE_INIT = """
payloadHeader.rois = {
  Choice: [{x:-3,z:0,side:'left'}, {x:-3,z:0,side:'left'}, {x:3,z:0,side:'right'}],
  Never: [{x:-3,z:0,side:'left'}, {x:3,z:0,side:'right'}],
  Ambiguous: [{x:0,z:0,side:'left'}, {x:0,z:0,side:'right'}],
  NoTargets: [], Invalid: [{x:0,z:0,side:'left'}],
};
initDataset({header:payloadHeader, buffer:binary, bodyOffset:payloadBodyOffset});
for (let seg=0; seg<starts.length; seg++) {
  if (header.categories.config[data.segmentConfig[seg]] === 'Invalid') {
    for(let row=starts[seg];row<ends[seg];row++) data.x[row]=NaN;
  }
}
const state = {groupBy:'all', filters:{}, ranges:{}, roiReach:.1,
  roiEntered:true, roiTrim:true, angleSource:'orientation', polarR:[0,1], statsUnit:'trial'};
selectCoordinateFrame(state);
"""


def test_choice_counts_are_unique_exclusive_and_keep_pre_roi_denominator(choice_binary):
    actual = worker(CHOICE_INIT + """
const analysis=buildAnalysis(state), roi=buildRoi(state,analysis);
({choice:roi.choice, animalTrials:roi.animalTrials, leftReached:roi.leftReachedCount,
 rightReached:roi.rightReachedCount, leftResidence:roi.leftResidence,
 rightResidence:roi.rightResidence, times:roi.timeValues, sides:roi.timeSides,
 base:analysis.roiBaseSegments.length, visible:analysis.visibleSegments.length});
""", choice_binary)
    counts = actual["choice"][0]
    assert counts["total"] == actual["base"] == 5
    assert actual["visible"] == 3
    assert [counts[key] for key in ("left", "right", "ambiguous", "neither")] == [1, 1, 1, 2]
    assert counts["informative"] == 2
    assert counts["leftReached"] == 2
    assert counts["rightReached"] == 3
    assert counts["bothReached"] == 2
    assert counts["noTargets"] == 1
    assert counts["pValue"] == 1
    assert counts["rightFraction"] == .5
    assert actual["animalTrials"] == [5]
    # Duplicate target objects cannot multiply same-side sample residence.
    assert actual["leftResidence"] == pytest.approx([.8])
    assert actual["rightResidence"] == pytest.approx([1])
    # Tail trimming removes the later Right reach from the Left-first trial.
    assert len(actual["times"]) == 4
    assert actual["sides"].count(1) == 2


def test_empty_category_selection_means_none(choice_binary):
    actual = worker(CHOICE_INIT + """
state.filters.vr=[];
const analysis=buildAnalysis(state);
({segments:analysis.visibleSegments.length, base:analysis.roiBaseSegments.length,
 rows:analysis.visibleRows, choice:buildRoi(state,analysis).choice});
""", choice_binary)
    assert actual["segments"] == actual["base"] == actual["rows"] == 0
    assert actual["choice"][0]["pValue"] is None


def test_filter_audit_does_not_count_segments_with_zero_retained_rows(choice_binary):
    actual = worker(CHOICE_INIT + """
state.ranges.time=[10000,20000];
const analysis=buildAnalysis(state);
({visible:analysis.visibleSegments.length, timeAudit:analysis.filterAudit.find(row=>row.label==='Local trial time')});
""", choice_binary)
    assert actual["visible"] == 0
    assert actual["timeAudit"]["segments"] == actual["timeAudit"]["rows"] == 0


def test_roi_panel_denominators_survive_reordering_and_duplicate_labels(choice_binary):
    actual = worker(CHOICE_INIT + """
state.groupBy='config';
state.labels={config:header.categories.config.map(()=> 'Same display label')};
const analysis=buildAnalysis(state), before=buildRoi(state,analysis);
state.panelOrders={config:header.categories.config.map((_,i)=>i).reverse()};
applyPanelMapping(state,analysis);
const after=buildRoi(state,analysis);
({keys:analysis.panelKeys, categories:header.categories.config,
 before:before.choice, after:after.choice});
""", choice_binary)
    categories = actual["categories"]
    before = {categories[i]: value for i, value in enumerate(actual["before"])}
    after = {categories[code]: value for code, value in zip(actual["keys"], actual["after"], strict=True)}
    assert before["Choice"]["total"] == after["Choice"]["total"] == 2
    assert before["Never"]["total"] == after["Never"]["total"] == 1
    assert before["Never"]["neither"] == after["Never"]["neither"] == 1
    assert before["NoTargets"]["total"] == after["NoTargets"]["total"] == 1


def test_curtain_filters_pre_roi_denominator_without_reached_only_bias(choice_binary):
    actual = worker(CHOICE_INIT + """
state.ringEnabled=true;state.rings=[{x:0,z:0,r:.2}];
const analysis=applyCurtain(state,buildAnalysis(state));
({choice:buildRoi(state,analysis).choice[0], visible:analysis.visibleSegments.length});
""", choice_binary)
    assert actual["choice"]["total"] == 5
    assert actual["choice"]["neither"] == 2
    assert actual["visible"] == 3


def test_roi_expected_right_probability_default_override_and_validation(choice_binary):
    actual = worker(CHOICE_INIT + """
const analysis=buildAnalysis(state);
[undefined, 0, 1, .8, -1, ''].map(value=> {
 state.roiNullProbability=value; return buildRoi(state,analysis).choice[0];
});
""", choice_binary)
    assert [value["nullProbability"] for value in actual] == [.5, 0, 1, .8, .5, .5]
    assert [value["pValue"] for value in actual[:3]] == [1, 0, 0]
    assert actual[3]["pValue"] == pytest.approx(stats.binomtest(1, 2, .8).pvalue)


def test_polar_population_weights_samples_and_then_animals_equally():
    actual = worker("""
header={categories:{animal:['A','B']},ranges:{time:[0,1]}};
starts=Uint32Array.of(0,1);ends=Uint32Array.of(1,10);
activeOrientation=Float32Array.from([0,...Array(9).fill(90)]);
data={time:new Float32Array(10),speed:new Float32Array(10),segmentAnimal:Uint16Array.of(0,1),
 segmentFly:Uint16Array.of(0,1),segmentVr:Uint16Array.of(0,0),
 segmentTrial:Float32Array.of(0,0),segmentStep:Float32Array.of(0,0),segmentReplicate:Float32Array.of(1,1)};
const analysis={visibleSegments:[0,1],rowKeep:new Uint8Array(10).fill(1),
 segmentPanel:Int32Array.of(0,0),panelCount:1,panelNames:['All']};
const state={angleSource:'orientation',polarR:[0,1],statsUnit:'trial'};
const trials=buildPolar(state,analysis);state.statsUnit='animal';const animals=buildPolar(state,analysis);
({trialAngle:trials.populationAngle[0],trialR:trials.populationR[0],
 animalAngle:animals.populationAngle[0],animalR:animals.populationR[0]});
""")
    assert actual["trialAngle"] == pytest.approx(np.degrees(np.arctan2(9, 1)), abs=1e-5)
    assert actual["trialR"] == pytest.approx(np.hypot(1, 9) / 10)
    assert actual["animalAngle"] == pytest.approx(45)
    assert actual["animalR"] == pytest.approx(np.sqrt(.5))


def test_cancelled_polar_resultants_have_no_inferential_direction():
    actual = worker("""
const ns=20,n=40;
header={categories:{animal:['A']},ranges:{time:[0,1]}};
starts=Uint32Array.from({length:ns},(_,i)=>i*2);ends=Uint32Array.from(starts,value=>value+2);
activeOrientation=Float32Array.from({length:n},(_,i)=>i%2?180:0);
data={time:new Float32Array(n),speed:new Float32Array(n),segmentAnimal:new Uint16Array(ns),
 segmentFly:new Uint16Array(ns),segmentVr:new Uint16Array(ns),segmentTrial:new Float32Array(ns),
 segmentStep:new Float32Array(ns),segmentReplicate:new Float32Array(ns)};
const analysis={visibleSegments:Array.from({length:ns},(_,i)=>i),rowKeep:new Uint8Array(n).fill(1),
 segmentPanel:new Int32Array(ns),panelCount:1,panelNames:['All']};
const polar=buildPolar({polarR:[0,1],statsUnit:'trial'},analysis);
const metrics={panelCount:1,panelNames:['All'],panel:new Uint16Array(ns),
 distance:new Float32Array(ns),displacement:new Float32Array(ns),speed:new Float32Array(ns),tortuosity:new Float32Array(ns)};
const result=buildStatistics(metrics,polar);
({allUndefined:polar.angle.every(Number.isNaN),allZero:polar.r.every(r=>r===0),
 populationAngle:polar.populationAngle[0],populationR:polar.populationR[0],
 headingSamples:polar.headingDensity.reduce((sum,value)=>sum+value,0),rayleigh:result.rayleigh[0]});
""")
    assert actual["allUndefined"] is True
    assert actual["allZero"] is True
    assert actual["populationAngle"] is None
    assert actual["populationR"] == 0
    assert actual["headingSamples"] == 40
    assert actual["rayleigh"]["n"] == 0
    assert actual["rayleigh"]["p"] is None


def test_window_paths_never_bridge_rejected_samples():
    actual = worker("""
starts=Uint32Array.of(0);ends=Uint32Array.of(3);
activeX=Float32Array.of(0,100,2);activeZ=new Float32Array(3);
activeOrientation=new Float32Array(3);
const analysis={visibleSegments:[0],rowKeep:Uint8Array.of(1,0,1),
 segmentPanel:Int32Array.of(0),panelCount:1,panelNames:['All']};
buildWindows({windows:[{xmin:-1,xmax:3,zmin:-1,zmax:1},{xmin:10,xmax:20,zmin:-1,zmax:1}]},analysis);
""")
    assert actual["summaries"][0]["rows"] == 2
    assert actual["summaries"][0]["distance"] == 0
    assert actual["paired"][0]["distance"]["p"] == 1


def test_large_heading_segment_calculates_sequential_color_once():
    actual = worker("""
const n=50000;
starts=Uint32Array.of(0);ends=Uint32Array.of(n);
activeOrientation=new Float32Array(n).fill(10);
header={categories:{animal:['A']},ranges:{time:[0,50],speed:[0,4],distance:[0,2]}};
data={time:Float32Array.from({length:n},(_,i)=>i*.001),speed:new Float32Array(n).fill(2),
 segmentAnimal:Uint16Array.of(0),segmentTrial:Float32Array.of(0),segmentStep:Float32Array.of(0)};
const analysis={visibleSegments:[0],rowKeep:new Uint8Array(n).fill(1),
 segmentPanel:Int32Array.of(0),panelCount:1,panelNames:['All'],durationSummary:{p95:50,p99:50,max:50}};
const originalColor=unitColor;let colorCalls=0;
unitColor=(...args)=>{
 if(++colorCalls>1)throw new Error('Heading color must be computed once per segment');
 return originalColor(...args);
};
const start=performance.now(),result=buildHeading({colorBy:'speed'},analysis);
({colorCalls,links:result.vertices.length/4,color:result.colors[0],elapsedMs:performance.now()-start});
""")
    assert actual["colorCalls"] == 1
    assert actual["links"] == 49_999
    assert actual["color"] == 39
    assert actual["elapsedMs"] < 5_000


def test_heading_density_caps_allocation_for_an_extreme_duration():
    actual = worker("""
starts=Uint32Array.of(0);ends=Uint32Array.of(3);
activeOrientation=Float32Array.of(0,20,40);
header={categories:{animal:['A']}};
data={time:Float32Array.of(0,1,1e9)};
const analysis={visibleSegments:[0],rowKeep:Uint8Array.of(1,1,1),
 segmentPanel:Int32Array.of(0),panelCount:1,panelNames:['All'],durationSummary:{max:1e9}};
const result=buildHeading({headingMode:'density',headingBin:.05,playbackPercentile:'max'},analysis);
({bins:result.nTime,cells:result.density.length,timeBin:result.timeBin,
 requested:result.requestedTimeBin,finite:result.density.every(Number.isFinite)});
""")
    assert actual["bins"] == 2048
    assert actual["cells"] == 2048 * 36
    assert actual["timeBin"] > actual["requested"] == .05
    assert actual["finite"] is True


def test_report_scope_builds_all_export_products(choice_binary):
    actual = worker(CHOICE_INIT + """
compute({scope:'report', requestId:1, state});
const result=messages[messages.length-1];
({scope:result.scope, products:Object.keys(result.products),
 choice:result.products.roi.choice[0], statistics:result.products.statistics.method});
""", choice_binary)
    assert actual["scope"] == "report"
    assert set(actual["products"]) == {"trajectory", "heatmap", "direction", "polar", "heading",
                                        "metrics", "roi", "diagnostics", "filterHistograms",
                                        "statistics", "transition", "windows"}
    assert actual["choice"]["total"] == 5


def test_cached_products_survive_transfer_and_invalidate_for_settings_and_filters(choice_binary):
    actual = worker(CHOICE_INIT + """
const delivered=[];
postMessage=(message,transfer=[])=>delivered.push(structuredClone(message,{transfer}));
compute({scope:'statistics',requestId:1,state});
compute({scope:'statistics',requestId:2,state});
state.roiNullProbability=0;
compute({scope:'roi',requestId:3,state});
state.filters.config=[header.categories.config.indexOf('Choice')];
compute({scope:'statistics',requestId:4,state});
({counts:delivered.map(result=>result.products.roi.choice.reduce((sum,c)=>sum+c.total,0)),
 p:delivered[2].products.roi.choice[0].pValue,
 polarLengths:delivered.filter(result=>result.products.polar).map(result=>result.products.polar.angle.length),
 firstTrials:delivered[0].products.roi.animalTrials,
 secondTrials:delivered[1].products.roi.animalTrials,
 finalTrials:delivered[3].products.roi.animalTrials});
""", choice_binary)
    assert actual["counts"] == [5, 5, 5, 2]
    assert actual["p"] == 0
    assert actual["polarLengths"] == [3, 3, 2]
    assert actual["firstTrials"] == actual["secondTrials"] == [5]
    assert actual["finalTrials"] == [2]


def test_pooled_overview_reuses_polar_cache_without_evicting_normal_view(choice_binary):
    actual = worker(CHOICE_INIT + """
let calls=0;const original=buildPolar;
buildPolar=(...args)=>{calls++;return original(...args);};
compute({scope:'polar',requestId:1,state});
compute({scope:'overview',requestId:2,state});
const firstPool=lastPooledAnalysis;
compute({scope:'overview',requestId:3,state});
const samePool=firstPool===lastPooledAnalysis;
compute({scope:'polar',requestId:4,state});
({calls,samePool});
""", choice_binary)
    assert actual == {"calls": 2, "samePool": True}
